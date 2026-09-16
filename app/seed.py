"""Insert two demo patients so the dashboard and API are not empty.

    python -m app.seed

Idempotent: re-running does not create duplicates.
"""

import logging

from app import crud
from app.database import SessionLocal, init_db
from app.logging_config import configure_logging

SEED_PATIENTS = [
    {
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": "1985-03-05",
        "sex": "Female",
        "phone_number": "5551234567",
        "email": "jane.doe@example.com",
        "address_line_1": "742 Evergreen Terrace",
        "address_line_2": "Apt 3B",
        "city": "Springfield",
        "state": "OR",
        "zip_code": "97477",
        "insurance_provider": "Blue Cross Blue Shield",
        "insurance_member_id": "BCBS884213",
        "preferred_language": "English",
        "emergency_contact_name": "John Doe",
        "emergency_contact_phone": "5559876543",
    },
    {
        "first_name": "Miguel",
        "last_name": "Alvarez",
        "date_of_birth": "1972-11-18",
        "sex": "Male",
        "phone_number": "5122003344",
        "email": "m.alvarez@example.com",
        "address_line_1": "1900 Barton Springs Rd",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78704",
        "preferred_language": "Spanish",
    },
]


def main() -> None:
    configure_logging()
    log = logging.getLogger("app.seed")
    init_db()

    with SessionLocal() as db:
        for record in SEED_PATIENTS:
            if crud.find_by_phone(db, record["phone_number"]):
                log.info("seed.skip %s %s", record["first_name"], record["last_name"])
                continue
            patient = crud.create_patient(db, dict(record), source="seed")
            log.info("seed.created %s", patient.patient_id)

    log.info("seed.done")


if __name__ == "__main__":
    main()
