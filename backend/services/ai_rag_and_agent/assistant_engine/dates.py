"""Natural-language periods → exact UTC ranges in the organization's time zone (decision D-027).

``parse_periods`` finds every period a question names, in order: "today", "yesterday", "this/last week|month|quarter|
year", "last 7 days", "past 3 months", "October", "October 2025", "Q3", "third quarter of 2025", "2025",
"between 2026-10-01 and 2026-10-15", "on 2026-10-12", "since September". Calendar periods start at local midnight;
periods that include today end now. ``until`` is exclusive.

``previous_period`` gives the comparison baseline: the previous calendar unit (month-over-month) or the same unit one
year earlier (year-over-year). A question without a period gets the default "last 30 days", and the answer says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

Kind = Literal["day", "week", "month", "quarter", "year", "rolling", "custom"]

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
ORDINAL_QUARTERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
DEFAULT_DAYS = 30


@dataclass(frozen=True, slots=True)
class Period:
    since: datetime  # UTC, inclusive
    until: datetime  # UTC, exclusive
    label: str
    kind: Kind = "custom"
    anchor: date | None = None  # first local day of a calendar period

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "label": self.label,
            "kind": self.kind,
        }
        if self.anchor is not None:
            value["anchor"] = self.anchor.isoformat()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Period:
        anchor = value.get("anchor")
        return cls(
            datetime.fromisoformat(value["since"]),
            datetime.fromisoformat(value["until"]),
            str(value.get("label", "the chosen period")),
            value.get("kind", "custom"),
            date.fromisoformat(anchor) if anchor else None,
        )


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - an unknown time zone name falls back to UTC
        return ZoneInfo("UTC")


def _start(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tz).astimezone(UTC)


def _cap(until: datetime, now: datetime) -> datetime:
    """A period that includes today ends now (plus one second, so records written this second count)."""
    return min(until, now + timedelta(seconds=1))


def _month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month(year: int, month_number: int, tz: ZoneInfo, now: datetime, label: str | None = None) -> Period:
    first = date(year, month_number, 1)
    ny, nm = _month_add(year, month_number, 1)
    until = _cap(_start(date(ny, nm, 1), tz), now)
    return Period(_start(first, tz), until, label or f"{MONTH_NAMES[month_number]} {year}", "month", first)


def quarter(year: int, q: int, tz: ZoneInfo, now: datetime, label: str | None = None) -> Period:
    first = date(year, 3 * (q - 1) + 1, 1)
    ny, nm = _month_add(year, first.month, 3)
    until = _cap(_start(date(ny, nm, 1), tz), now)
    return Period(_start(first, tz), until, label or f"Q{q} {year}", "quarter", first)


def year(value: int, tz: ZoneInfo, now: datetime, label: str | None = None) -> Period:
    first = date(value, 1, 1)
    until = _cap(_start(date(value + 1, 1, 1), tz), now)
    return Period(_start(first, tz), until, label or str(value), "year", first)


def week(monday: date, tz: ZoneInfo, now: datetime, label: str) -> Period:
    return Period(_start(monday, tz), _cap(_start(monday + timedelta(days=7), tz), now), label, "week", monday)


def day(value: date, tz: ZoneInfo, now: datetime, label: str | None = None) -> Period:
    return Period(
        _start(value, tz), _cap(_start(value + timedelta(days=1), tz), now), label or value.isoformat(), "day", value
    )


def rolling(days: float, now: datetime, label: str) -> Period:
    return Period(now - timedelta(days=days), now + timedelta(seconds=1), label, "rolling")


def default_period(now: datetime) -> Period:
    return rolling(DEFAULT_DAYS, now, f"the last {DEFAULT_DAYS} days")


_ISO = r"(\d{4}-\d{2}-\d{2})"
_MONTH_WORD = "|".join(sorted((m for m in MONTHS if m != "may"), key=len, reverse=True))
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("range", re.compile(rf"\b(?:between|from)\s+{_ISO}\s+(?:and|to|until|till)\s+{_ISO}\b")),
    ("iso_day", re.compile(rf"\b{_ISO}\b")),
    ("today", re.compile(r"\btoday\b")),
    ("yesterday", re.compile(r"\byesterday\b")),
    ("relative_unit", re.compile(r"\b(this|current|last|previous|past)\s+(week|month|quarter|year)\b")),
    ("rolling", re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(hour|day|week|month|year)s?\b")),
    ("quarter", re.compile(r"\bq([1-4])(?:\s*(?:of\s+)?(\d{4}))?\b")),
    ("ordinal_quarter", re.compile(r"\b(first|second|third|fourth)\s+quarter(?:\s+(?:of\s+)?(\d{4}))?\b")),
    ("month", re.compile(rf"\b({_MONTH_WORD})\b(?:\s+(\d{{4}}))?")),
    ("may", re.compile(r"\b(?:(?:in|during|of|for|since|from)\s+may\b|may\s+(\d{4}))")),
    ("year", re.compile(r"\b(?:in|during|for|of|year)\s+(\d{4})\b|\b(19\d\d|20\d\d)\b")),
]
_SINCE = re.compile(r"\bsince\s+")


def _resolve(kind: str, m: re.Match[str], now: datetime, tz: ZoneInfo) -> Period | None:
    today = now.astimezone(tz).date()
    if kind == "range":
        start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        if end < start:
            start, end = end, start
        return Period(
            _start(start, tz), _cap(_start(end + timedelta(days=1), tz), now), f"{start} to {end}", "custom", start
        )
    if kind == "iso_day":
        return day(date.fromisoformat(m.group(1)), tz, now)
    if kind == "today":
        return day(today, tz, now, "today")
    if kind == "yesterday":
        return day(today - timedelta(days=1), tz, now, "yesterday")
    if kind == "relative_unit":
        which, unit = m.group(1), m.group(2)
        back = which in ("last", "previous", "past")
        if unit == "week":
            monday = today - timedelta(days=today.weekday()) - (timedelta(days=7) if back else timedelta())
            return week(monday, tz, now, "last week" if back else "this week")
        if unit == "month":
            y, mo = _month_add(today.year, today.month, -1 if back else 0)
            return month(y, mo, tz, now, f"last month ({MONTH_NAMES[mo]} {y})" if back else "this month")
        if unit == "quarter":
            q = (today.month - 1) // 3 + 1
            y = today.year
            if back:
                q, y = (4, y - 1) if q == 1 else (q - 1, y)
            return quarter(y, q, tz, now, f"last quarter (Q{q} {y})" if back else "this quarter")
        y = today.year - 1 if back else today.year
        return year(y, tz, now, f"last year ({y})" if back else "this year")
    if kind == "rolling":
        n, unit = int(m.group(1)), m.group(2)
        if n < 1:
            return None
        days = {"hour": n / 24, "day": n, "week": 7 * n, "month": 30 * n, "year": 365 * n}[unit]
        return rolling(days, now, f"the last {n} {unit}{'s' if n != 1 else ''}")
    if kind in ("quarter", "ordinal_quarter"):
        q = int(m.group(1)) if kind == "quarter" else ORDINAL_QUARTERS[m.group(1)]
        y = int(m.group(2)) if m.group(2) else today.year
        if not m.group(2) and date(y, 3 * (q - 1) + 1, 1) > today:
            y -= 1
        return quarter(y, q, tz, now)
    if kind in ("month", "may"):
        if kind == "may":
            number, explicit = 5, m.group(1)
        else:
            number, explicit = MONTHS[m.group(1)], m.group(2)
        y = int(explicit) if explicit else today.year
        if not explicit and date(y, number, 1) > today:
            y -= 1  # "October" asked in March means last October
        return month(y, number, tz, now)
    if kind == "year":
        value = int(m.group(1) or m.group(2))
        if not 1990 <= value <= today.year + 1:
            return None
        return year(value, tz, now)
    return None


def parse_periods(text: str, now: datetime, time_zone: str) -> list[Period]:
    """Every period named in ``text``, in order of appearance (overlapping matches are taken once)."""
    lowered = text.lower()
    tz = _zone(time_zone)
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, Period]] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(lowered):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            period = _resolve(kind, m, now, tz)
            if period is None:
                continue
            taken.append((m.start(), m.end()))
            since_word = _SINCE.search(lowered, max(0, m.start() - 7), m.start() + 1)
            if since_word and since_word.end() == m.start():
                period = Period(period.since, now + timedelta(seconds=1), f"since {period.label}", "custom")
            found.append((m.start(), period))
    found.sort(key=lambda item: item[0])
    return [p for _, p in found]


def previous_period(period: Period, time_zone: str, now: datetime, *, year_over_year: bool = False) -> Period:
    """The baseline to compare ``period`` with."""
    tz = _zone(time_zone)
    a = period.anchor
    if year_over_year and a is not None:
        if period.kind == "month":
            return month(a.year - 1, a.month, tz, now)
        if period.kind == "quarter":
            return quarter(a.year - 1, (a.month - 1) // 3 + 1, tz, now)
        if period.kind == "year":
            return year(a.year - 1, tz, now)
    if year_over_year:
        shift = timedelta(days=365)
        return Period(period.since - shift, period.until - shift, "the same period a year earlier", "custom")
    if a is not None and period.kind == "month":
        y, mo = _month_add(a.year, a.month, -1)
        return month(y, mo, tz, now)
    if a is not None and period.kind == "quarter":
        q = (a.month - 1) // 3 + 1
        return quarter(a.year - 1, 4, tz, now) if q == 1 else quarter(a.year, q - 1, tz, now)
    if a is not None and period.kind == "year":
        return year(a.year - 1, tz, now)
    if a is not None and period.kind == "week":
        return week(a - timedelta(days=7), tz, now, "the week before")
    if a is not None and period.kind == "day":
        return day(a - timedelta(days=1), tz, now, "the day before")
    length = period.until - period.since
    return Period(period.since - length, period.since, "the period before", "custom")
