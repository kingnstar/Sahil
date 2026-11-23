from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import math
import re
from urllib.parse import urlparse

from pdp.config import LIMITS
from .errors import AppError


class FieldType(str, Enum):
    TEXT = "TEXT"
    WHOLE = "WHOLE"
    NUMBER = "NUMBER"
    DATE = "DATE"
    DATETIME = "DATETIME"
    YESNO = "YESNO"
    CHOICE = "CHOICE"
    EMAIL = "EMAIL"
    URL = "URL"


def _bad(message="Invalid value"):
    raise AppError("validation_error", message)


def parse_value(value, field_type: FieldType | str, options=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        kind = field_type if isinstance(field_type, FieldType) else FieldType(str(field_type).upper())
    except ValueError:
        _bad("Unknown field type")
    text = str(value).strip()
    if kind == FieldType.TEXT:
        cleaned = "".join(ch for ch in text if ord(ch) >= 32 and ord(ch) != 127)
        if len(cleaned) > LIMITS.text_chars:
            _bad(f"Text must be at most {LIMITS.text_chars} characters")
        return cleaned
    if kind == FieldType.WHOLE:
        if not re.fullmatch(r"[+-]?[0-9]+", text, flags=re.ASCII):
            _bad("Enter a whole number using ASCII digits")
        result = int(text)
        if abs(result) > 999_999_999_999:
            _bad("Whole number is out of range")
        return result
    if kind == FieldType.NUMBER:
        try:
            number = Decimal(text)
        except InvalidOperation:
            _bad("Enter a valid number")
        if not number.is_finite() or abs(number) >= Decimal("1e15"):
            _bad("Number is out of range")
        result = float(number)
        if not math.isfinite(result):
            _bad("Number is out of range")
        return result
    if kind == FieldType.DATE:
        parsed = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                pass
        if parsed is None:
            match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", text)
            if match:
                a, b, year = map(int, match.groups())
                year += 2000 if year <= 69 else 1900
                try:
                    parsed = date(year, b, a)
                except ValueError:
                    _bad("Enter a valid date")
            else:
                _bad("Use DD/MM/YYYY, YYYY-MM-DD, or 14 Mar 2026")
        if parsed.year < LIMITS.min_year or parsed.year > LIMITS.max_year:
            _bad(f"Year must be between {LIMITS.min_year} and {LIMITS.max_year}")
        return parsed.isoformat()
    if kind == FieldType.DATETIME:
        try:
            parsed_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
                try:
                    parsed_dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    parsed_dt = None
            if parsed_dt is None:
                _bad("Enter a valid date and time")
        if parsed_dt.year < LIMITS.min_year or parsed_dt.year > LIMITS.max_year:
            _bad(f"Year must be between {LIMITS.min_year} and {LIMITS.max_year}")
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        return parsed_dt.astimezone(timezone.utc).isoformat()
    if kind == FieldType.YESNO:
        normalized = text.casefold()
        if normalized in {"yes", "y", "true", "1"}:
            return True
        if normalized in {"no", "n", "false", "0"}:
            return False
        _bad("Enter yes or no")
    if kind == FieldType.CHOICE:
        allowed = options or []
        found = next((item for item in allowed if item.casefold() == text.casefold()), None)
        if found is None:
            _bad("Choose one of the listed options")
        return found
    if kind == FieldType.EMAIL:
        if len(text) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
            _bad("Enter a valid email address")
        return text
    if kind == FieldType.URL:
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            _bad("URL must start with http:// or https://")
        return text
    _bad()


def validate_display_name(value):
    if not isinstance(value, str) or not 2 <= len(value) <= 60:
        _bad("Display name must be 2–60 letters, numbers, or spaces")
    if not value.strip():
        _bad("Display name cannot be blank")
    if any(not (ch == " " or ch.isalnum()) for ch in value):
        _bad("Display name may contain only letters, numbers, and spaces")
    if value.strip().isdigit():
        _bad("Display name cannot contain only digits")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        _bad("Control characters are not allowed")
    return value.strip()


def ambiguous_slash_date(value):
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*", value)
    if not match:
        return False
    first, second = int(match.group(1)), int(match.group(2))
    return first <= 12 and second <= 12 and first != second


def validate_name(value, minimum=1, maximum=60):
    if not isinstance(value, str):
        _bad("Name is required")
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _bad(f"Name must be {minimum}–{maximum} characters and contain no control characters")
    return value
