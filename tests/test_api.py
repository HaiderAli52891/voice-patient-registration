"""Integration tests for the REST layer."""


def create(client, payload):
    return client.post("/patients", json=payload)


def test_health(client):
    body = client.get("/health").json()
    assert body["error"] is None
    assert body["data"]["database"] == "up"


def test_create_returns_201_and_normalises_input(client, valid_patient):
    response = create(client, valid_patient)
    assert response.status_code == 201
    data = response.json()["data"]

    assert data["patient_id"]
    assert data["sex"] == "Female"                    # "female" -> enum
    assert data["state"] == "OR"                      # "Oregon" -> abbreviation
    assert data["city"] == "Springfield"
    assert data["phone_number"] == "(555) 123-4567"
    assert data["date_of_birth"] == "03/05/1985"
    assert data["preferred_language"] == "English"    # default applied
    assert response.json()["error"] is None


def test_create_rejects_bad_fields_with_422(client, valid_patient):
    bad = {**valid_patient, "phone_number": "123", "zip_code": "9747",
           "date_of_birth": "12/31/2099"}
    response = create(client, bad)
    assert response.status_code == 422
    fields = response.json()["error"]["fields"]
    assert "phone_number" in fields
    assert "zip_code" in fields
    assert "date_of_birth" in fields
    assert response.json()["data"] is None


def test_create_requires_required_fields(client):
    response = create(client, {"first_name": "Jane"})
    assert response.status_code == 422


def test_get_by_id(client, valid_patient):
    pid = create(client, valid_patient).json()["data"]["patient_id"]
    response = client.get(f"/patients/{pid}")
    assert response.status_code == 200
    assert response.json()["data"]["last_name"] == "Doe"


def test_get_unknown_id_returns_404(client):
    response = client.get("/patients/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["data"] is None


def test_list_and_filters(client, valid_patient):
    create(client, valid_patient)
    create(client, {**valid_patient, "first_name": "Miguel", "last_name": "Alvarez",
                    "phone_number": "5122003344", "email": None})

    assert len(client.get("/patients").json()["data"]) == 2
    assert len(client.get("/patients?last_name=alvarez").json()["data"]) == 1
    assert len(client.get("/patients?phone_number=555-123-4567").json()["data"]) == 1
    assert len(client.get("/patients?date_of_birth=03/05/1985").json()["data"]) == 2
    assert client.get("/patients?last_name=nobody").json()["data"] == []


def test_partial_update(client, valid_patient):
    pid = create(client, valid_patient).json()["data"]["patient_id"]
    response = client.put(f"/patients/{pid}", json={"city": "portland",
                                                    "insurance_provider": "Aetna"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["city"] == "Portland"
    assert data["insurance_provider"] == "Aetna"
    assert data["last_name"] == "Doe"          # untouched


def test_update_validates(client, valid_patient):
    pid = create(client, valid_patient).json()["data"]["patient_id"]
    assert client.put(f"/patients/{pid}", json={"state": "Westeros"}).status_code == 422


def test_update_unknown_id_returns_404(client):
    response = client.put("/patients/does-not-exist", json={"city": "Austin"})
    assert response.status_code == 404


def test_soft_delete_hides_but_keeps_record(client, valid_patient):
    pid = create(client, valid_patient).json()["data"]["patient_id"]

    assert client.delete(f"/patients/{pid}").status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []

    # Still there, just flagged.
    revived = client.get("/patients?include_deleted=true").json()["data"]
    assert len(revived) == 1
    assert revived[0]["deleted_at"] is not None


def test_dashboard_renders(client, valid_patient):
    create(client, valid_patient)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Jane Doe" in response.text
