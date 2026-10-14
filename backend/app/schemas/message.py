from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class MessageResponse(BaseModel):
    id: str
    contact_id: Optional[str] = None
    channel: str
    direction: str
    sender_id: str
    recipient_id: str
    subject: Optional[str] = None
    body: str
    external_id: Optional[str] = None
    is_read: bool
    assigned_to: Optional[str] = None
    # Filled in for messages that came through ai_chatbot_demo events.
    author_kind: Optional[str] = None
    bot_id: Optional[str] = None
    delivery_status: Optional[str] = None
    delivery_error: Optional[str] = None
    created_at: datetime
    contact_name: Optional[str] = None
    sent_by: Optional[str] = None
    sent_by_name: Optional[str] = None

    model_config = {"from_attributes": True}


class WhatsAppSendRequest(BaseModel):
    contact_id: str
    message: str

    @field_validator("message")
    @classmethod
    def message_length(cls, value: str) -> str:
        if not 1 <= len(value.strip()) <= 4096:
            raise ValueError("message must contain 1 to 4096 non-whitespace characters")
        return value


class EmailSendRequest(BaseModel):
    contact_id: str
    subject: str
    body: str


class PaginatedMessages(BaseModel):
    data: list[MessageResponse]
    total: int
    page: int
    page_size: int
