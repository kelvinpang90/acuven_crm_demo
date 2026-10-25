from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, field_validator

from app.schemas.deal import DealSource


class ContactCreate(BaseModel):
    name: str
    company: Optional[str] = None
    industry: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    tags: Optional[List[str]] = None
    autocount_customer_code: Optional[str] = None
    # Initial deal fields (used to populate the auto-created Deal)
    initial_status: str = "lead"
    initial_priority: str = "mid"
    initial_amount: float = 0.0
    initial_title: Optional[str] = None
    initial_source: Optional[DealSource] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name is required")
        return v.strip()


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    tags: Optional[List[str]] = None
    is_archived: Optional[int] = None
    autocount_customer_code: Optional[str] = None

    @field_validator("is_archived")
    @classmethod
    def archived_must_be_binary(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in (0, 1):
            raise ValueError("is_archived must be 0 or 1")
        return v


class ContactResponse(BaseModel):
    id: str
    name: str
    company: Optional[str] = None
    industry: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    last_contact: Optional[date] = None
    tags: Optional[List[str]] = None
    is_archived: int = 0
    autocount_customer_code: Optional[str] = None
    total_deal_amount: float = 0.0
    deal_count: int = 0
    # Distinct sources of the contact's live deals, sorted.
    sources: List[str] = []
    # False once expires_at has passed, whatever is stored (docs/chatbot-events.md section 5).
    wa_handover_active: bool = False
    wa_handover_expires_at: Optional[datetime] = None
    wa_handover_updated_at: Optional[datetime] = None
    wa_reply_window_until: Optional[datetime] = None
    # List and detail only; computed for admins, always False for other roles.
    phone_conflict: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ArchiveRequest(BaseModel):
    is_archived: int

    @field_validator("is_archived")
    @classmethod
    def must_be_binary(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("is_archived must be 0 or 1")
        return v


class PaginatedContacts(BaseModel):
    data: List[ContactResponse]
    total: int
    page: int
    page_size: int


class ImportError_(BaseModel):
    row: int
    field: str
    message: str


class ImportResult(BaseModel):
    # What contact_service.import_contacts returns; deals_added counts rows that
    # added a deal to an existing customer.
    total: int
    inserted: int
    deals_added: int
    skipped: int
    errors: List[ImportError_]
