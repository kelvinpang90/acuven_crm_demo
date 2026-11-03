import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Meta's limits on body parameters (docs/chatbot-phase3.md section 5.3 item 6).
TEMPLATE_PARAM_FORBIDDEN = re.compile(r"[\r\n\t]| {4,}")


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
    # Set for template messages only (docs/chatbot-phase3.md section 15 item 3).
    template_name: Optional[str] = None

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


class WhatsAppTemplateSendRequest(BaseModel):
    contact_id: str
    template_name: str
    language: str = Field(min_length=1, max_length=20)
    params: list[str] = Field(max_length=10)

    @field_validator("template_name")
    @classmethod
    def template_name_length(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("template_name must contain 1 to 100 non-whitespace characters")
        return value

    @field_validator("params")
    @classmethod
    def params_characters(cls, value: list[str]) -> list[str]:
        for param in value:
            stripped = param.strip()
            if not 1 <= len(stripped) <= 1024:
                raise ValueError("each param must contain 1 to 1024 non-whitespace characters")
            if TEMPLATE_PARAM_FORBIDDEN.search(stripped):
                raise ValueError(
                    "params may not contain newlines, tabs or more than 3 consecutive spaces"
                )
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
