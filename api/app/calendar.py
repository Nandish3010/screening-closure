"""Working-day calendar for SLA arithmetic. Pure: no clock reads, no I/O after the config load.

UAE default (ADR-009): Monday to Friday are working days, Saturday and Sunday are the weekend.
The weekend set, an optional holiday list and the calendar's time zone come from
`config/calendar.yaml`; nothing is hard-coded below except the fallback defaults.

Semantics (all tested in tests/test_calendar.py):

* The calendar day of an instant is its date in the calendar's time zone (Asia/Dubai).
* `add_working_days(start, n)` counts `n` working days after `start`. A start that falls on a
  non-working day "counts from" the next working day, whose first instant (00:00 local) is
  the effective start. The time of day of the effective start is kept, so a 2 working day SLA
  started Thursday 10:00 is due Monday 10:00. The result is always a working day.
* `working_days_between(start, end)` counts the working days in (effective start day, end day],
  so `working_days_between(start, add_working_days(start, n)) == n` for every start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

WEEKDAY_NAMES = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)
DEFAULT_TIMEZONE = "Asia/Dubai"
DEFAULT_WEEKEND = frozenset({5, 6})  # Saturday, Sunday (Monday is 0)


class CalendarError(ValueError):
    pass


@dataclass(frozen=True)
class WorkingCalendar:
    weekend: frozenset[int] = DEFAULT_WEEKEND
    holidays: frozenset[date] = frozenset()
    tz: tzinfo = field(default_factory=lambda: ZoneInfo(DEFAULT_TIMEZONE))

    def __post_init__(self) -> None:
        if not self.weekend <= set(range(7)):
            raise CalendarError(f"weekend days must be 0..6, got {sorted(self.weekend)}")
        if len(self.weekend) >= 7:
            raise CalendarError("weekend covers every weekday: the calendar has no working days")

    # -- day level ----------------------------------------------------------------------

    def local_date(self, when: date | datetime) -> date:
        """Calendar day of a date or instant. A naive datetime is taken as local time."""
        if isinstance(when, datetime):
            if when.tzinfo is None:
                return when.date()
            return when.astimezone(self.tz).date()
        return when

    def is_working_day(self, when: date | datetime) -> bool:
        d = self.local_date(when)
        return d.weekday() not in self.weekend and d not in self.holidays

    def next_working_day(self, d: date) -> date:
        """`d` itself if it is a working day, otherwise the next one."""
        while not self.is_working_day(d):
            d += timedelta(days=1)
        return d

    def _step(self, d: date, n: int) -> date:
        """The date n working days after the working day `d` (n >= 0). Memoised (pure in the weekend and holidays): the
        sweep and the simulator ask the same few (day, n) pairs again and again."""
        return _step_days(self.weekend, self.holidays, d, n)

    # -- instant level ------------------------------------------------------------------

    def _effective_start(self, start: date | datetime) -> date | datetime:
        if isinstance(start, datetime):
            if start.tzinfo is None:
                raise CalendarError("datetimes must be timezone-aware")
            local = start.astimezone(self.tz)
            if self.is_working_day(local):
                return local
            return datetime.combine(self.next_working_day(local.date()), time.min, self.tz)
        return self.next_working_day(start)

    def add_working_days(self, start: date | datetime, n: int) -> date | datetime:
        """`start` plus `n` working days; always lands on a working day."""
        if n < 0:
            raise CalendarError("cannot add a negative number of working days")
        eff = self._effective_start(start)
        if isinstance(eff, datetime):
            due = datetime.combine(self._step(eff.date(), n), eff.timetz())
            return due.astimezone(start.tzinfo)
        return self._step(eff, n)

    def working_days_between(self, start: date | datetime, end: date | datetime) -> int:
        """Working days in (effective start day, end day]; negative if `end` is earlier."""
        s = self.local_date(self._effective_start(start))
        e = self.local_date(end)
        lo, hi, sign = (s, e, 1) if e >= s else (e, s, -1)
        count, d = 0, lo
        while d < hi:
            d += timedelta(days=1)
            if self.is_working_day(d):
                count += 1
        return sign * count


@lru_cache(maxsize=1 << 16)
def _step_days(weekend: frozenset[int], holidays: frozenset[date], d: date, n: int) -> date:
    for _ in range(n):
        d += timedelta(days=1)
        while d.weekday() in weekend or d in holidays:
            d += timedelta(days=1)
    return d


def calendar_from_dict(raw: dict) -> WorkingCalendar:
    """Build a calendar from parsed YAML (see config/calendar.yaml). Raises CalendarError."""
    raw = raw or {}
    weekend_raw = raw.get("weekend", [WEEKDAY_NAMES[i] for i in sorted(DEFAULT_WEEKEND)])
    weekend = set()
    for name in weekend_raw:
        key = str(name).strip().lower()
        if key not in WEEKDAY_NAMES:
            raise CalendarError(f"unknown weekday {name!r} in weekend (use {WEEKDAY_NAMES})")
        weekend.add(WEEKDAY_NAMES.index(key))
    holidays = set()
    for item in raw.get("holidays") or []:
        value = item.get("date") if isinstance(item, dict) else item
        try:
            holidays.add(value if isinstance(value, date) else date.fromisoformat(str(value)))
        except ValueError as exc:
            raise CalendarError(f"bad holiday date {value!r}: {exc}") from exc
    tz_name = raw.get("timezone", DEFAULT_TIMEZONE)
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError
        raise CalendarError(f"unknown timezone {tz_name!r}") from exc
    return WorkingCalendar(frozenset(weekend), frozenset(holidays), tz)


def load_calendar(path: str | Path) -> WorkingCalendar:
    return calendar_from_dict(yaml.safe_load(Path(path).read_text()))


@lru_cache(maxsize=1)
def default_calendar() -> WorkingCalendar:
    """The configured calendar, loaded once per process from `settings.calendar_path`."""
    from app.config import settings

    return load_calendar(settings.calendar_path)


def is_working_day(when: date | datetime) -> bool:
    return default_calendar().is_working_day(when)


def add_working_days(start: date | datetime, n: int) -> date | datetime:
    return default_calendar().add_working_days(start, n)


def working_days_between(start: date | datetime, end: date | datetime) -> int:
    return default_calendar().working_days_between(start, end)
