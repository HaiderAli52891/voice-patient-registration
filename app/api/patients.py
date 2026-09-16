"""REST API for patient records.

Every response uses the envelope {"data": ..., "error": ...}.
Status codes: 200 OK, 201 Created, 400 bad request, 404 not found,
422 validation failure, 500 server error.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas import CallLogOut, PatientCreate, PatientOut, PatientUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/patients", tags=["patients"])


def ok(data):
    return {"data": data, "error": None}


@router.get("", summary="List patients")
@router.get("/", include_in_schema=False)
def list_patients(
    last_name: str | None = Query(default=None),
    date_of_birth: str | None = Query(default=None, description="MM/DD/YYYY"),
    phone_number: str | None = Query(default=None),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    patients = crud.list_patients(
        db,
        last_name=last_name,
        date_of_birth=date_of_birth,
        phone_number=phone_number,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    return ok([PatientOut.from_model(p).model_dump(mode="json") for p in patients])


@router.get("/{patient_id}", summary="Get one patient")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = crud.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return ok(PatientOut.from_model(patient).model_dump(mode="json"))


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a patient")
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_none=True)
    try:
        patient = crud.create_patient(db, data, source="api")
    except Exception:  # noqa: BLE001
        log.exception("api.create_failed")
        raise HTTPException(status_code=500, detail="Could not create patient")
    return ok(PatientOut.from_model(patient).model_dump(mode="json"))


@router.put("/{patient_id}", summary="Update a patient (partial allowed)")
@router.patch("/{patient_id}", include_in_schema=False)
def update_patient(
    patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)
):
    patient = crud.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        patient = crud.update_patient(db, patient, payload.changes())
    except Exception:  # noqa: BLE001
        log.exception("api.update_failed id=%s", patient_id)
        raise HTTPException(status_code=500, detail="Could not update patient")
    return ok(PatientOut.from_model(patient).model_dump(mode="json"))


@router.delete("/{patient_id}", summary="Soft-delete a patient")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = crud.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    patient = crud.soft_delete_patient(db, patient)
    return ok(
        {
            "patient_id": patient.patient_id,
            "deleted_at": patient.deleted_at.isoformat(),
        }
    )


calls_router = APIRouter(prefix="/calls", tags=["calls"])


@calls_router.get("", summary="Recent call transcripts")
@calls_router.get("/", include_in_schema=False)
def list_calls(limit: int = Query(default=50, ge=1, le=200),
               db: Session = Depends(get_db)):
    calls = crud.list_calls(db, limit=limit)
    return ok([CallLogOut.model_validate(c).model_dump(mode="json") for c in calls])


@calls_router.get("/{call_sid}", summary="One call transcript")
def get_call(call_sid: str, db: Session = Depends(get_db),
             response: Response = None):
    calls = [c for c in crud.list_calls(db, limit=500) if c.call_sid == call_sid]
    if not calls:
        raise HTTPException(status_code=404, detail="Call not found")
    return ok(CallLogOut.model_validate(calls[0]).model_dump(mode="json"))
