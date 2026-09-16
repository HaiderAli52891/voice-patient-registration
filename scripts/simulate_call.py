"""Talk to the intake agent from your terminal — no phone, no Twilio.

    python -m scripts.simulate_call

Same code path as a real call: identical prompt, tools, validators and
database writes. This is how to test conversation changes in seconds instead
of dialling a number each time.

Type 'quit' to hang up.
"""

import sys
import uuid

from app import crud
from app.database import SessionLocal, init_db
from app.logging_config import configure_logging
from app.voice.agent import run_turn
from app.voice.prompts import GREETING, RETURNING_GREETING

GREEN = "\033[32m"
BLUE = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def main() -> None:
    configure_logging()
    init_db()

    caller_number = sys.argv[1] if len(sys.argv) > 1 else "+15125550147"
    call_sid = f"SIM{uuid.uuid4().hex[:16]}"

    with SessionLocal() as db:
        call = crud.get_or_create_call(db, call_sid, caller_number)
        history: list[dict] = []

        known = crud.find_by_phone(db, caller_number)
        greeting = (
            RETURNING_GREETING.format(name=f"{known.first_name} {known.last_name}")
            if known
            else GREETING
        )
        if known:
            history.append(
                {
                    "role": "system",
                    "content": (
                        "EXISTING PATIENT MATCHED BY CALLER ID: "
                        f"{known.first_name} {known.last_name}."
                    ),
                }
            )
        history.append({"role": "assistant", "content": greeting})
        crud.append_transcript(db, call, "agent", greeting)

        print(f"{DIM}--- call {call_sid} from {caller_number} ---{RESET}")
        print(f"{GREEN}Riley:{RESET} {greeting}")

        while True:
            try:
                text = input(f"{BLUE}You:{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.lower() in {"quit", "exit"}:
                break
            if not text:
                continue

            crud.append_transcript(db, call, "caller", text)
            reply = run_turn(db, call, history, text)
            crud.append_transcript(db, call, "agent", reply.speech)

            print(f"{GREEN}Riley:{RESET} {reply.speech}")
            if reply.saved_patient_id:
                print(f"{DIM}[saved patient {reply.saved_patient_id}]{RESET}")
            if reply.hangup:
                print(f"{DIM}--- call ended ---{RESET}")
                break


if __name__ == "__main__":
    main()
