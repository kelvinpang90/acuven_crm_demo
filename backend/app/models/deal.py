import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("lead", "following", "negotiating", "won", "lost", name="deal_status"),
        nullable=False,
        default="lead",
    )
    priority: Mapped[str] = mapped_column(
        Enum("high", "mid", "low", name="deal_priority"),
        nullable=False,
        default="mid",
    )
    amount: Mapped[Decimal] = mapped_column(DECIMAL(15, 2), nullable=False, default=Decimal("0.00"))
    # The ai_chatbot_demo industry bot the enquiry came in through (DEAL_SOURCES);
    # NULL for deals created any other way.
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    won_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("idx_deal_contact_id", "contact_id"),
        Index("idx_deal_status", "status"),
        Index("idx_deal_assigned_to", "assigned_to"),
        Index("idx_deal_won_at", "won_at"),
        Index("idx_deal_deleted_at", "deleted_at"),
    )
