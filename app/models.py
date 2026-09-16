"""ORM models.

Two tables:
  patients    — the demographic record described in the brief
  call_logs   — one row per phone call, with the full transcript, optionally
                linked to the patient that was created or updated
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")


def _uuid4() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"

    # --- Identity ---------------------------------------------------------
    patient_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid4
    )

    # --- Required demographics -------------------------------------------
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # Stored ISO (YYYY-MM-DD) so it sorts and compares correctly in SQL.
    # The API and the voice agent speak MM/DD/YYYY to humans.
    date_of_birth: Mapped[str] = mapped_column(String(10), nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    # Normalised to 10 digits, no punctuation, so lookups are exact.
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)

    # --- Optional ---------------------------------------------------------
    email: Mapped[str | None] = mapped_column(String(254))
    address_line_2: Mapped[str | None] = mapped_column(String(100))
    insurance_provider: Mapped[str | None] = mapped_column(String(120))
    insurance_member_id: Mapped[str | None] = mapped_column(String(50))
    preferred_language: Mapped[str] = mapped_column(String(50), default="English")
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))

    # --- Bookkeeping ------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    # Soft delete: DELETE /patients/:id sets this instead of removing the row.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "voice" | "api" | "seed"
    source: Mapped[str] = mapped_column(String(20), default="api", nullable=False)

    call_logs: Mapped[list["CallLog"]] = relationship(back_populates="patient")

    __table_args__ = (
        CheckConstraint(
            "sex IN ('Male','Female','Other','Decline to Answer')",
            name="ck_patients_sex",
        ),
        CheckConstraint("length(phone_number) = 10", name="ck_patients_phone_len"),
        CheckConstraint("length(state) = 2", name="ck_patients_state_len"),
        Index("ix_patients_last_name_lower", "last_name"),
        Index("ix_patients_dob", "date_of_birth"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Patient {self.patient_id} {self.first_name} {self.last_name}>"


class CallLog(Base):
    """Transcript + outcome for a single inbound call.

    Written incrementally during the call so a dropped connection still
    leaves behind everything that was said.
    """

    __tablename__ = "call_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid4)
    # Twilio CallSid / Vapi call id — the key the webhook uses to resume state.
    call_sid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    from_number: Mapped[str | None] = mapped_column(String(20))
    transcript: Mapped[str] = mapped_column(Text, default="")
    # JSON blob of the fields collected so far, so a call can be resumed and
    # so we have an audit trail of what the LLM extracted.
    collected_json: Mapped[str] = mapped_column(Text, default="{}")
    outcome: Mapped[str] = mapped_column(String(30), default="in_progress")
    patient_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("patients.patient_id")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    patient: Mapped["Patient | None"] = relationship(back_populates="call_logs")
