from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

# ai_chatbot_demo industry bots, by their bot id.
DealSource = Literal["banking", "food", "hotel", "realestate", "retail", "saas"]


class DealCreate(BaseModel):
    contact_id: str
    title: Optional[str] = None
    status: str = "lead"
    priority: str = "mid"
    amount: Decimal = Decimal("0.00")
    assigned_to: Optional[str] = None
    source: Optional[DealSource] = None

    @field_validator("amount")
    @classmethod
    def amount_non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Amount must be non-negative")
        return v


class DealUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    amount: Optional[Decimal] = None
    assigned_to: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_non_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("Amount must be non-negative")
        return v


class DealResponse(BaseModel):
    id: str
    contact_id: str
    contact_name: Optional[str] = None
    contact_company: Optional[str] = None
    title: Optional[str] = None
    status: str
    priority: str
    amount: Decimal
    source: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    won_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
