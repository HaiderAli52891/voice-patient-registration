"""Service layer.

Both the REST API and the voice agent go through these functions, so the two
paths can never drift apart in how they validate or write data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CallLog, Patient
from app.validators import normalize_phone, validate_dob

log = logging.getLogger(__name__)


# --- Patients ------------------------------------------------------------

def list_patients(
    db: Session,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Patient]:
    stmt = select(Patient)

    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    if last_name:
        stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
    if date_of_birth:
        ok, iso = validate_dob(date_of_birth)
        # An unparsable date should return nothing rather than everything.
        stmt = stmt.where(Patient.date_of_birth == (iso if ok else "__invalid__"))
    if phone_number:
        stmt = stmt.where(Patient.phone_number == normalize_phone(phone_number))

    stmt = stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_patient(
    db: Session, patient_id: str, include_deleted: bool = False
) -> Patient | None:
    patient = db.get(Patient, patient_id)
    if patient is None:
        return None
    if patient.deleted_at is not None and not include_deleted:
        return None
    return patient


def find_by_phone(db: Session, phone: str) -> Patient | None:
    """Used for duplicate detection on inbound calls."""
    digits = normalize_phone(phone)
    if len(digits) != 10:
        return None
    stmt = (
        select(Patient)
        .where(Patient.phone_number == digits, Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
    )
    return db.execute(stmt).scalars().first()


def create_patient(db: Session, data: dict, source: str = "api") -> Patient:
    patient = Patient(**data, source=source)
    db.add(patient)
    db.commit()
    db.refresh(patient)
    log.info(
        "patient.created id=%s name=%s %s source=%s",
        patient.patient_id, patient.first_name, patient.last_name, source,
    )
    return patient


def update_patient(db: Session, patient: Patient, changes: dict) -> Patient:
    for key, value in changes.items():
        setattr(patient, key, value)
    patient.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(patient)
    log.info("patient.updated id=%s fields=%s", patient.patient_id, list(changes))
    return patient


def soft_delete_patient(db: Session, patient: Patient) -> Patient:
    patient.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(patient)
    log.info("patient.soft_deleted id=%s", patient.patient_id)
    return patient


def count_patients(db: Session, include_deleted: bool = False) -> int:
    stmt = select(func.count(Patient.patient_id))
    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    return db.execute(stmt).scalar_one()


# --- Call logs -----------------------------------------------------------

def get_or_create_call(
    db: Session, call_sid: str, from_number: str | None = None
) -> CallLog:
    call = db.execute(
        select(CallLog).where(CallLog.call_sid == call_sid)
    ).scalars().first()
    if call is None:
        call = CallLog(call_sid=call_sid, from_number=from_number)
        db.add(call)
        db.commit()
        db.refresh(call)
    return call


def append_transcript(db: Session, call: CallLog, speaker: str, text: str) -> None:
    if not text:
        return
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    call.transcript = f"{call.transcript}[{stamp}] {speaker}: {text}\n"
    db.commit()


def save_call_state(
    db: Session,
    call: CallLog,
    collected: dict | None = None,
    outcome: str | None = None,
    patient_id: str | None = None,
) -> None:
    if collected is not None:
        call.collected_json = json.dumps(collected, default=str)
    if outcome is not None:
        call.outcome = outcome
    if patient_id is not None:
        call.patient_id = patient_id
    db.commit()


def list_calls(db: Session, limit: int = 50) -> list[CallLog]:
    stmt = select(CallLog).order_by(CallLog.started_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())
