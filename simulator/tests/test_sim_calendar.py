from datetime import date

import pytest

from simulator.sim.calendar import WorkingCalendar

MON = date(2026, 1, 5)


def test_weekend_is_saturday_sunday_by_default():
    cal = WorkingCalendar(MON)
    assert [cal.is_working_day(date(2026, 1, d)) for d in range(5, 12)] == [True] * 5 + [False] * 2


def test_add_working_days_skips_weekends():
    cal = WorkingCalendar(MON)
    assert cal.add_working_days(date(2026, 1, 9), 1) == date(2026, 1, 12)  # Fri + 1 = Mon
    assert cal.add_working_days(date(2026, 1, 10), 1) == date(2026, 1, 12)  # Sat + 1 = Mon
    assert cal.add_working_days(date(2026, 1, 5), 5) == date(2026, 1, 12)  # Mon + 5 = next Mon
    assert cal.add_working_days(date(2026, 1, 10), 0) == date(2026, 1, 10)  # zero is identity
    assert cal.add_working_days(date(2026, 1, 12), -1) == date(2026, 1, 9)  # Mon - 1 = Fri
    assert cal.add_working_days(date(2026, 1, 5), 10) == date(2026, 1, 19)


def test_working_days_between():
    cal = WorkingCalendar(MON)
    assert cal.working_days_between(date(2026, 1, 9), date(2026, 1, 12)) == 1  # Fri -> Mon
    assert cal.working_days_between(date(2026, 1, 5), date(2026, 1, 12)) == 5
    assert cal.working_days_between(date(2026, 1, 10), date(2026, 1, 11)) == 0  # Sat -> Sun
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


def test_simulation_clock_api_matches_date_api():
    cal = WorkingCalendar(MON)
    for t in (0.0, 0.35, 3.9, 4.5, 5.2, 6.0, 12.75, 30.1):
        for n in (0, 1, 3, 5, 14, 45):
            expected = cal.add_working_days(cal.date_of(t), n)
            got = cal.date_of(cal.add_working_days_t(t, n))
            assert got == expected, (t, n)
    assert cal.add_working_days_t(0.5, 5) == pytest.approx(7.5)  # Mon 0.5 + 5wd = next Mon, time of day kept
    assert cal.working_days_between_t(0.0, 7.0) == 5
    assert cal.working_days_between_t(7.0, 0.0) == -5
    assert cal.next_working_t(5.1) == pytest.approx(7.35)  # Saturday -> Monday morning
    assert cal.next_working_t(2.2) == 2.2
