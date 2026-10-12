"""Sync orchestration: AutoCount Cloud Accounting -> CRM.

Customers are synced first, directly into `contacts` (upserted by
`autocount_customer_code` = AutoCount Debtor.accNo). CRM contacts are not
created manually in this workflow, so there's no dedup/fuzzy-matching
concern — accNo is a stable, unique-per-account-book key we own end to end.
Invoice/quotation sync runs after, so matching sees the freshest contact
data from the same run. Both the manual "Sync Now" button and the 60s
background poller call `sync_all()` — one code path, two triggers.

Full sync every run for both customers and documents (see conversation
notes in tasks/todo.md): the debtor/listing endpoint has no incremental
filter at all, and invoice/quotation listing's `lastModifiedDate` filter
is deliberately not used yet — noted there as a future upgrade.
"""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.autocount_document import AutocountDocument
from app.models.contact import Contact
from app.services import autocount_client
from app.utils.phone import phone_for_storage

logger = logging.getLogger(__name__)


async def list_contact_documents(db: AsyncSession, contact_id: str) -> list[dict]:
    result = await db.execute(
        select(AutocountDocument)
        .where(AutocountDocument.contact_id == contact_id)
        .order_by(AutocountDocument.doc_date.desc())
    )
    return [_document_to_dict(d) for d in result.scalars().all()]


async def sync_all(db: AsyncSession) -> dict:
    customers_synced = await _sync_customers(db)
    invoices_synced, invoices_unmatched = await _sync_documents(db, "invoice")
    quotations_synced, quotations_unmatched = await _sync_documents(db, "quotation")
    await db.commit()
    return {
        "customers_synced": customers_synced,
        "invoices_synced": invoices_synced,
        "invoices_unmatched": invoices_unmatched,
        "quotations_synced": quotations_synced,
        "quotations_unmatched": quotations_unmatched,
        "synced_at": datetime.utcnow().isoformat(),
    }


async def _sync_customers(db: AsyncSession) -> int:
    # activeOnly=True: a customer AutoCount marks inactive is dropped from
    # the pull entirely — it won't create or update a Contact.
    debtors = await autocount_client.get_all_debtors(active_only=True)

    for d in debtors:
        # debtor/listing returns PascalCase keys — see autocount_client._DEBTOR_FIELDS.
        customer_code = d["AccNo"]
        company_name = d.get("CompanyName", "") or ""
        attention = d.get("Attention") or ""
        # Contact.name is required; AutoCount's Attention (contact person) is
        # usually blank on trial data, so fall back to the company name.
        name = attention or company_name or customer_code

        existing = await db.execute(
            select(Contact).where(Contact.autocount_customer_code == customer_code)
        )
        contact = existing.scalar_one_or_none()
        if contact is None:
            contact = Contact(id=str(uuid.uuid4()), autocount_customer_code=customer_code)
            db.add(contact)

        contact.name = name
        contact.company = company_name or None
        contact.industry = d.get("NatureOfBusiness") or None
        contact.email = d.get("EmailAddress") or None
        # Normalized like every other save path; otherwise each 60s run would
        # write AutoCount's raw form back over the normalized number.
        contact.phone = phone_for_storage(d.get("Phone1") or None)
        contact.address = d.get("Address") or None
        # `updated_at` is deliberately not assigned. The column carries
        # `onupdate`, so it moves by itself when one of the fields above actually
        # changed -- and stays put when the pull brought back exactly what we
        # already had. Assigning it here made every row dirty on every run, and
        # the poller runs every 60s: 1440 full rewrites of the table a day with
        # nothing to show for them.
        #
        # rs-roof-pms ran the same code against its own MySQL and filled a 29G
        # disk with 13G of binlog on 2026-08-30 -- see that repo's
        # docs/binlog-write-loop-investigation.md. This database is small enough
        # that it only cost ~9M of binlog a day on the shared server, which is
        # luck about data volume, not a difference in the code.

    await db.flush()
    return len(debtors)


async def _sync_documents(db: AsyncSession, doc_type: str) -> tuple[int, int]:
    if doc_type == "invoice":
        raw_docs = await autocount_client.get_all_invoices()
    else:
        raw_docs = await autocount_client.get_all_quotations()

    unmatched = 0
    for raw in raw_docs:
        master = raw.get("master") or {}
        customer_code = master.get("debtorCode", "")
        contact_id = await _find_contact_id(db, customer_code)
        if contact_id is None:
            unmatched += 1

        existing = await db.execute(
            select(AutocountDocument).where(
                AutocountDocument.doc_type == doc_type,
                AutocountDocument.doc_no == master.get("docNo", ""),
            )
        )
        doc = existing.scalar_one_or_none()
        if doc is None:
            doc = AutocountDocument(doc_type=doc_type, doc_no=master.get("docNo", ""))
            db.add(doc)

        doc.customer_code = customer_code
        doc.contact_id = contact_id
        doc.status = master.get("status", "") or ""
        doc.total_amount = Decimal(str(master.get("finalTotal", 0) or 0))
        doc.currency_code = master.get("currencyCode", "") or ""
        doc.doc_date = _parse_date(master.get("docDate"))
        if doc_type == "invoice":
            outstanding = master.get("outstandingAmount")
            doc.outstanding_amount = Decimal(str(outstanding)) if outstanding is not None else None
            doc.due_date = _parse_date(master.get("dueDate"))
        else:
            doc.validity = master.get("validity") or None
        doc.line_items = _build_line_items(raw.get("details") or [])
        # Not assigned, for the same reason as `contact.updated_at` above: the
        # column carries `onupdate`. Stamping it made every row dirty every run,
        # and documents are the expensive ones -- `line_items` is JSON, and a
        # ROW-format binlog writes the whole row twice (before and after image)
        # for an UPDATE that changed nothing but a timestamp.

    await db.flush()
    return len(raw_docs), unmatched


async def _find_contact_id(db: AsyncSession, customer_code: str) -> Optional[str]:
    if not customer_code:
        return None
    result = await db.execute(
        select(Contact.id).where(Contact.autocount_customer_code == customer_code)
    )
    return result.scalar_one_or_none()


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return datetime.fromisoformat(value).date()


def _build_line_items(details: list[dict]) -> list[dict]:
    return [
        {
            "product_code": d.get("productCode"),
            "description": d.get("description"),
            "qty": d.get("qty"),
            "unit": d.get("unit"),
            "unit_price": d.get("unitPrice"),
            "discount_amt": d.get("discountAmt"),
            "sub_total": d.get("subTotal"),
        }
        for d in details
    ]


def _document_to_dict(d: AutocountDocument) -> dict:
    return {
        "id": d.id,
        "doc_type": d.doc_type,
        "doc_no": d.doc_no,
        "customer_code": d.customer_code,
        "contact_id": d.contact_id,
        "status": d.status,
        "total_amount": d.total_amount,
        "currency_code": d.currency_code,
        "doc_date": d.doc_date.isoformat() if d.doc_date else None,
        "outstanding_amount": d.outstanding_amount,
        "due_date": d.due_date.isoformat() if d.due_date else None,
        "validity": d.validity,
        "line_items": d.line_items or [],
        "synced_at": d.synced_at.isoformat() if d.synced_at else None,
    }
