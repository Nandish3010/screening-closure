from datetime import date, datetime, timedelta, timezone

import pytest

from app.calendar import default_calendar, load_calendar
from simulator.sim.calendar import WorkingCalendar
from simulator.sim.runner import REPO_ROOT

MON = date(2026, 1, 5)
DUBAI = timezone(timedelta(hours=4))


def test_weekend_is_saturday_sunday_by_default():
    cal = WorkingCalendar(MON)
    assert [cal.is_working_day(date(2026, 1, d)) for d in range(5, 12)] == [True] * 5 + [False] * 2


def test_add_working_days_is_the_apis():
    """M7-1: the date API is app.calendar's: a non-working start counts from the next working day."""
    cal = WorkingCalendar(MON)
    assert cal.add_working_days(date(2026, 1, 9), 1) == date(2026, 1, 12)  # Fri + 1 = Mon
    assert cal.add_working_days(date(2026, 1, 10), 1) == date(2026, 1, 13)  # Sat counts from Mon: + 1 = Tue
    assert cal.add_working_days(date(2026, 1, 5), 5) == date(2026, 1, 12)  # Mon + 5 = next Mon
    assert cal.add_working_days(date(2026, 1, 10), 0) == date(2026, 1, 12)  # zero from a Saturday: the effective start
    assert cal.add_working_days(date(2026, 1, 12), -1) == date(2026, 1, 9)  # Mon - 1 = Fri
    assert cal.add_working_days(date(2026, 1, 5), 10) == date(2026, 1, 19)


def test_working_days_between():
    cal = WorkingCalendar(MON)
    assert cal.working_days_between(date(2026, 1, 9), date(2026, 1, 12)) == 1  # Fri -> Mon
    assert cal.working_days_between(date(2026, 1, 5), date(2026, 1, 12)) == 5
    assert cal.working_days_between(date(2026, 1, 10), date(2026, 1, 11)) == -1  # Sat counts from Mon
    assert cal.working_days_between(date(2026, 1, 12), date(2026, 1, 9)) == -1
    assert cal.working_days_between(MON, MON) == 0


@pytest.mark.parametrize("offset", range(0, 14))
@pytest.mark.parametrize("n", [0, 1, 2, 5, 7, 14, 31, 45])
def test_between_inverts_add(offset, n):
    cal = WorkingCalendar(MON)
    d = date.fromordinal(MON.toordinal() + offset)
    assert cal.working_days_between(d, cal.add_working_days(d, n)) == n


def test_holidays_and_custom_weekend():
    cal = WorkingCalendar(MON, holidays=["2026-01-07"])
    assert not cal.is_working_day(date(2026, 1, 7))
    assert cal.add_working_days(date(2026, 1, 6), 1) == date(2026, 1, 8)  # Wed is a holiday
    gulf = WorkingCalendar(MON, weekend=(4, 5))  # Friday-Saturday weekend
    assert gulf.add_working_days(date(2026, 1, 8), 1) == date(2026, 1, 11)  # Thu + 1 = Sun
    with pytest.raises(ValueError):
        WorkingCalendar(MON, weekend=range(7))


def test_the_clock_starts_at_local_midnight_in_the_apis_time_zone():
    """M7-1: day index floor(t) is the Asia/Dubai calendar day, not the UTC day."""
    cal = WorkingCalendar(MON)
    assert cal.start_dt.utcoffset() == timedelta(hours=4)
    assert cal.at(0.0) == datetime(2026, 1, 5, 0, 0, tzinfo=DUBAI)
    assert cal.at(0.0).astimezone(timezone.utc) == datetime(2026, 1, 4, 20, 0, tzinfo=timezone.utc)
    assert cal.t_of(cal.at(12.375)) == pytest.approx(12.375)
    # 21:00 UTC on a Friday is already Saturday in Dubai: not a working day for the API, nor for the simulator
    friday_late = datetime(2026, 1, 9, 21, 0, tzinfo=timezone.utc)
    t = cal.t_of(friday_late)
    assert not cal.api.is_working_day(friday_late) and not cal.is_working_index(int(t // 1))


def test_a_zone_with_daylight_saving_is_refused():
    with pytest.raises(ValueError, match="UTC offset"):
        WorkingCalendar(MON, tz="Europe/London")


@pytest.mark.parametrize("hours", range(0, 14 * 24, 5))
@pytest.mark.parametrize("n", [0, 1, 2, 3, 5, 7, 31])
def test_clock_api_equals_the_apis_add_working_days(hours, n):
    """The float-clock arithmetic is the API's `add_working_days` on the instant, to the second."""
    cal = WorkingCalendar(MON, holidays=["2026-01-07"])
    t = hours / 24 + 0.013
    got = cal.at(cal.add_working_days_t(t, n))
    want = cal.api.add_working_days(cal.at(t), n)
    assert abs((got - want).total_seconds()) < 1e-3, (hours, n, got, want)


@pytest.mark.parametrize("h1", range(0, 10 * 24, 7))
@pytest.mark.parametrize("h2", range(0, 10 * 24, 11))
def test_clock_between_equals_the_apis(h1, h2):
    cal = WorkingCalendar(MON, holidays=["2026-01-07"])
    t1, t2 = h1 / 24 + 0.01, h2 / 24 + 0.02
    assert cal.working_days_between_t(t1, t2) == cal.api.working_days_between(cal.at(t1), cal.at(t2))


def test_simulation_clock_api():
    cal = WorkingCalendar(MON)
    assert cal.add_working_days_t(0.5, 5) == pytest.approx(7.5)  # Mon 0.5 + 5wd = next Mon, time of day kept
    assert cal.add_working_days_t(5.5, 1) == pytest.approx(8.0)  # Sat noon counts from Mon 00:00: + 1 = Tue 00:00
    assert cal.working_days_between_t(0.0, 7.0) == 5
    assert cal.working_days_between_t(7.0, 0.0) == -5
    assert cal.next_working_t(5.1) == pytest.approx(7.35)  # Saturday -> Monday morning
    assert cal.next_working_t(2.2) == 2.2


def test_the_simulation_context_uses_the_apis_calendar(ctx):
    """The context's calendar is config/calendar.yaml: same weekend, holidays and zone as the API's."""
    api = load_calendar(REPO_ROOT / "config" / "calendar.yaml")
    assert ctx.cal.api == api
    assert ctx.cal.api == default_calendar()
