"""Pydantic schemas.

Input validation intentionally delegates to `app.validators` so that the REST
API and the voice agent apply exactly the same rules — the brief asks for
server-side validation that does not trust the agent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.validators import (
    dob_to_us,
    phone_to_display,
    validate_patient_payload,
)

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Consistent response shape: {"data": ..., "error": ...}."""

    data: T | None = None
    error: Any | None = None


class PatientCreate(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    first_name: str
    last_name: str
    date_of_birth: str = Field(description="MM/DD/YYYY or YYYY-MM-DD")
    sex: str
    phone_number: str
    address_line_1: str
    city: str
    state: str
    zip_code: str

    email: str | None = None
    address_line_2: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = "English"
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    _partial: bool = False

    @model_validator(mode="after")
    def _run_domain_validators(self):
        payload = {k: v for k, v in self.model_dump().items() if v is not None}
        cleaned, errors = validate_patient_payload(payload, partial=False)
        if errors:
            raise ValueError(errors)
        for key, value in cleaned.items():
            object.__setattr__(self, key, value)
        return self


class PatientUpdate(BaseModel):
    """Partial update — every field optional."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: str | None = None
    sex: str | None = None
    phone_number: str | None = None
    email: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None

    @model_validator(mode="after")
    def _run_domain_validators(self):
        payload = {k: v for k, v in self.model_dump().items() if v is not None}
        if not payload:
            raise ValueError({"body": "Provide at least one field to update."})
        cleaned, errors = validate_patient_payload(payload, partial=True)
        if errors:
            raise ValueError(errors)
        for key, value in cleaned.items():
            object.__setattr__(self, key, value)
        return self

    def changes(self) -> dict:
        return {
            k: v
            for k, v in self.model_dump().items()
            if v is not None and v != ""
        }


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: str
    sex: str
    phone_number: str
    email: str | None = None
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    source: str

    @classmethod
    def from_model(cls, patient) -> "PatientOut":
        """Present human-facing formats: MM/DD/YYYY dates, (xxx) xxx-xxxx phones."""
        obj = cls.model_validate(patient)
        obj.date_of_birth = dob_to_us(obj.date_of_birth)
        obj.phone_number = phone_to_display(obj.phone_number)
        obj.emergency_contact_phone = phone_to_display(obj.emergency_contact_phone)
        return obj


class CallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    call_sid: str
    from_number: str | None
    outcome: str
    patient_id: str | None
    transcript: str
    started_at: datetime
