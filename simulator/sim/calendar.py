"""Working-day calendar.

Date API (`add_working_days`, `working_days_between`) works on `datetime.date`. The simulator clock is
a float number of days since the simulation start date, so there is a parallel index/time API
(`*_t`) backed by a lazily extended table of working-day indices.

Conventions
  * `add_working_days(d, 0)` is `d` itself, even when `d` is not a working day.
  * `add_working_days(d, n)` for n > 0 is the n-th working day strictly after `d`; for n < 0 the
    n-th working day strictly before `d`.
  * `working_days_between(a, b)` is the number of working days in the half-open interval (a, b]
    (negative when b < a), so `working_days_between(a, add_working_days(a, n)) == n` for n >= 0.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, timedelta
from typing import Iterable

DEFAULT_WEEKEND = (5, 6)  # Saturday, Sunday (Monday = 0)
WORKING_TIME_OF_DAY = 0.35  # fraction of a day (~08:24) at which staff work resumes


class WorkingCalendar:
    def __init__(
        self,
        start: date,
        weekend: Iterable[int] = DEFAULT_WEEKEND,
        holidays: Iterable[date | str] = (),
    ):
        self.start = start
        self.weekend = frozenset(int(w) for w in weekend)
        if any(w < 0 or w > 6 for w in self.weekend):
            raise ValueError("weekend weekdays must be in 0..6")
        if len(self.weekend) >= 7:
            raise ValueError("a calendar needs at least one working weekday")
        self.holidays = frozenset(
            h if isinstance(h, date) else date.fromisoformat(str(h)) for h in holidays
        )
        self._wd: list[int] = []  # sorted working-day indices (days since `start`)
        self._built = -1  # last index examined

    # ---- date API ------------------------------------------------------------------------
    def is_working_day(self, d: date) -> bool:
        return d.weekday() not in self.weekend and d not in self.holidays

    def add_working_days(self, d: date, n: int) -> date:
        step = 1 if n >= 0 else -1
        remaining = abs(n)
        while remaining:
            d += timedelta(days=step)
            if self.is_working_day(d):
                remaining -= 1
        return d

    def working_days_between(self, a: date, b: date) -> int:
        if a == b:
            return 0
        if b < a:
            return -self.working_days_between(b, a)
        count, d = 0, a
        while d < b:
            d += timedelta(days=1)
            if self.is_working_day(d):
                count += 1
        return count

    # ---- simulation-clock API (float days since `start`) -----------------------------------
    def _extend(self, upto: int) -> None:
        while self._built < upto:
            self._built += 1
            if self.is_working_day(self.start + timedelta(days=self._built)):
                self._wd.append(self._built)

    def is_working_index(self, i: int) -> bool:
        self._extend(i)
        k = bisect_right(self._wd, i)
        return k > 0 and self._wd[k - 1] == i

    def date_of(self, t: float) -> date:
        return self.start + timedelta(days=math.floor(t))

    def add_working_days_t(self, t: float, n: int) -> float:
        """Time `n` working days after `t`, keeping the time of day (n >= 0)."""
        if n == 0:
            return t
        i = math.floor(t)
        frac = t - i
        # grow the table until it holds the n-th working day after i
        self._extend(i + 1)
        while True:
            k = bisect_right(self._wd, i)
            if k + n - 1 < len(self._wd):
                return self._wd[k + n - 1] + frac
            self._extend(self._built + max(64, 2 * n))

    def working_days_between_t(self, t1: float, t2: float) -> int:
        i1, i2 = math.floor(t1), math.floor(t2)
        if i1 == i2:
            return 0
        lo, hi = (i1, i2) if i2 > i1 else (i2, i1)
        self._extend(hi)
        n = bisect_right(self._wd, hi) - bisect_right(self._wd, lo)
        return n if i2 > i1 else -n

    def next_working_t(self, t: float) -> float:
        """`t` if it falls on a working day, else the start of the next working day (+ working hours)."""
        i = math.floor(t)
        if self.is_working_index(i):
            return t
        return self.add_working_days_t(float(i), 1) + WORKING_TIME_OF_DAY
