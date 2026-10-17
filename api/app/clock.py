"""Injectable simulation clock. All timers must read time from here, never datetime.now()."""

from datetime import UTC, datetime, timedelta

from app.config import settings


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


class SimClock:
    def __init__(self, start: datetime):
        self._start = start
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, days: float = 0, hours: float = 0) -> datetime:
        if days < 0 or hours < 0:
            raise ValueError("SimClock only moves forward; use reset() to rewind")
        self._now = self._now + timedelta(days=days, hours=hours)
        return self._now

    def reset(self) -> datetime:
        self._now = self._start
        return self._now


clock = SimClock(_parse(settings.sim_clock_start))
