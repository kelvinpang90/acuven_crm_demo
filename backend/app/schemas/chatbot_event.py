"""Request body of POST /internal/chatbot/events (docs/chatbot-events.md
section 4 and 9.5). Anything that fails here is a 422: the chatbot dead-letters
it instead of retrying.
"""

from typing import Literal, Optional, Union

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.utils.phone import normalize_phone

EventType = Literal[
    "customer_message",
    "ai_reply",
    "console_reply",
    "agent_reply",
    "handover_changed",
    "delivery_status",
]
MESSAGE_EVENTS = ("customer_message", "ai_reply", "console_reply")


def _iso_string_only(value):
    """Times come as ISO 8601 strings; a bare number would otherwise be read
    as a Unix time."""
    if value is not None and not isinstance(value, str):
        raise ValueError("must be an ISO 8601 date-time string")
    return value


class EventVisitor(BaseModel):
    phone: Optional[StrictStr] = None
    bsuid: Optional[StrictStr] = Field(None, max_length=128)
    profile_name: Optional[StrictStr] = None

    @model_validator(mode="after")
    def phone_or_bsuid(self) -> "EventVisitor":
        if not self.phone and not self.bsuid:
            raise ValueError("visitor needs a phone or a bsuid")
        if self.phone and normalize_phone(self.phone) is None:
            raise ValueError("visitor.phone cannot be normalized")
        return self


class EventMessage(BaseModel):
    wa_message_id: StrictStr = Field(min_length=1, max_length=200)
    body: StrictStr


class EventHandover(BaseModel):
    active: StrictBool
    # Required key, null allowed: null exactly when the takeover has ended (9.5).
    expires_at: Optional[AwareDatetime]

    @field_validator("expires_at", mode="before")
    @classmethod
    def expires_at_is_string(cls, value):
        return _iso_string_only(value)

    @model_validator(mode="after")
    def expiry_matches_active(self) -> "EventHandover":
        if self.active and self.expires_at is None:
            raise ValueError("handover.expires_at is required while active")
        if not self.active and self.expires_at is not None:
            raise ValueError("handover.expires_at must be null when not active")
        return self


class EventStatus(BaseModel):
    wa_message_id: StrictStr = Field(min_length=1, max_length=200)
    status: Literal["sent", "delivered", "read", "failed"]
    error_code: Optional[Union[StrictInt, StrictStr]] = None
    error_title: Optional[StrictStr] = None


class ChatbotEvent(BaseModel):
    # Only logged, never stored.
    event_id: Optional[StrictStr] = None
    type: EventType
    occurred_at: AwareDatetime
    visitor: EventVisitor
    message: Optional[EventMessage] = None
    bot_id: Optional[StrictStr] = Field(None, max_length=50)
    handover: Optional[EventHandover] = None
    status: Optional[EventStatus] = None
    crm_message_id: Optional[StrictStr] = Field(None, min_length=1, max_length=36)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def occurred_at_is_string(cls, value):
        return _iso_string_only(value)

    @model_validator(mode="after")
    def type_fields_present(self) -> "ChatbotEvent":
        if (self.type in MESSAGE_EVENTS or self.type == "agent_reply") and self.message is None:
            raise ValueError(f"{self.type} needs message")
        if self.type == "agent_reply" and not self.crm_message_id:
            raise ValueError("agent_reply needs crm_message_id")
        if self.type == "handover_changed" and self.handover is None:
            raise ValueError("handover_changed needs handover")
        if self.type == "delivery_status" and self.status is None:
            raise ValueError("delivery_status needs status")
        return self

    def service_kwargs(self) -> dict:
        """Plain values for chatbot_event_service.process_event."""
        kwargs: dict = {
            "event_type": self.type,
            "occurred_at": self.occurred_at,
            "phone": self.visitor.phone,
            "bsuid": self.visitor.bsuid,
            "profile_name": self.visitor.profile_name,
        }
        if self.type in MESSAGE_EVENTS:
            kwargs.update(
                wa_message_id=self.message.wa_message_id,
                body=self.message.body,
                bot_id=self.bot_id if self.type == "ai_reply" else None,
            )
        elif self.type == "agent_reply":
            kwargs.update(
                crm_message_id=self.crm_message_id,
                wa_message_id=self.message.wa_message_id,
                body=self.message.body,
            )
        elif self.type == "handover_changed":
            kwargs.update(
                handover_active=self.handover.active,
                handover_expires_at=self.handover.expires_at,
            )
        else:
            code = self.status.error_code
            kwargs.update(
                wa_message_id=self.status.wa_message_id,
                status=self.status.status,
                error_code=None if code is None else str(code),
                error_title=self.status.error_title,
            )
        return kwargs
