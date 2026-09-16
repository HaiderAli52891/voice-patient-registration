"""Test fixtures.

The environment is set before `app` is imported so the cached Settings object
picks up a throwaway SQLite file instead of the real one.
"""

import os
import tempfile

import pytest

_TMP_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["ENVIRONMENT"] = "test"
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def valid_patient():
    return {
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": "03/05/1985",
        "sex": "female",
        "phone_number": "(555) 123-4567",
        "email": "jane.doe@example.com",
        "address_line_1": "742 Evergreen Terrace",
        "city": "springfield",
        "state": "Oregon",
        "zip_code": "97477",
    }
