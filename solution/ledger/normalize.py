"""Pure value-normalisation rules from the spec. No I/O, no DB: easy to unit-test."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))

_HEADER_SEP = re.compile(r"[_\s]+")
_NON_DIGIT = re.compile(r"\D")
_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
_DECIMAL_BODY = re.compile(r"^\d+(?:\.\d+)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y")
_DATE_SHAPES = (
    re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
    re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"),
)


class Invalid(ValueError):
    """Raised when a present value fails its type rule."""


def normalize_header(h: str) -> str:
    return _HEADER_SEP.sub(" ", (h or "").strip().lower())


def normalize_phone(raw: str | None) -> str | None:
    """Return canonical +91XXXXXXXXXX, or None if invalid. Caller handles 'missing'."""
    if raw is None:
        return None
    d = _NON_DIGIT.sub("", str(raw))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "+91" + d
    return None


def parse_date(raw: str) -> date:
    s = raw.strip()
    for fmt, shape in zip(_DATE_FORMATS, _DATE_SHAPES):
        if shape.match(s):
            try:
                return datetime.strptime(s, fmt).date()  # %b is case-insensitive
            except ValueError as e:  # e.g. 31/02
                raise Invalid(f"impossible date {raw!r}") from e
    raise Invalid(f"unrecognised date {raw!r}")


def parse_decimal(raw: str) -> Decimal:
    s = _CURRENCY_PREFIX.sub("", raw.strip()).replace(",", "").strip()
    if not _DECIMAL_BODY.match(s):  # also rejects negatives, "N/A", "-"
        raise Invalid(f"bad decimal {raw!r}")
    try:
        return Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as e:
        raise Invalid(f"bad decimal {raw!r}") from e


def parse_enum(raw: str, values: list[str]) -> str:
    v = raw.strip().lower()
    if v in {x.lower() for x in values}:
        return v
    raise Invalid(f"{raw!r} not in {values}")


def parse_timestamp(raw: str) -> datetime:
    """UTC 'Z', explicit offset, or naive (= IST). Returns an aware UTC datetime."""
    s = str(raw).strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def to_ist_iso(dt_utc: datetime) -> str:
    return dt_utc.astimezone(IST).isoformat(timespec="seconds")


def mask_phone(canonical: str) -> str:
    return "+91******" + canonical[-4:]
