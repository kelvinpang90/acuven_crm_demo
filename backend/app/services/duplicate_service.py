"""Possible duplicate customers (docs/contact-duplicate-hints.md sections 3, 4, 6.1 and 9).

Rules only, no AI: the same phone, the same email, or the same name at the same
company. Nothing is merged or stored (B-05); the result only says which other
customers look like the same person.

Matches the user may not open are counted, never described, so a duplicate
check cannot be used to read someone else's customer. Reach is the one
`access_service` applies to a single contact, judged for all matches in one
query.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.user import User
from app.services.access_service import owner_scope
from app.utils.phone import normalize_phone

RULE_ORDER = ("phone", "email", "name_company")

# Longest first, so `sdn bhd` goes as a whole before `bhd` could leave `sdn`.
_COMPANY_SUFFIX_WORDS = tuple(
    tuple(suffix.split())
    for suffix in (
        "sdn bhd",
        "bhd",
        "berhad",
        "plt",
        "enterprise",
        "trading",
        "ltd",
        "limited",
        "co",
        "inc",
    )
)
# Chinese is written without spaces, so these go as a trailing substring.
_COMPANY_SUFFIX_CJK = ("有限公司", "公司")

# A chatbot visitor's name is often just their number (section 3).
_NUMBER_NAME = re.compile(r"[0-9+\- ]+")


@dataclass
class DuplicateContact:
    id: str
    name: str
    company: Optional[str]
    assigned_to_name: Optional[str]
    rules: list[str]


@dataclass
class DuplicateResult:
    visible: list[DuplicateContact] = field(default_factory=list)
    hidden_count: int = 0


def _is_punctuation(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def normalize_name(value: Optional[str]) -> Optional[str]:
    """Lowercase with all punctuation and whitespace removed; None when nothing is left."""
    if value is None:
        return None
    text = "".join(ch for ch in value.lower() if not _is_punctuation(ch) and not ch.isspace())
    return text or None


def _strip_company_suffixes(text: str) -> str:
    """Drop the known suffixes from the end of a lowercased, single-spaced name,
    again and again, so `co ltd` goes as well as `ltd`. English suffixes go only
    as whole words: `zinc` keeps its `inc` and `mexico` its `co`."""
    while text:
        words = text.split(" ")
        for suffix in _COMPANY_SUFFIX_WORDS:
            if len(words) >= len(suffix) and tuple(words[-len(suffix) :]) == suffix:
                text = " ".join(words[: -len(suffix)])
                break
        else:
            for suffix in _COMPANY_SUFFIX_CJK:
                if text.endswith(suffix):
                    text = text[: -len(suffix)].rstrip()
                    break
            else:
                return text
    return text


def normalize_company(value: Optional[str]) -> Optional[str]:
    """Section 9.4's order: lowercase, punctuation to spaces, collapse whitespace,
    strip suffixes, then remove all whitespace; None when nothing is left."""
    if value is None:
        return None
    text = "".join(" " if _is_punctuation(ch) else ch for ch in value.lower())
    text = _strip_company_suffixes(" ".join(text.split()))
    text = "".join(text.split())
    return text or None


def _is_number_name(value: Optional[str]) -> bool:
    return value is not None and _NUMBER_NAME.fullmatch(value.strip()) is not None


async def find_duplicates(
    db: AsyncSession,
    *,
    user: User,
    name: Optional[str],
    company: Optional[str],
    email: Optional[str],
    phone: Optional[str],
    exclude_id: Optional[str] = None,
    limit: int = 20,
) -> DuplicateResult:
    """Other non-deleted customers (archived included) that look like the given
    details, split into the ones `user` may open and a count of the rest.

    A fixed number of queries whatever the data: one per rule that applies, one
    for the manager's team, one for the matches the user may open.
    """
    others = [Contact.deleted_at.is_(None)]
    if exclude_id is not None:
        others.append(Contact.id != exclude_id)

    matched: dict[str, set[str]] = {}

    wanted_phone = normalize_phone(phone)
    if wanted_phone is not None:
        # Stored numbers are already normalized (CRM-TASK-009), as in
        # contact_service.find_phone_conflicts.
        rows = await db.execute(select(Contact.id).where(*others, Contact.phone == wanted_phone))
        for contact_id in rows.scalars():
            matched.setdefault(contact_id, set()).add("phone")

    wanted_email = (email or "").strip().lower()
    if wanted_email:
        rows = await db.execute(
            select(Contact.id).where(*others, func.lower(func.trim(Contact.email)) == wanted_email)
        )
        for contact_id in rows.scalars():
            matched.setdefault(contact_id, set()).add("email")

    wanted_name = None if _is_number_name(name) else normalize_name(name)
    wanted_company = normalize_company(company)
    if wanted_name is not None and wanted_company is not None:
        # Every candidate is compared in Python: a prefix filter in SQL would miss
        # `T.A.N. Hardware` against `Tan Hardware` (section 9.2). Fine at demo
        # scale; to be revisited before real customers come in.
        rows = await db.execute(
            select(Contact.id, Contact.name, Contact.company).where(
                *others,
                Contact.name.is_not(None),
                Contact.name != "",
                Contact.company.is_not(None),
                Contact.company != "",
            )
        )
        for contact_id, other_name, other_company in rows.all():
            if _is_number_name(other_name):
                continue
            if (
                normalize_name(other_name) == wanted_name
                and normalize_company(other_company) == wanted_company
            ):
                matched.setdefault(contact_id, set()).add("name_company")

    if not matched:
        return DuplicateResult()

    rows = await db.execute(
        select(Contact.id, Contact.name, Contact.company, Contact.created_at, User.name)
        .outerjoin(User, User.id == Contact.assigned_to)
        .where(
            Contact.id.in_(list(matched)),
            Contact.deleted_at.is_(None),
            *await owner_scope(db, user, Contact.assigned_to),
        )
    )
    reachable = []
    for contact_id, contact_name, contact_company, created_at, owner_name in rows.all():
        rules = [rule for rule in RULE_ORDER if rule in matched[contact_id]]
        reachable.append(
            (
                created_at,
                DuplicateContact(
                    id=contact_id,
                    name=contact_name,
                    company=contact_company,
                    assigned_to_name=owner_name,
                    rules=rules,
                ),
            )
        )
    reachable.sort(key=lambda item: (-len(item[1].rules), item[0], item[1].id))

    return DuplicateResult(
        visible=[contact for _, contact in reachable[:limit]],
        hidden_count=len(matched) - len(reachable),
    )
