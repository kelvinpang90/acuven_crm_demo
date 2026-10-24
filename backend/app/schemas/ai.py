from typing import Literal

from pydantic import BaseModel, Field, field_validator

Language = Literal["zh-CN", "en", "ms"]
Channel = Literal["whatsapp", "email"]


class ConversationBriefRequest(BaseModel):
    output_language: Language | None = None


class ContactSummaryRequest(BaseModel):
    output_language: Language | None = None


class ContactExtractRequest(BaseModel):
    output_language: Language | None = None


class DealNextStepRequest(BaseModel):
    output_language: Language | None = None


class DashboardInsightRequest(BaseModel):
    output_language: Language | None = None


class FollowupDraftRequest(BaseModel):
    deal_id: str = Field(min_length=1)
    note: str
    today: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    output_language: Language | None = None

    @field_validator("note")
    @classmethod
    def note_length(cls, value: str) -> str:
        if not 1 <= len(value.strip()) <= 2000:
            raise ValueError("note must contain 1 to 2000 non-whitespace characters")
        return value


class ReplyDraftRequest(BaseModel):
    channel: Channel
    instruction: str | None = None
    output_language: Language | None = None

    @field_validator("instruction")
    @classmethod
    def instruction_length(cls, value: str | None) -> str | None:
        if value is not None and not 1 <= len(value.strip()) <= 200:
            raise ValueError("instruction must contain 1 to 200 non-whitespace characters")
        return value


class TranslateRequest(BaseModel):
    contact_id: str
    text: str
    target_language: Language

    @field_validator("text")
    @classmethod
    def text_length(cls, value: str) -> str:
        if not 1 <= len(value.strip()) <= 2000:
            raise ValueError("text must contain 1 to 2000 non-whitespace characters")
        return value
