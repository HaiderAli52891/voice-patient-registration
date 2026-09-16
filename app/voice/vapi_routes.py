"""Optional Vapi / Retell integration.

The Twilio path above owns the whole conversation. If you would rather let
Vapi own the LLM loop (better latency, real barge-in, built-in recordings),
point a Vapi assistant's custom tools at these endpoints instead. The
validation and persistence logic is shared, so both front-ends behave
identically.

Vapi assistant setup is described in README.md under "Option B".
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.validators import (
    REQUIRED_FIELDS,
    validate_field,
    validate_patient_payload,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/vapi", tags=["voice"])


def _auth(secret: str | None) -> None:
    if settings.vapi_secret and secret != settings.vapi_secret:
        raise HTTPException(status_code=401, detail="Bad webhook secret")


def _result(tool_call_id: str, payload: dict) -> dict:
    """Vapi expects results wrapped like this."""
    return {"results": [{"toolCallId": tool_call_id, "result": json.dumps(payload)}]}


@router.post("/tool")
async def vapi_tool(
    request: Request,
    x_vapi_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Single endpoint handling every custom tool Vapi invokes."""
    _auth(x_vapi_secret)
    body = await request.json()
    message = body.get("message", {})

    tool_calls = (
        message.get("toolCalls")
        or message.get("toolCallList")
        or []
    )
    if not tool_calls:
        return {"results": []}

    tool_call = tool_calls[0]
    tool_call_id = tool_call.get("id", "")
    fn = tool_call.get("function", {})
    name = fn.get("name", "")
    args = fn.get("arguments", {})
    if isinstance(args, str):
        args = json.loads(args or "{}")

    call_sid = message.get("call", {}).get("id", "vapi-unknown")
    from_number = (
        message.get("call", {}).get("customer", {}).get("number", "")
    )
    call = crud.get_or_create_call(db, call_sid, from_number)
    collected = json.loads(call.collected_json or "{}")

    if name == "save_field":
        ok, result = validate_field(args.get("field", ""), args.get("value", ""))
        if not ok:
            return _result(tool_call_id, {"ok": False, "message": result})
        collected[args["field"]] = result
        crud.save_call_state(db, call, collected=collected)
        missing = [f for f in REQUIRED_FIELDS if not collected.get(f)]
        return _result(
            tool_call_id,
            {"ok": True, "stored_value": result, "still_missing": missing},
        )

    if name == "lookup_patient":
        existing = crud.find_by_phone(db, args.get("phone_number", ""))
        return _result(
            tool_call_id,
            {"found": bool(existing)}
            | (
                {
                    "first_name": existing.first_name,
                    "last_name": existing.last_name,
                    "patient_id": existing.patient_id,
                }
                if existing
                else {}
            ),
        )

    if name == "save_patient":
        payload = {**collected, **{k: v for k, v in args.items() if k != "confirmed"}}
        cleaned, errors = validate_patient_payload(payload, partial=False)
        if errors:
            field, msg = next(iter(errors.items()))
            return _result(
                tool_call_id, {"ok": False, "field": field, "message": msg}
            )
        try:
            existing = crud.find_by_phone(db, cleaned["phone_number"])
            patient = (
                crud.update_patient(db, existing, cleaned)
                if existing
                else crud.create_patient(db, cleaned, source="voice")
            )
        except Exception:  # noqa: BLE001
            log.exception("vapi.save_failed call=%s", call_sid)
            crud.save_call_state(db, call, outcome="db_error")
            return _result(
                tool_call_id,
                {"ok": False, "message": "Could not save. Ask the caller to call back."},
            )
        crud.save_call_state(
            db, call, collected=cleaned, outcome="completed",
            patient_id=patient.patient_id,
        )
        log.info("registration.vapi patient_id=%s", patient.patient_id)
        return _result(
            tool_call_id,
            {"ok": True, "patient_id": patient.patient_id,
             "first_name": patient.first_name},
        )

    return _result(tool_call_id, {"ok": False, "message": f"Unknown tool {name}"})


@router.post("/events")
async def vapi_events(
    request: Request,
    x_vapi_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Store end-of-call transcripts so every call has an audit trail."""
    _auth(x_vapi_secret)
    body = await request.json()
    message = body.get("message", {})

    if message.get("type") != "end-of-call-report":
        return {"ok": True}

    call_id = message.get("call", {}).get("id", "vapi-unknown")
    call = crud.get_or_create_call(db, call_id)
    call.transcript = message.get("transcript", call.transcript)
    if call.outcome == "in_progress":
        crud.save_call_state(db, call, outcome="abandoned")
    else:
        db.commit()
    log.info("vapi.call_ended id=%s outcome=%s", call_id, call.outcome)
    return {"ok": True}
