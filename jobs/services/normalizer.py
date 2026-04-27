"""
Shared data-normalisation utilities used by all job-board providers.

These helpers convert provider-specific strings/formats into the exact
types expected by the ``JobListing`` model.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from dateutil import parser as date_parser

from jobs.models import EmploymentType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------

# Matches patterns like: "£40k", "$75,000", "50000", "40k-60k", "USD 50000"
_SALARY_RE = re.compile(
    r"""
    (?:[$£€]|\bUSD\b|\bGBP\b|\bEUR\b)?   # optional currency symbol/code
    \s*
    (\d[\d,]*)                             # number with optional commas
    k?                                     # optional 'k' suffix (thousands)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_salary(raw: str | int | float | None) -> tuple[int | None, int | None]:
    """
    Parse a raw salary string or number into a (min, max) tuple.

    Examples::
        parse_salary("£40,000 - £60,000")  -> (40000, 60000)
        parse_salary("50k")                -> (50000, None)
        parse_salary(75000)                -> (75000, None)
        parse_salary(None)                 -> (None, None)
    """
    if raw is None:
        return None, None

    if isinstance(raw, (int, float)):
        val = int(raw)
        return (val, None) if val > 0 else (None, None)

    raw_str = str(raw).strip()
    if not raw_str:
        return None, None

    matches = _SALARY_RE.findall(raw_str)
    amounts: list[int] = []
    for m in matches:
        try:
            amount = int(m.replace(",", ""))
            if raw_str.lower().find("k", raw_str.lower().rfind(m)) != -1:
                amount *= 1000
            if amount > 0:
                amounts.append(amount)
        except ValueError:
            continue

    if not amounts:
        return None, None
    if len(amounts) == 1:
        return amounts[0], None
    return min(amounts), max(amounts)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def parse_posted_at(raw: str | None) -> datetime | None:
    """
    Parse a raw date string from any provider into a timezone-aware datetime.

    Returns None if parsing fails rather than raising.
    """
    if not raw:
        return None
    try:
        dt = date_parser.parse(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        logger.debug("Could not parse date: %r", raw)
        return None


# ---------------------------------------------------------------------------
# Employment type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bpart[\s-]?time\b", re.I), EmploymentType.PART_TIME),
    (re.compile(r"\bcontract\b|\bcontractor\b|\bfreelance\b", re.I), EmploymentType.CONTRACT),
    (re.compile(r"\bintern(ship)?\b", re.I), EmploymentType.INTERNSHIP),
    (re.compile(r"\bfull[\s-]?time\b", re.I), EmploymentType.FULL_TIME),
]


def employment_type_from_string(raw: str | None) -> str:
    """
    Map a raw employment-type string to a JobListing EmploymentType choice.

    Defaults to FULL_TIME when the string is absent or unrecognised.
    """
    if not raw:
        return EmploymentType.FULL_TIME
    for pattern, choice in _TYPE_MAP:
        if pattern.search(raw):
            return choice
    return EmploymentType.FULL_TIME


# ---------------------------------------------------------------------------
# Remote detection
# ---------------------------------------------------------------------------

_REMOTE_RE = re.compile(
    r"\bremote\b|\bwork from home\b|\bwfh\b|\banywhere\b",
    re.IGNORECASE,
)


def is_remote(title: str = "", description: str = "", location: str = "") -> bool:
    """Return True when any of the provided fields indicate a remote position."""
    combined = f"{title} {description} {location}"
    return bool(_REMOTE_RE.search(combined))
