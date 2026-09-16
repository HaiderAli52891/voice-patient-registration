"""Validator unit tests — the rules the voice agent depends on."""

from datetime import date, timedelta

import pytest

from app.validators import (
    validate_dob,
    validate_email,
    validate_phone,
    validate_sex,
    validate_state,
    validate_zip,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("03/05/1985", "1985-03-05"),
        ("1985-03-05", "1985-03-05"),
        ("March 5 1985", "1985-03-05"),
        ("March 5th, 1985", "1985-03-05"),
    ],
)
def test_dob_accepts_spoken_and_written_forms(raw, expected):
    ok, value = validate_dob(raw)
    assert ok and value == expected


def test_dob_rejects_future_dates():
    tomorrow = (date.today() + timedelta(days=1)).strftime("%m/%d/%Y")
    ok, message = validate_dob(tomorrow)
    assert not ok and "future" in message.lower()


def test_dob_rejects_gibberish():
    assert validate_dob("sometime in the spring")[0] is False


@pytest.mark.parametrize(
    "raw",
    ["(555) 123-4567", "555-123-4567", "+1 555 123 4567", "five five five 1234567"],
)
def test_phone_normalises_to_ten_digits(raw):
    ok, value = validate_phone(raw)
    assert ok and value == "5551234567"


def test_phone_rejects_short_numbers():
    ok, message = validate_phone("123")
    assert not ok and "10 digits" in message


def test_phone_rejects_invalid_area_code():
    assert validate_phone("0551234567")[0] is False


@pytest.mark.parametrize("raw,expected", [("California", "CA"), ("c.a.", "CA"), ("ny", "NY")])
def test_state_accepts_names_and_abbreviations(raw, expected):
    ok, value = validate_state(raw)
    assert ok and value == expected


def test_state_rejects_unknown():
    assert validate_state("Westeros")[0] is False


@pytest.mark.parametrize("raw,expected", [("97477", "97477"), ("974771234", "97477-1234")])
def test_zip_formats(raw, expected):
    ok, value = validate_zip(raw)
    assert ok and value == expected


def test_zip_rejects_four_digits():
    assert validate_zip("9747")[0] is False


@pytest.mark.parametrize(
    "raw,expected",
    [("f", "Female"), ("MALE", "Male"), ("non-binary", "Other"),
     ("prefer not to say", "Decline to Answer")],
)
def test_sex_aliases(raw, expected):
    ok, value = validate_sex(raw)
    assert ok and value == expected


def test_email_understands_spoken_form():
    ok, value = validate_email("jane dot doe at example dot com")
    assert ok and value == "jane.doe@example.com"


def test_email_rejects_malformed():
    assert validate_email("jane at example")[0] is False


def test_email_is_optional():
    ok, value = validate_email("")
    assert ok and value == ""
