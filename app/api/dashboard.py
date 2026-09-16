"""Server-rendered dashboard for front-desk staff.

Server-rendered Jinja rather than a SPA: it is one page of read-only records,
and a build step would add minutes to a three-hour budget for no user gain.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.validators import dob_to_us, phone_to_display

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)
_basic = HTTPBasic(auto_error=False)


def require_dashboard_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    """Basic auth, only enforced when DASHBOARD_USER is configured."""
    if not settings.dashboard_user:
        return
    valid = (
        credentials is not None
        and secrets.compare_digest(credentials.username, settings.dashboard_user)
        and secrets.compare_digest(credentials.password, settings.dashboard_password)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authorised",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/dashboard", include_in_schema=False)
def dashboard(
    request: Request,
    q: str = Query(default=""),
    db: Session = Depends(get_db),
    _: None = Depends(require_dashboard_auth),
):
    term = q.strip()
    patients = crud.list_patients(db, limit=200)

    if term:
        needle = term.lower()
        digits = "".join(ch for ch in term if ch.isdigit())
        patients = [
            p
            for p in patients
            if needle in f"{p.first_name} {p.last_name}".lower()
            or (digits and digits in p.phone_number)
            or needle in (p.city or "").lower()
        ]

    rows = [
        {
            "patient_id": p.patient_id,
            "name": f"{p.first_name} {p.last_name}",
            "dob": dob_to_us(p.date_of_birth),
            "sex": p.sex,
            "phone": phone_to_display(p.phone_number),
            "email": p.email,
            "address": ", ".join(
                filter(None, [p.address_line_1, p.address_line_2,
                              f"{p.city}, {p.state} {p.zip_code}"])
            ),
            "insurance": p.insurance_provider,
            "member_id": p.insurance_member_id,
            "language": p.preferred_language,
            "emergency": (
                f"{p.emergency_contact_name} · {phone_to_display(p.emergency_contact_phone)}"
                if p.emergency_contact_name
                else None
            ),
            "source": p.source,
            "created_at": p.created_at,
        }
        for p in patients
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "query": term,
            "total": crud.count_patients(db),
            "calls": crud.list_calls(db, limit=12),
            "phone_number": phone_to_display(
                "".join(c for c in settings.twilio_phone_number if c.isdigit())[-10:]
            )
            or settings.twilio_phone_number,
        },
    )
