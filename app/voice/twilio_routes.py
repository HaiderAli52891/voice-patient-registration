"""Twilio voice webhooks.

Call flow
---------
  Twilio  --POST /voice/incoming-->  greeting + <Gather input="speech">
          --POST /voice/turn------>  agent reply + <Gather ...>   (loop)
          --POST /voice/status---->  mark abandoned calls

Why Twilio <Gather> rather than a media-stream + Deepgram + ElevenLabs
pipeline: <Gather> gives working speech-to-text and neural text-to-speech in
about forty lines of code, which is the right trade-off for a three-hour
build. The cost is turn latency (~1s) and no barge-in. See the README for how
the Vapi path removes both limits without changing the agent.
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from app import crud
from app.config import settings
from app.database import get_db
from app.voice.agent import run_turn
from app.voice.prompts import (
    FATAL_ERROR_MESSAGE,
    GREETING,
    NO_INPUT_REPROMPT,
    RETURNING_GREETING,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

# Conversation history per live call.
#
# Kept in memory because it is worthless once the call ends, and because a
# round-trip to the DB on every syllable is wasted I/O. The *collected fields*
# are persisted after every tool call, so a process restart mid-call loses the
# chat history but not the patient data — the agent re-reads its state from
# `call_logs.collected_json` and carries on. Trade-off documented in the
# README: this assumes a single web worker. Move to Redis for multi-worker.
_HISTORIES: dict[str, list[dict]] = {}
_LOCK = threading.Lock()


def _history(call_sid: str) -> list[dict]:
    with _LOCK:
        return _HISTORIES.setdefault(call_sid, [])


def _drop_history(call_sid: str) -> None:
    with _LOCK:
        _HISTORIES.pop(call_sid, None)


async def verify_twilio(request: Request) -> None:
    """Reject forged webhooks. Enabled with VALIDATE_TWILIO_SIGNATURE=true."""
    if not settings.validate_twilio_signature:
        return
    validator = RequestValidator(settings.twilio_auth_token)
    form = await request.form()
    url = str(request.url).replace("http://", "https://", 1)
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validator.validate(url, dict(form), signature):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def _twiml(response: VoiceResponse) -> Response:
    return Response(content=str(response), media_type="application/xml")


def _say(response: VoiceResponse, text: str) -> None:
    response.say(text, voice=settings.twilio_voice)


def _listen(response: VoiceResponse) -> VoiceResponse:
    """Attach a speech <Gather> that posts the caller's words to /voice/turn."""
    gather = Gather(
        input="speech",
        action="https://web-production-9dd9e.up.railway.app/voice/turn",
        method="POST",
        speech_timeout="auto",
        speech_model="phone_call",
        language="en-US",
        # Fire the action even when the caller says nothing, so we can
        # re-prompt instead of silently dropping the call.
        action_on_empty_result=True,
        profanity_filter=False,
    )
    response.append(gather)
    return response


@router.post("/incoming")
async def incoming_call(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """First webhook Twilio hits when someone dials the number."""
    await verify_twilio(request)
    log.info("call.started sid=%s from=%s", CallSid, From)

    call = crud.get_or_create_call(db, CallSid, From)
    response = VoiceResponse()

    # Bonus: recognise a returning caller by caller ID.
    known = crud.find_by_phone(db, From) if From else None
    if known:
        greeting = RETURNING_GREETING.format(name=f"{known.first_name} {known.last_name}")
        history = _history(CallSid)
        history.append(
            {
                "role": "system",
                "content": (
                    "EXISTING PATIENT MATCHED BY CALLER ID: "
                    f"{known.first_name} {known.last_name}, patient_id "
                    f"{known.patient_id}. You have greeted them and asked if "
                    "they want to update. Saving will update this record."
                ),
            }
        )
        crud.save_call_state(db, call, patient_id=known.patient_id)
    else:
        greeting = GREETING

    history = _history(CallSid)
    history.append({"role": "assistant", "content": greeting})
    crud.append_transcript(db, call, "agent", greeting)

    _say(response, greeting)
    return _twiml(_listen(response))


@router.post("/turn")
async def conversation_turn(
    request: Request,
    CallSid: str = Form(...),
    SpeechResult: str = Form(default=""),
    Confidence: float = Form(default=0.0),
    db: Session = Depends(get_db),
):
    """One caller utterance in, one agent reply out."""
    await verify_twilio(request)

    call = crud.get_or_create_call(db, CallSid)
    response = VoiceResponse()
    caller_text = (SpeechResult or "").strip()

    # Silence or unusable audio: re-prompt rather than hang up.
    if not caller_text:
        log.info("call.no_input sid=%s", CallSid)
        _say(response, NO_INPUT_REPROMPT)
        return _twiml(_listen(response))

    log.info("caller.said sid=%s conf=%.2f text=%r", CallSid, Confidence, caller_text)
    crud.append_transcript(db, call, "caller", caller_text)

    try:
        reply = run_turn(db, call, _history(CallSid), caller_text)
    except Exception:  # noqa: BLE001 - never leave the caller in silence
        log.exception("turn.failed sid=%s", CallSid)
        crud.save_call_state(db, call, outcome="error")
        _say(response, FATAL_ERROR_MESSAGE)
        response.hangup()
        _drop_history(CallSid)
        return _twiml(response)

    crud.append_transcript(db, call, "agent", reply.speech)
    _say(response, reply.speech)

    if reply.hangup:
        log.info(
            "call.completed sid=%s patient_id=%s", CallSid, reply.saved_patient_id
        )
        response.hangup()
        _drop_history(CallSid)
        return _twiml(response)

    return _twiml(_listen(response))


@router.post("/status")
async def call_status(
    request: Request,
    CallSid: str = Form(...),
    CallStatus: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Twilio status callback — records dropped calls instead of losing them."""
    await verify_twilio(request)
    log.info("call.status sid=%s status=%s", CallSid, CallStatus)

    if CallStatus in {"completed", "failed", "busy", "no-answer", "canceled"}:
        call = crud.get_or_create_call(db, CallSid)
        if call.outcome == "in_progress":
            # The caller hung up before confirming. Everything they said is
            # already in call_logs, so an operator can follow up.
            crud.save_call_state(db, call, outcome=f"abandoned_{CallStatus}")
        _drop_history(CallSid)
    return Response(status_code=204)


@router.post("/fallback")
async def fallback(CallSid: str = Form(default="")):
    """Configured as Twilio's fallback URL so an app crash still says something."""
    response = VoiceResponse()
    response.say(FATAL_ERROR_MESSAGE, voice=settings.twilio_voice)
    response.hangup()
    return _twiml(response)
