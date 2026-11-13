from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from ledger.normalize import (Invalid, mask_phone, normalize_header, normalize_phone, parse_date,
                              parse_decimal, parse_enum, parse_timestamp, to_ist_iso)


@pytest.mark.parametrize("raw", [
    "9876543210", "+91 98765 43210", "09876543210", "91-98765-43210", "(+91) 9876543210",
    "+919876543210", " 98765-43210 ",
])
def test_phone_accepted_formats(raw):
    assert normalize_phone(raw) == "+919876543210"


@pytest.mark.parametrize("raw", [
    "987654321",          # 9 digits
    "5876543210",         # bad leading digit
    "022-2345-6789",      # landline
    "N/A",
    "+1 415 555 0134",
    "919876543210 1",     # 13 digits
    "1919876543210",
    "00919876543210",     # 14 digits
    "",
])
def test_phone_rejected(raw):
    assert normalize_phone(raw) is None


def test_phone_91_prefix_only_dropped_when_12_digits():
    # 10-digit number that itself starts with 91 is not a valid mobile (starts with 9 though)
    assert normalize_phone("9123456789") == "+919123456789"
    assert normalize_phone("919123456789") == "+919123456789"


def test_mask():
    assert mask_phone("+919876543210") == "+91******3210"


@pytest.mark.parametrize("raw,expected", [
    ("2026-12-01", date(2026, 12, 1)),
    ("01/12/2026", date(2026, 12, 1)),     # day-first
    ("01-12-2026", date(2026, 12, 1)),
    ("01-Dec-2026", date(2026, 12, 1)),
    ("01-DEC-2026", date(2026, 12, 1)),
    ("01-dec-2026", date(2026, 12, 1)),
    ("29/02/2028", date(2028, 2, 29)),     # leap year
])
def test_dates(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "2026-13-01", "soon", "TBD", "29/02/2026",
                                 "12/01/26", "2026/12/01", "01-December-2026"])
def test_bad_dates(raw):
    with pytest.raises(Invalid):
        parse_date(raw)


@pytest.mark.parametrize("raw,expected", [
    ("12000", "12000.00"), ("12,345.678", "12345.68"), ("₹12,345", "12345.00"),
    ("Rs. 24787", "24787.00"), ("Rs24787", "24787.00"), ("rs 5", "5.00"), ("INR 18678.3", "18678.30"),
    ("inr18678.3", "18678.30"), ("0", "0.00"), ("1.005", "1.01"),
])
def test_decimal(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["N/A", "-", "twelve thousand", "-5", "Rs. -5", "1.2.3", "USD 5"])
def test_bad_decimal(raw):
    with pytest.raises(Invalid):
        parse_decimal(raw)


def test_enum():
    assert parse_enum("HI", ["en", "hi"]) == "hi"
    with pytest.raises(Invalid):
        parse_enum("Hindi", ["en", "hi"])


def test_headers():
    assert normalize_header("  Mobile__No ") == "mobile no"
    assert normalize_header("Insured   Name") == "insured name"
    assert normalize_header("customer_name") == "customer name"


@pytest.mark.parametrize("raw", ["2026-10-03T04:31:00Z", "2026-10-03T10:01:00+05:30", "2026-10-03 10:01:00"])
def test_timestamps_all_mean_the_same_instant(raw):
    assert parse_timestamp(raw) == datetime(2026, 10, 3, 4, 31, tzinfo=timezone.utc)


def test_ist_date_crosses_midnight():
    # 20:00Z is 01:30 next day IST: report dates must use IST
    ts = parse_timestamp("2026-10-03T20:00:00Z")
    assert to_ist_iso(ts) == "2026-10-04T01:30:00+05:30"
