"""Working-day calendar of the simulator: the API's calendar (`app.calendar`), on the simulator's clock.

M7-1: the simulator used to count working days on UTC days with conventions of its own; the API counts them on the
calendar days of `config/calendar.yaml`'s time zone (Asia/Dubai). Now there is one calendar. `WorkingCalendar` here wraps
an `app.calendar.WorkingCalendar` (weekend, holidays, time zone: `api`) and:

* the simulation clock is a float number of days since 00:00 LOCAL time (in the calendar's zone) of the start date, so
  the day index `floor(t)` IS the local calendar day the API would use (`start_dt` is that instant). The zone must keep
  one UTC offset over the run (Asia/Dubai has no daylight saving); a zone that does not is refused.
* the date API (`add_working_days`, `working_days_between`, `is_working_day`) is the API's own functions;
* the clock API (`add_working_days_t`, `working_days_between_t`) has the API's semantics on the float clock, backed by a
  lazily extended table of working-day indices for speed. tests/test_sim_calendar.py checks it against the API's
  `add_working_days` on datetimes for every start in a fortnight and many n:
    - `add_working_days_t(t, n)`: a start on a non-working day counts from 00:00 of the next working day; the time of day
      of that effective start is kept; `n == 0` returns the effective start.
    - `working_days_between_t(t1, t2)`: working days in (effective start day, end day], negative when t2 is earlier.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Iterable
from zoneinfo import ZoneInfo

from app.calendar import DEFAULT_TIMEZONE
from app.calendar import WorkingCalendar as ApiCalendar

DEFAULT_WEEKEND = (5, 6)  # Saturday, Sunday (Monday = 0)
WORKING_TIME_OF_DAY = 0.35  # fraction of a day (~08:24 local) at which staff work resumes
_OFFSET_CHECK_DAYS = 3 * 366  # the run's horizon over which the zone's UTC offset must not change


class WorkingCalendar:
    def __init__(
        self,
        start: date,
        weekend: Iterable[int] = DEFAULT_WEEKEND,
        holidays: Iterable[date | str] = (),
        tz: tzinfo | str = DEFAULT_TIMEZONE,
    ):
        self.start = start
        weekend = frozenset(int(w) for w in weekend)
        if any(w < 0 or w > 6 for w in weekend):
            raise ValueError("weekend weekdays must be in 0..6")
        if len(weekend) >= 7:
            raise ValueError("a calendar needs at least one working weekday")
        holidays = frozenset(h if isinstance(h, date) else date.fromisoformat(str(h)) for h in holidays)
        self.api = ApiCalendar(weekend, holidays, ZoneInfo(tz) if isinstance(tz, str) else tz)
        self.weekend, self.holidays, self.tz = self.api.weekend, self.api.holidays, self.api.tz
        self.start_dt = datetime.combine(start, time.min, self.tz)
        offset = self.start_dt.utcoffset()
        for d in range(0, _OFFSET_CHECK_DAYS, 7):
            if (self.start_dt + timedelta(days=d)).utcoffset() != offset:
                raise ValueError(f"calendar time zone {self.tz} changes its UTC offset: the simulation clock needs a fixed one")
        self._wd: list[int] = []  # sorted working-day indices (days since `start`)
        self._built = -1  # last index examined

    @classmethod
    def from_api(cls, api: ApiCalendar, start: date) -> "WorkingCalendar":
        return cls(start, api.weekend, api.holidays, api.tz)

    # ---- date API: the API's functions ---------------------------------------------------
    def is_working_day(self, d: date | datetime) -> bool:
        return self.api.is_working_day(d)

    def add_working_days(self, d, n: int):
        """`app.calendar.WorkingCalendar.add_working_days` (n >= 0). A negative n steps back over working days (tests only)."""
        if n >= 0:
            return self.api.add_working_days(d, n)
        remaining = -n
        while remaining:
            d -= timedelta(days=1)
            if self.is_working_day(d):
                remaining -= 1
        return d

    def working_days_between(self, a, b) -> int:
        return self.api.working_days_between(a, b)

    # ---- simulation-clock API (float days since `start_dt`) --------------------------------
    def at(self, t: float) -> datetime:
        """The instant of simulation time `t`."""
        return self.start_dt + timedelta(days=t)

    def t_of(self, when: datetime) -> float:
        """Simulation time of an instant (the inverse of `at`)."""
        return (when - self.start_dt) / timedelta(days=1)

    def _extend(self, upto: int) -> None:
        while self._built < upto:
            self._built += 1
            if self.api.is_working_day(self.start + timedelta(days=self._built)):
                self._wd.append(self._built)

    def is_working_index(self, i: int) -> bool:
        self._extend(i)
        k = bisect_right(self._wd, i)
        return k > 0 and self._wd[k - 1] == i

    def working_ordinal(self, i: int) -> int:
        """Number of working days with index below `i` (so the first working day has ordinal 0)."""
        self._extend(i)
        return bisect_right(self._wd, i - 1)

    def _next_working_index(self, i: int) -> int:
        """`i` if it is a working day, else the first working day after it."""
        self._extend(i)
        while True:
            k = bisect_right(self._wd, i - 1)
            if k < len(self._wd):
                return self._wd[k]
            self._extend(self._built + 64)

    def working_index_after(self, i: int) -> int:
        """Index of the first working day strictly after day index `i`."""
        return self._next_working_index(i + 1)

    def date_of(self, t: float) -> date:
        return self.start + timedelta(days=math.floor(t))

    def _effective(self, t: float) -> tuple[int, float]:
        """(day index, time of day) of the effective start of a count from `t` (the API's `_effective_start`)."""
        i = math.floor(t)
        if self.is_working_index(i):
            return i, t - i
        return self._next_working_index(i), 0.0

    def add_working_days_t(self, t: float, n: int) -> float:
        """Time `n` working days after `t`, with the API's semantics (n >= 0)."""
        if n < 0:
            raise ValueError("cannot add a negative number of working days")
        i, frac = self._effective(t)
        if n == 0:
            return i + frac
        while True:
            k = bisect_right(self._wd, i)  # the position after day i (a working day, so _wd[k - 1] == i)
            if k + n - 1 < len(self._wd):
                return self._wd[k + n - 1] + frac
            self._extend(self._built + max(64, 2 * n))

    def working_days_between_t(self, t1: float, t2: float) -> int:
        """Working days in (effective start day of t1, day of t2]; negative when t2 is earlier."""
        s, _ = self._effective(t1)
        e = math.floor(t2)
        lo, hi, sign = (s, e, 1) if e >= s else (e, s, -1)
        self._extend(hi)
        return sign * (bisect_right(self._wd, hi) - bisect_right(self._wd, lo))

    def next_working_t(self, t: float) -> float:
        """`t` if it falls on a working day, else the start of the next working day (+ working hours)."""
        i = math.floor(t)
        if self.is_working_index(i):
            return t
        return self._next_working_index(i) + WORKING_TIME_OF_DAY
