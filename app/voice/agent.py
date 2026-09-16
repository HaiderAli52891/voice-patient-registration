"""The conversational brain.

Deliberately knows nothing about Twilio or Vapi. It takes a transcript of what
the caller said and returns what to say back plus whether to hang up, so the
same engine serves the Twilio webhook, the Vapi tool webhook, and the tests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.validators import (
    REQUIRED_FIELDS,
    dob_to_spoken,
    phone_to_display,
    validate_field,
    validate_patient_payload,
)
from app.voice.prompts import (
    FATAL_ERROR_MESSAGE,
    SYSTEM_PROMPT,
    TOOLS,
)

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=20.0,
            max_retries=1,
        )
    return _client

@dataclass
class AgentReply:
    """What the telephony layer should do next."""

    speech: str
    hangup: bool = False
    saved_patient_id: str | None = None
    collected: dict = field(default_factory=dict)


# --- Tool implementations -------------------------------------------------
# Each returns a JSON-serialisable dict that goes straight back to the model.


def _tool_save_field(collected: dict, args: dict) -> dict:
    field_name = args.get("field", "")
    raw = args.get("value", "")
    ok, result = validate_field(field_name, raw)
    if not ok:
        log.info("field.rejected %s=%r -> %s", field_name, raw, result)
        return {"ok": False, "field": field_name, "message": result}
    collected[field_name] = result
    log.info("field.saved %s=%r", field_name, result)
    return {
        "ok": True,
        "field": field_name,
        "stored_value": result,
        "still_missing": _missing(collected),
    }


def _missing(collected: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if not collected.get(f)]


def _tool_review(collected: dict) -> dict:
    return {
        "collected": _spoken_view(collected),
        "still_missing": _missing(collected),
        "ready_to_confirm": not _missing(collected),
    }


def _spoken_view(collected: dict) -> dict:
    """Format values the way they should be read aloud."""
    view = dict(collected)
    if view.get("date_of_birth"):
        view["date_of_birth"] = dob_to_spoken(view["date_of_birth"])
    for key in ("phone_number", "emergency_contact_phone"):
        if view.get(key):
            view[key] = phone_to_display(view[key])
    return view


def _tool_save_patient(
    db: Session, collected: dict, args: dict, call
) -> dict:
    if not args.get("confirmed"):
        return {
            "ok": False,
            "message": "Read the information back and get a verbal yes first.",
        }

    cleaned, errors = validate_patient_payload(collected, partial=False)
    if errors:
        first_field, first_message = next(iter(errors.items()))
        return {
            "ok": False,
            "field": first_field,
            "message": first_message,
            "still_missing": list(errors),
        }

    try:
        existing = crud.find_by_phone(db, cleaned["phone_number"])
        if existing:
            patient = crud.update_patient(db, existing, cleaned)
            action = "updated"
        else:
            patient = crud.create_patient(db, cleaned, source="voice")
            action = "created"
    except Exception:  # noqa: BLE001 - surfaced to the caller as speech
        log.exception("db.write_failed call_sid=%s", getattr(call, "call_sid", "?"))
        crud.save_call_state(db, call, collected=collected, outcome="db_error")
        return {
            "ok": False,
            "message": (
                "The save failed. Apologise, tell the caller their information "
                "was not saved, and ask them to call back."
            ),
        }

    crud.save_call_state(
        db, call, collected=cleaned, outcome="completed", patient_id=patient.patient_id
    )
    log.info(
        "registration.%s patient_id=%s payload=%s",
        action, patient.patient_id, json.dumps(cleaned, default=str),
    )
    return {
        "ok": True,
        "action": action,
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
    }


# --- Main turn handler ----------------------------------------------------

MAX_TOOL_ROUNDS = 6


def run_turn(
    db: Session,
    call,
    history: list[dict],
    user_text: str | None,
) -> AgentReply:
    """Advance the conversation by one caller utterance.

    `history` is the OpenAI-format message list for this call (mutated in
    place so the caller can persist it). Returns what to speak next.
    """
    collected: dict = json.loads(call.collected_json or "{}")

    if not history or history[0].get("role") != "system":
        history.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    if user_text:
        history.append({"role": "user", "content": user_text})

    # Re-inject current state every turn: the model stays correct even if the
    # window is trimmed or the call was resumed after a reconnect.
    state_note = {
        "role": "system",
        "content": (
            "CURRENT RECORD: " + json.dumps(_spoken_view(collected))
            + " | STILL MISSING: " + json.dumps(_missing(collected))
        ),
    }

    hangup = False
    saved_id: str | None = None
    speech = ""

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = get_client().chat.completions.create(
                model=settings.openai_model,
                messages=[*_trim(history), state_note],
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.6,
                max_tokens=200,
            )
        except Exception:  # noqa: BLE001
            log.exception("llm.call_failed call_sid=%s", call.call_sid)
            crud.save_call_state(db, call, collected=collected, outcome="llm_error")
            return AgentReply(FATAL_ERROR_MESSAGE, hangup=True, collected=collected)

        message = response.choices[0].message
        history.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            speech = (message.content or "").strip()
            break

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "save_field":
                result = _tool_save_field(collected, args)
            elif name == "review_registration":
                result = _tool_review(collected)
            elif name == "reset_registration":
                collected.clear()
                result = {"ok": True, "message": "Cleared. Start again from the name."}
            elif name == "save_patient":
                result = _tool_save_patient(db, collected, args, call)
                if result.get("ok"):
                    saved_id = result["patient_id"]
            elif name == "end_call":
                hangup = True
                result = {"ok": True}
            else:
                result = {"ok": False, "message": f"Unknown tool {name}"}

            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

        crud.save_call_state(db, call, collected=collected)
        state_note = {
            "role": "system",
            "content": (
                "CURRENT RECORD: " + json.dumps(_spoken_view(collected))
                + " | STILL MISSING: " + json.dumps(_missing(collected))
            ),
        }

        if hangup:
            # Let the model produce its farewell line in the next round, but
            # stop tool use.
            continue

    if not speech:
        speech = (
            "Thanks, you're all set. Goodbye."
            if hangup
            else "Sorry, could you say that again?"
        )

    crud.save_call_state(db, call, collected=collected)
    return AgentReply(
        speech=speech, hangup=hangup, saved_patient_id=saved_id, collected=collected
    )


def _trim(history: list[dict], keep: int = 30) -> list[dict]:
    """Keep the system prompt plus the most recent turns.

    Trimming must not orphan a tool message from its assistant tool_call, so
    we walk backwards to a safe boundary.
    """
    if len(history) <= keep + 1:
        return history
    head, tail = history[:1], history[-keep:]
    while tail and tail[0].get("role") == "tool":
        tail = tail[1:]
    return head + tail
