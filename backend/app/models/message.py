import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    channel: Mapped[str] = mapped_column(
        Enum("whatsapp", "email", name="message_channel"), nullable=False
    )
    direction: Mapped[str] = mapped_column(
        Enum("inbound", "outbound", name="message_direction"), nullable=False
    )
    sender_id: Mapped[str] = mapped_column(String(200), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assigned_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sent_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Who wrote it; NULL for messages from before ai_chatbot_demo events.
    author_kind: Mapped[str | None] = mapped_column(
        Enum("customer", "ai", "console", "agent", name="message_author_kind"), nullable=True
    )
    # Industry bot that wrote an `ai` message.
    bot_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Meta delivery status of messages sent through ai_chatbot_demo.
    delivery_status: Mapped[str | None] = mapped_column(
        Enum("sent", "delivered", "read", "failed", name="message_delivery_status"), nullable=True
    )
    delivery_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_msg_contact_id", "contact_id"),
        Index("idx_msg_channel", "channel"),
        Index("idx_msg_sender_id", "sender_id"),
        Index("idx_msg_created_at", "created_at"),
    )
