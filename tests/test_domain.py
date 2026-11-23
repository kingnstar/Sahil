import pytest
from pdp.domain.errors import AppError
from pdp.domain.types import FieldType, parse_value, validate_display_name, validate_name


@pytest.mark.parametrize("value,kind,expected", [
    ("", "TEXT", None), (None, "TEXT", None), ("hello", "TEXT", "hello"),
    ("-0", "WHOLE", 0), ("+12", "WHOLE", 12), ("999999999999", "WHOLE", 999999999999),
    ("-999999999999", "WHOLE", -999999999999), ("0", "NUMBER", 0.0),
    ("12.5", "NUMBER", 12.5), ("1e3", "NUMBER", 1000.0),
    ("yes", "YESNO", True), ("Y", "YESNO", True), ("true", "YESNO", True),
    ("1", "YESNO", True), ("no", "YESNO", False), ("N", "YESNO", False),
    ("false", "YESNO", False), ("0", "YESNO", False),
    ("01/02/2026", "DATE", "2026-02-01"), ("2026-03-14", "DATE", "2026-03-14"),
    ("14 Mar 2026", "DATE", "2026-03-14"), ("01/02/69", "DATE", "2069-02-01"),
    ("01/02/70", "DATE", "1970-02-01"), ("a@b.com", "EMAIL", "a@b.com"),
    ("https://example.com", "URL", "https://example.com"),
    ("a", "CHOICE", "A"),
])
def test_parse_valid_values(value, kind, expected):
    actual = parse_value(value, kind, ["A", "B"] if kind == "CHOICE" else None)
    assert actual == expected


@pytest.mark.parametrize("value,kind", [
    ("１２", "WHOLE"), ("1.0", "WHOLE"), ("1e4", "WHOLE"), ("1,000", "WHOLE"),
    ("1000000000000", "WHOLE"), ("NaN", "NUMBER"), ("Infinity", "NUMBER"),
    ("1e15", "NUMBER"), ("not-number", "NUMBER"), ("31/02/2024", "DATE"),
    ("1800-01-01", "DATE"), ("2101-01-01", "DATE"), ("not-a-date", "DATE"),
    ("maybe", "YESNO"), ("x@y", "EMAIL"), ("httpsx://example.com", "URL"),
    ("file:///etc/passwd", "URL"), ("C", "CHOICE"), ("nonsense", "UNKNOWN"),
])
def test_parse_rejects_invalid_values(value, kind):
    with pytest.raises(AppError):
        parse_value(value, kind, ["A", "B"])


def test_datetime_normalizes_to_utc():
    assert parse_value("2026-01-01T04:00:00+04:00", "DATETIME") == "2026-01-01T00:00:00+00:00"
    assert parse_value("2026-01-01T00:00:00Z", "DATETIME") == "2026-01-01T00:00:00+00:00"
    assert parse_value("value", FieldType.TEXT) == "value"


@pytest.mark.parametrize("value", ["2026-01-01T25:00:00", "not datetime", "1800-01-01T00:00:00"])
def test_datetime_rejects_invalid_or_out_of_range(value):
    with pytest.raises(AppError):
        parse_value(value, "DATETIME")


def test_text_strips_control_characters_and_enforces_length():
    assert parse_value("ab\x00cd\x7f", "TEXT") == "abcd"
    assert parse_value("a\nb\tc", "TEXT") == "abc"
    with pytest.raises(AppError):
        parse_value("x" * 4001, "TEXT")


@pytest.mark.parametrize("value", ["A", "1", "a\nb", "x" * 61, ""])
def test_display_name_validation(value):
    with pytest.raises(AppError):
        validate_display_name(value)


def test_display_name_valid_and_names_reject_controls():
    assert validate_display_name("Morgan 22") == "Morgan 22"
    with pytest.raises(AppError):
        validate_name("bad\x00name")
    with pytest.raises(AppError):
        validate_display_name("  ")
