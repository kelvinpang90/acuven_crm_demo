import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AiRequest(Base):
    __tablename__ = "ai_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(
        Enum(
            "ok",
            "unavailable",
            "error",
            name="ai_request_outcome",
            validate_strings=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_ai_requests_contact_id", "contact_id"),
        Index("idx_ai_requests_created_at", "created_at"),
    )
