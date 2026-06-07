from datetime import datetime

from pydantic import BaseModel, StrictStr, EmailStr, field_validator


class EmailSchema(BaseModel):
    sender: EmailStr
    subject: StrictStr
    body: StrictStr
    timestamp: StrictStr

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
