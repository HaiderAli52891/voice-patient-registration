"""Validation and normalisation helpers.

These live outside the Pydantic schemas on purpose: the voice agent needs to
validate a *single* field mid-conversation ("that ZIP was only four digits")
without constructing a whole patient object.

Every function returns (ok, normalised_value_or_error_message).
"""

from __future__ import annotations

import re
from datetime import date, datetime

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}

# Full state names -> abbreviation. Callers say "California", not "C-A".
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "puerto rico": "PR",
}

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
SEX_ALIASES = {
    "m": "Male", "male": "Male", "man": "Male", "boy": "Male",
    "f": "Female", "female": "Female", "woman": "Female", "girl": "Female",
    "o": "Other", "other": "Other", "nonbinary": "Other",
    "non-binary": "Other", "non binary": "Other", "x": "Other",
    "decline": "Decline to Answer", "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "no answer": "Decline to Answer",
    "skip": "Decline to Answer", "rather not say": "Decline to Answer",
}

NAME_RE = re.compile(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-. ]{0,49}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[A-Za-z]{2,}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")

# Callers spell emails aloud: "jane dot doe at gmail dot com".
_SPOKEN_EMAIL = [
    (r"\s+at\s+", "@"),
    (r"\s+dot\s+", "."),
    (r"\s+underscore\s+", "_"),
    (r"\s+dash\s+", "-"),
    (r"\s+hyphen\s+", "-"),
]


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


# --- Names ---------------------------------------------------------------

def validate_name(value, field: str = "name") -> tuple[bool, str]:
    v = _clean(value).strip(" -")
    if not v:
        return False, f"{field} is required."
    if len(v) > 50:
        return False, f"{field} must be 50 characters or fewer."
    if not NAME_RE.match(v):
        return False, (
            f"{field} may only contain letters, spaces, hyphens and apostrophes."
        )
    # Title-case but keep O'Brien / McDonald-Smith readable.
    return True, "-".join(p.capitalize() if p.islower() or p.isupper() else p
                          for p in v.split("-"))


# --- Date of birth -------------------------------------------------------

def validate_dob(value) -> tuple[bool, str]:
    """Accept MM/DD/YYYY, YYYY-MM-DD, 'March 5 1985'. Return ISO YYYY-MM-DD."""
    v = _clean(value)
    if not v:
        return False, "Date of birth is required."

    v = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", v, flags=re.I).replace(",", "")

    parsed: date | None = None
    for fmt in (
        "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y",
        "%B %d %Y", "%b %d %Y", "%d %B %Y",
    ):
        try:
            parsed = datetime.strptime(v, fmt).date()
            break
        except ValueError:
            continue

    if parsed is None:
        return False, "I couldn't read that date. Please say it as month, day, and year."
    if parsed > date.today():
        return False, "That date is in the future. Please give a past date of birth."
    if parsed.year < 1900:
        return False, "That year seems too far back. Please repeat the year."
    return True, parsed.isoformat()


def dob_to_spoken(iso: str) -> str:
    """YYYY-MM-DD -> 'March 5, 1985' for reading back to the caller."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        return iso


def dob_to_us(iso: str) -> str:
    """YYYY-MM-DD -> MM/DD/YYYY for API responses."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y")
    except (ValueError, TypeError):
        return iso


# --- Phone ---------------------------------------------------------------

_WORD_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
}


def normalize_phone(value) -> str:
    """Strip everything but digits, handle spoken digits and +1 country code."""
    v = _clean(value).lower()
    for word, digit in _WORD_DIGITS.items():
        v = re.sub(rf"\b{word}\b", digit, v)
    digits = re.sub(r"\D", "", v)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def validate_phone(value, field: str = "Phone number") -> tuple[bool, str]:
    digits = normalize_phone(value)
    if not digits:
        return False, f"{field} is required."
    if len(digits) != 10:
        return False, (
            f"{field} needs to be 10 digits including the area code — "
            f"I heard {len(digits)}."
        )
    # Area codes never begin with 0 or 1. The exchange code is deliberately
    # NOT checked so the reserved 555-01xx demo range stays usable.
    if digits[0] in "01":
        return False, f"That isn't a valid U.S. {field.lower()}. Please repeat it."
    return True, digits


def phone_to_display(digits: str | None) -> str | None:
    if not digits or len(digits) != 10:
        return digits
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


# --- Address -------------------------------------------------------------

def validate_state(value) -> tuple[bool, str]:
    v = _clean(value)
    if not v:
        return False, "State is required."
    # "C. A." or "C A" spelled out
    compact = re.sub(r"[^A-Za-z]", "", v).upper()
    if len(compact) == 2 and compact in US_STATES:
        return True, compact
    named = STATE_NAMES.get(v.lower())
    if named:
        return True, named
    return False, "I didn't recognise that state. Please say the full state name."


