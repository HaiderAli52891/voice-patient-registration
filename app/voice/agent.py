
"""The conversational brain.

Deliberately knows nothing about Twilio or Vapi. It takes a transcript of what
the caller said and returns what to say back plus whether to hang up, so the
same engine serves the Twilio webhook, the Vapi tool webhook, and the tests.

The turn handler deliberately limits field collection to one successful
save_field operation per caller turn. This prevents the LLM from collecting
multiple fields or asking multiple questions without giving the caller a
chance to respond.
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


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_save_field(collected: dict, args: dict) -> dict:
    """Validate and save one field."""

    field_name = args.get("field", "")
    raw = args.get("value", "")

    ok, result = validate_field(field_name, raw)

    if not ok:
        log.info(
            "field.rejected %s=%r -> %s",
            field_name,
            raw,
            result,
        )

        return {
            "ok": False,
            "field": field_name,
            "message": result,
        }

    collected[field_name] = result

    log.info(
        "field.saved %s=%r",
        field_name,
        result,
    )

    return {
        "ok": True,
        "field": field_name,
        "stored_value": result,
        "still_missing": _missing(collected),
    }


def _missing(collected: dict) -> list[str]:
    """Return required fields that have not yet been collected."""

    return [
        field_name
        for field_name in REQUIRED_FIELDS
        if not collected.get(field_name)
    ]


def _tool_review(collected: dict) -> dict:
    """Return the current registration state."""

    return {
        "collected": _spoken_view(collected),
        "still_missing": _missing(collected),
        "ready_to_confirm": not _missing(collected),
    }


def _spoken_view(collected: dict) -> dict:
    """Format values the way they should be read aloud."""

    view = dict(collected)

    if view.get("date_of_birth"):
        view["date_of_birth"] = dob_to_spoken(
            view["date_of_birth"]
        )

    for key in (
        "phone_number",
        "emergency_contact_phone",
    ):
        if view.get(key):
            view[key] = phone_to_display(
                view[key]
            )

    return view


def _tool_save_patient(
    db: Session,
    collected: dict,
    args: dict,
    call,
) -> dict:
    """Validate and persist the complete patient registration."""

    if not args.get("confirmed"):
        return {
            "ok": False,
            "message": (
                "Read the information back and get a verbal yes first."
            ),
        }

    cleaned, errors = validate_patient_payload(
        collected,
        partial=False,
    )

    if errors:
        first_field, first_message = next(
            iter(errors.items())
        )

        return {
            "ok": False,
            "field": first_field,
            "message": first_message,
            "still_missing": list(errors),
        }

    try:
        existing = crud.find_by_phone(
            db,
            cleaned["phone_number"],
        )

        if existing:
            patient = crud.update_patient(
                db,
                existing,
                cleaned,
            )
            action = "updated"
        else:
            patient = crud.create_patient(
                db,
                cleaned,
                source="voice",
            )
            action = "created"

    except Exception:  # noqa: BLE001
        log.exception(
            "db.write_failed call_sid=%s",
            getattr(call, "call_sid", "?"),
        )

        crud.save_call_state(
            db,
            call,
            collected=collected,
            outcome="db_error",
        )

        return {
            "ok": False,
            "message": (
                "The save failed. Apologise, tell the caller their "
                "information was not saved, and ask them to call back."
            ),
        }

    crud.save_call_state(
        db,
        call,
        collected=cleaned,
        outcome="completed",
        patient_id=patient.patient_id,
    )

    log.info(
        "registration.%s patient_id=%s payload=%s",
        action,
        patient.patient_id,
        json.dumps(cleaned, default=str),
    )

    return {
        "ok": True,
        "action": action,
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
    }


# ---------------------------------------------------------------------------
# Main turn handler
# ---------------------------------------------------------------------------

# Only a small number of LLM/tool cycles are needed.
#
# Most importantly, after one successful save_field operation, we stop
# processing additional tool calls from that model response. This prevents
# the model from collecting:
#
#     DOB -> sex -> phone
#
# in one caller turn.
#
# Instead it becomes:
#
#     Caller: DOB
#     Riley: Thanks. What is your sex?
#     Caller: Male
#     Riley: Thanks. What is your phone number?
#     Caller: ...
#
MAX_TOOL_ROUNDS = 3


def run_turn(
    db: Session,
    call,
    history: list[dict],
    user_text: str | None,
) -> AgentReply:
    """Advance the conversation by one caller utterance.

    `history` is the OpenAI-format message list for this call and is mutated
    in place.

    The important behavior here is that one caller utterance should normally
    result in one field being captured and one question being asked.
    """

    collected: dict = json.loads(
        call.collected_json or "{}"
    )

    # Make sure the system prompt is present.
    if not history or history[0].get("role") != "system":
        history.insert(
            0,
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
        )

    # Add caller's latest speech.
    if user_text:
        history.append(
            {
                "role": "user",
                "content": user_text,
            }
        )

    # Re-inject current state every turn.
    state_note = {
        "role": "system",
        "content": (
            "CURRENT RECORD: "
            + json.dumps(_spoken_view(collected))
            + " | STILL MISSING: "
            + json.dumps(_missing(collected))
            + " | IMPORTANT: Ask only ONE question at a time. "
            "Never collect another field until the caller responds "
            "to your current question."
        ),
    }

    hangup = False
    saved_id: str | None = None
    speech = ""

    for _ in range(MAX_TOOL_ROUNDS):

        try:
            response = get_client().chat.completions.create(
                model=settings.openai_model,
                messages=[
                    *_trim(history),
                    state_note,
                ],
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.4,
                max_tokens=200,
            )

        except Exception:  # noqa: BLE001
            log.exception(
                "llm.call_failed call_sid=%s",
                call.call_sid,
            )

            crud.save_call_state(
                db,
                call,
                collected=collected,
                outcome="llm_error",
            )

            return AgentReply(
                FATAL_ERROR_MESSAGE,
                hangup=True,
                collected=collected,
            )

        message = response.choices[0].message

        # ------------------------------------------------------------------
        # Normal speech response
        # ------------------------------------------------------------------

        history.append(
            message.model_dump(
                exclude_none=True
            )
        )

        if not message.tool_calls:
            speech = (
                message.content or ""
            ).strip()

            break

        # ------------------------------------------------------------------
        # Process tool calls
        # ------------------------------------------------------------------

        successful_field_saved = False

        for tool_call in message.tool_calls:

            name = tool_call.function.name

            try:
                args = json.loads(
                    tool_call.function.arguments or "{}"
                )
            except json.JSONDecodeError:
                args = {}

            # --------------------------------------------------------------
            # save_field
            # --------------------------------------------------------------

            if name == "save_field":

                # Once one field has been successfully saved, do not allow
                # another field to be captured from the same caller turn.
                if successful_field_saved:
                    result = {
                        "ok": False,
                        "message": (
                            "Do not save another field during this turn. "
                            "Ask the caller for the next field and wait "
                            "for their response."
                        ),
                    }

                else:
                    result = _tool_save_field(
                        collected,
                        args,
                    )

                    if result.get("ok"):
                        successful_field_saved = True

            # --------------------------------------------------------------
            # review_registration
            # --------------------------------------------------------------

            elif name == "review_registration":

                result = _tool_review(
                    collected
                )

            # --------------------------------------------------------------
            # reset_registration
            # --------------------------------------------------------------

            elif name == "reset_registration":

                collected.clear()

                result = {
                    "ok": True,
                    "message": (
                        "Cleared. Start again from the name."
                    ),
                }

            # --------------------------------------------------------------
            # save_patient
            # --------------------------------------------------------------

            elif name == "save_patient":

                result = _tool_save_patient(
                    db,
                    collected,
                    args,
                    call,
                )

                if result.get("ok"):
                    saved_id = result[
                        "patient_id"
                    ]

            # --------------------------------------------------------------
            # end_call
            # --------------------------------------------------------------

            elif name == "end_call":

                hangup = True

                result = {
                    "ok": True
                }

            # --------------------------------------------------------------
            # Unknown tool
            # --------------------------------------------------------------

            else:

                result = {
                    "ok": False,
                    "message": (
                        f"Unknown tool {name}"
                    ),
                }

            # Add tool response to conversation history.
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

            # If a field was successfully saved, stop processing any
            # additional tool calls returned by the model.
            #
            # This is the key fix.
            if successful_field_saved:
                break

        # Persist the current state.
        crud.save_call_state(
            db,
            call,
            collected=collected,
        )

        # Refresh state note for the next model call.
        state_note = {
            "role": "system",
            "content": (
                "CURRENT RECORD: "
                + json.dumps(_spoken_view(collected))
                + " | STILL MISSING: "
                + json.dumps(_missing(collected))
                + " | IMPORTANT: Ask only ONE question at a time. "
                "The caller must answer your current question before "
                "you collect another field."
            ),
        }

        # ------------------------------------------------------------------
        # Critical behavior:
        #
        # After successfully saving ONE field, immediately ask the model
        # for natural speech based on the tool result, but do NOT let it
        # invoke another tool.
        # ------------------------------------------------------------------

        if successful_field_saved:

            try:
                followup_response = (
                    get_client()
                    .chat.completions.create(
                        model=settings.openai_model,
                        messages=[
                            *_trim(history),
                            state_note,
                            {
                                "role": "system",
                                "content": (
                                    "The caller has just provided one "
                                    "valid piece of information. "
                                    "Acknowledge it briefly and ask "
                                    "ONLY for the next missing required "
                                    "field. Do not save another field. "
                                    "Do not ask multiple questions. "
                                    "Then stop and wait for the caller."
                                ),
                            },
                        ],
                        # Do not give tools to this follow-up call.
                        #
                        # This makes it impossible for the model to
                        # automatically save sex, phone, address, etc.
                        # without another caller response.
                        tool_choice="none",
                        temperature=0.4,
                        max_tokens=120,
                    )
                )

                followup_message = (
                    followup_response.choices[0].message
                )

                speech = (
                    followup_message.content or ""
                ).strip()

                if speech:
                    history.append(
                        followup_message.model_dump(
                            exclude_none=True
                        )
                    )

                break

            except Exception:  # noqa: BLE001
                log.exception(
                    "llm.followup_failed call_sid=%s",
                    call.call_sid,
                )

                # Fall back to a simple deterministic question if the
                # follow-up LLM request fails.
                missing = _missing(collected)

                if missing:
                    next_field = missing[0]

                    question_map = {
                        "first_name": "What is your first name?",
                        "last_name": "What is your last name?",
                        "date_of_birth": "What is your date of birth?",
                        "sex": "What is your sex, male or female?",
                        "phone_number": "What is your phone number?",
                        "address_line_1": "What is your street address?",
                        "city": "What city do you live in?",
                        "state": "What state do you live in?",
                        "zip_code": "What is your ZIP code?",
                    }

                    speech = question_map.get(
                        next_field,
                        "What is the next piece of information?"
                    )

                break

        # ------------------------------------------------------------------
        # Handle hangup.
        # ------------------------------------------------------------------

        if hangup:
            # Allow the model to produce the farewell message.
            continue

    # ----------------------------------------------------------------------
    # Fallback speech
    # ----------------------------------------------------------------------

    if not speech:

        if hangup:
            speech = (
                "Thanks, you're all set. Goodbye."
            )
        else:
            speech = (
                "Sorry, could you say that again?"
            )

    # Persist state after every turn.
    crud.save_call_state(
        db,
        call,
        collected=collected,
    )

    return AgentReply(
        speech=speech,
        hangup=hangup,
        saved_patient_id=saved_id,
        collected=collected,
    )


def _trim(
    history: list[dict],
    keep: int = 30,
) -> list[dict]:
    """Keep the system prompt plus recent turns.

    Trimming must not orphan a tool message from its assistant tool_call,
    so we walk backwards to a safe boundary.
    """

    if len(history) <= keep + 1:
        return history

    head = history[:1]
    tail = history[-keep:]

    while tail and tail[0].get("role") == "tool":
        tail = tail[1:]

    return head + tail


### Then do these commands

