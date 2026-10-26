from datetime import datetime

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConversationTriage(Base):
    __tablename__ = "conversation_triage"

    contact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intent: Mapped[str] = mapped_column(
        Enum(
            "inquiry",
            "quotation",
            "order",
            "complaint",
            "after_sales",
            "appointment",
            "other",
            name="conversation_triage_intent",
            validate_strings=True,
        ),
        nullable=False,
    )
    urgency: Mapped[str] = mapped_column(
        Enum(
            "low",
            "normal",
            "high",
            "urgent",
            name="conversation_triage_urgency",
            validate_strings=True,
        ),
        nullable=False,
    )
    message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    triaged_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