def validate_zip(value) -> tuple[bool, str]:
    v = re.sub(r"[^0-9-]", "", _clean(value))
    if len(v) == 9 and v.isdigit():
        v = f"{v[:5]}-{v[5:]}"
    if not ZIP_RE.match(v):
        return False, "A ZIP code is five digits. Please repeat it one digit at a time."
    return True, v


def validate_city(value) -> tuple[bool, str]:
    v = _clean(value)
    if not v:
        return False, "City is required."
    if len(v) > 100:
        return False, "City must be 100 characters or fewer."
    return True, v.title()


def validate_address_line(value, required: bool = True) -> tuple[bool, str]:
    v = _clean(value)
    if not v:
        return (False, "Street address is required.") if required else (True, "")
    if len(v) > 200:
        return False, "Street address is too long."
    return True, v


# --- Other ---------------------------------------------------------------

def validate_sex(value) -> tuple[bool, str]:
    v = _clean(value)
    if v in SEX_VALUES:
        return True, v
    mapped = SEX_ALIASES.get(v.lower())
    if mapped:
        return True, mapped
    return False, "Please answer male, female, other, or decline to answer."


def validate_email(value, required: bool = False) -> tuple[bool, str]:
    v = _clean(value).lower()
    if not v:
        return (False, "Email is required.") if required else (True, "")
    for pattern, repl in _SPOKEN_EMAIL:
        v = re.sub(pattern, repl, v)
    v = v.replace(" ", "")
    if len(v) > 254 or not EMAIL_RE.match(v):
        return False, "That email doesn't look right. Could you spell it for me?"
    return True, v


def validate_optional_text(value, field: str, max_len: int) -> tuple[bool, str]:
    v = _clean(value)
    if not v:
        return True, ""
    if len(v) > max_len:
        return False, f"{field} must be {max_len} characters or fewer."
    return True, v


def validate_member_id(value) -> tuple[bool, str]:
    v = re.sub(r"[^A-Za-z0-9-]", "", _clean(value)).upper()
    if not v:
        return True, ""
    if len(v) > 50:
        return False, "That member ID is too long."
    return True, v


# --- Whole-record validation --------------------------------------------

REQUIRED_FIELDS = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
]

_FIELD_VALIDATORS = {
    "first_name": lambda v: validate_name(v, "First name"),
    "last_name": lambda v: validate_name(v, "Last name"),
    "date_of_birth": validate_dob,
    "sex": validate_sex,
    "phone_number": lambda v: validate_phone(v, "Phone number"),
    "email": lambda v: validate_email(v, required=False),
    "address_line_1": lambda v: validate_address_line(v, required=True),
    "address_line_2": lambda v: validate_address_line(v, required=False),
    "city": validate_city,
    "state": validate_state,
    "zip_code": validate_zip,
    "insurance_provider": lambda v: validate_optional_text(v, "Insurance provider", 120),
    "insurance_member_id": validate_member_id,
    "preferred_language": lambda v: validate_optional_text(v, "Preferred language", 50),
    "emergency_contact_name": lambda v: (
        (True, "") if not _clean(v) else validate_name(v, "Emergency contact name")
    ),
    "emergency_contact_phone": lambda v: (
        (True, "") if not _clean(v)
        else validate_phone(v, "Emergency contact phone")
    ),
}


def validate_field(field: str, value) -> tuple[bool, str]:
    """Validate one field by name. Unknown fields pass through unchanged."""
    fn = _FIELD_VALIDATORS.get(field)
    if fn is None:
        return True, _clean(value)
    return fn(value)


def validate_patient_payload(
    payload: dict, partial: bool = False
) -> tuple[dict, dict[str, str]]:
    """Validate and normalise a whole patient dict.

    Returns (cleaned, errors). `partial=True` (for PUT) skips the
    required-field check for keys that were not supplied.
    """
    cleaned: dict = {}
    errors: dict[str, str] = {}

    for field, raw in payload.items():
        if field not in _FIELD_VALIDATORS:
            continue
        ok, result = validate_field(field, raw)
        if ok:
            if result != "" or field not in REQUIRED_FIELDS:
                cleaned[field] = result or None
        else:
            errors[field] = result

    if not partial:
        for field in REQUIRED_FIELDS:
            if not cleaned.get(field) and field not in errors:
                errors[field] = f"{field.replace('_', ' ').capitalize()} is required."

    if cleaned.get("preferred_language") in (None, ""):
        cleaned["preferred_language"] = "English"

    return cleaned, errors
