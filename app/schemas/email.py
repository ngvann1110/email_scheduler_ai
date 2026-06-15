from datetime import datetime
from typing import Optional

from pydantic import BaseModel, StrictStr, EmailStr, field_validator


class EmailSchema(BaseModel):
    sender: EmailStr
    subject: StrictStr
    body: StrictStr
    timestamp: StrictStr
    gmail_message_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def validate_iso_timestamp(cls, v: str) -> str:
        """Accept only ISO 8601 timestamps."""
        try:
            # This accepts both "2026-06-06T10:00:00" and "2026-06-06T10:00:00+07:00"
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError("Timestamp must be ISO 8601 format")
        return v


class EmailInsightSchema(BaseModel):
    """
    AI-generated understanding of a single incoming email.

    Returned by GET /api/v1/insights and used internally to store
    structured analysis results.
    """

    id: int
    gmail_message_id: Optional[str] = None
    sender: str
    subject: Optional[str] = None
    summary: Optional[str] = None
    category: str
    priority: Optional[str] = None
    action_required: bool = False
    important_note: Optional[str] = None
    is_read: bool = False
    created_at: Optional[str] = None  # ISO-8601 timestamp from SQLite

    model_config = {"from_attributes": True}
