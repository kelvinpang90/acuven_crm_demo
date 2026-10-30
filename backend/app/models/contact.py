import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_contact: Mapped[date | None] = mapped_column(Date, nullable=True)
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_archived: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # Manual mapping to an AutoCount Cloud Accounting customer (Debtor.accNo).
    # No fuzzy matching: invoice/quotation sync only links documents whose
    # debtorCode equals this value.
    autocount_customer_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # True for contacts that came in through the shared WhatsApp demo gateway
    # (whatsapp_gateway) rather than this project's own WhatsApp number.
    is_gateway: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # WhatsApp business-scoped user id from ai_chatbot_demo events: the channel
    # identity of visitors who don't share their number (docs/chatbot-events.md).
    whatsapp_bsuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Human takeover of the AI conversation. Times are naive UTC; updated_at is
    # the occurred_at of the event the current state is based on.
    wa_handover_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    wa_handover_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wa_handover_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Conversation claim: who is replying right now and until when (naive UTC).
    # A claim is valid only while reply_claimed_until is in the future.
    reply_claimed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reply_claimed_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("idx_industry", "industry"),
        Index("idx_assigned_to", "assigned_to"),
        Index("idx_deleted_at", "deleted_at"),
        Index("idx_is_archived", "is_archived"),
        Index("idx_autocount_customer_code", "autocount_customer_code"),
        Index("idx_contacts_is_gateway", "is_gateway"),
        Index("idx_contacts_whatsapp_bsuid", "whatsapp_bsuid", unique=True),
        Index("idx_contacts_phone", "phone"),
    )
