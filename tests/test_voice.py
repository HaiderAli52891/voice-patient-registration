"""Telephony layer tests.

The LLM is stubbed out: these assert the TwiML contract and the failure
behaviour, which is what actually breaks a live call.
"""

import app.voice.twilio_routes as tw
from app.voice.agent import AgentReply


def test_incoming_call_greets_and_listens(client):
    response = client.post(
        "/voice/incoming", data={"CallSid": "CA1", "From": "+15125550147"}
    )
    assert response.status_code == 200
    assert "<Response>" in response.text
    assert "<Gather" in response.text
    assert "Lakeside" in response.text
    assert "/voice/turn" in response.text


def test_returning_caller_is_recognised(client, valid_patient):
    client.post("/patients", json=valid_patient)
    response = client.post(
        "/voice/incoming", data={"CallSid": "CA2", "From": "+15551234567"}
    )
    assert "Jane Doe" in response.text
    assert "update your information" in response.text


def test_empty_speech_reprompts_without_hanging_up(client):
    client.post("/voice/incoming", data={"CallSid": "CA3", "From": "+15125550147"})
    response = client.post("/voice/turn", data={"CallSid": "CA3", "SpeechResult": ""})
    assert "<Gather" in response.text
    assert "<Hangup" not in response.text


def test_turn_speaks_agent_reply_and_keeps_listening(client, monkeypatch):
    monkeypatch.setattr(
        tw, "run_turn", lambda *a, **k: AgentReply("And your last name?")
    )
    client.post("/voice/incoming", data={"CallSid": "CA4", "From": "+15125550147"})
    response = client.post(
        "/voice/turn", data={"CallSid": "CA4", "SpeechResult": "My name is Jane"}
    )
    assert "And your last name?" in response.text
    assert "<Gather" in response.text


def test_hangup_after_successful_save(client, monkeypatch):
    monkeypatch.setattr(
        tw,
        "run_turn",
        lambda *a, **k: AgentReply("You're all set, Jane.", hangup=True,
                                   saved_patient_id="abc"),
    )
    client.post("/voice/incoming", data={"CallSid": "CA5", "From": "+15125550147"})
    response = client.post(
        "/voice/turn", data={"CallSid": "CA5", "SpeechResult": "yes that's right"}
    )
    assert "<Hangup" in response.text
    assert "<Gather" not in response.text


def test_agent_failure_apologises_instead_of_silence(client, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(tw, "run_turn", boom)
    client.post("/voice/incoming", data={"CallSid": "CA6", "From": "+15125550147"})
    response = client.post(
        "/voice/turn", data={"CallSid": "CA6", "SpeechResult": "hello"}
    )
    assert response.status_code == 200
    assert "call us back" in response.text
    assert "<Hangup" in response.text


def test_dropped_call_is_recorded_as_abandoned(client):
    client.post("/voice/incoming", data={"CallSid": "CA7", "From": "+15125550147"})
    assert client.post(
        "/voice/status", data={"CallSid": "CA7", "CallStatus": "completed"}
    ).status_code == 204

    call = client.get("/calls").json()["data"][0]
    assert call["outcome"] == "abandoned_completed"
    assert "Lakeside" in call["transcript"]


def test_transcript_captures_both_sides(client, monkeypatch):
    monkeypatch.setattr(tw, "run_turn", lambda *a, **k: AgentReply("Thanks, Jane."))
    client.post("/voice/incoming", data={"CallSid": "CA8", "From": "+15125550147"})
    client.post("/voice/turn", data={"CallSid": "CA8", "SpeechResult": "I'm Jane"})

    transcript = client.get("/calls/CA8").json()["data"]["transcript"]
    assert "caller: I'm Jane" in transcript
    assert "agent: Thanks, Jane." in transcript
