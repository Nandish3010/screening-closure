"""Finite capacity (ticket B19, milestone M6): a booking calendar per colonoscopy provider and a daily minutes budget for
the people who work escalations.

Both are SHARED by every case of a run, which is what the unconstrained simulator lacked (README, "What is and is not
modelled"). They live in the engine only when `ModelParams.capacity` is set; with it unset nothing in this module is
touched, no random number is drawn and the event log is the one every earlier result used. With supply so large that
nothing ever waits (`capacity.scale` and `capacity.staff.scale` very large) the event log is identical to the
unconstrained one: a test pins that.

Slots (`SlotBook`)
  * Supply: `sessions_per_week_per_1000_cases x slots_per_session x scale x cases / 1000` slots a week, spread evenly over
    the working days and over `providers` providers. A provider's slots on a working day are an integer: the difference
    of two floors of a running total, with a per-provider phase, so the supply averages exactly its rate.
  * A booking asks for the date the unconstrained model would have used (the lead time) and takes the earliest working
    day on or after it that still has a free slot at that provider; the time of day is kept.
  * A no-show consumes the slot; with probability `dna_backfill_prob` (a pre-drawn latent per appointment, so common
    random numbers hold) the unit refills it from outside the cohort. A patient who withdraws consent and declines care
    before the day cancels: the slot goes back to the calendar and the next booking can take it.
  * Utilisation counts slots booked (attended, no-show or waiting for their day) over slots offered, on the working
    days of the first `utilisation_window_days`; it can never exceed 1 because a day never holds more bookings than slots.

Escalation handling (`StaffQueue`)
  * Each role that works escalations (the supervisor, rung 1; the programme coordinator, rung 2 and watchdog flags) has a
    minutes budget per working day. An escalation is a job: `accept_minutes`, plus `contact_minutes` when the case is
    waiting for the patient (the person phones them). Jobs are served first come first served; a job that does not fit in
    the rest of the day's budget starts on the next working day (a budget shorter than one job is raised to one job). A job that
    starts the day it arrives costs no delay.
  * An escalation that waits is raised (the `escalated` event, the call, the staff reaction) when it is served, not when
    it was due. If the case has moved on by then it is stale: it still used the minutes (the person opened it).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .calendar import WORKING_TIME_OF_DAY, WorkingCalendar
from .metrics import percentile
from .params import CapacityParams, ParamError

SUPERVISOR, COORDINATOR = "owner_supervisor", "programme_coordinator"
ROLES = (SUPERVISOR, COORDINATOR)
SEARCH_LIMIT_DAYS = 3650


@dataclass(slots=True)
class Booking:
    case: int
    provider: int
    day: int
    request_t: float
    earliest_t: float
    slot_t: float
    outcome: str = "booked"  # booked -> attended | dna_backfilled | dna_empty | cancelled | superseded


class SlotBook:
    def __init__(self, cap: CapacityParams, cal: WorkingCalendar, n_cases: int, working_days_per_week: int):
        self.cap, self.cal = cap, cal
        weekly = cap.sessions_per_week_per_1000_cases * cap.slots_per_session * cap.scale * n_cases / 1000.0
        self.rate = weekly / working_days_per_week / cap.providers  # slots per provider per working day (a float)
        self.phase = [i / cap.providers for i in range(cap.providers)]
        self.booked: dict[tuple[int, int], int] = defaultdict(int)  # (provider, day) -> bookings holding the slot
        self.live: dict[int, Booking] = {}  # case -> its unresolved booking
        self.log: list[Booking] = []
        self._cap_cache: dict[tuple[int, int], int] = {}

    # ---- supply --------------------------------------------------------------------------------
    def capacity_on(self, provider: int, day: int) -> int:
        """Slots a provider offers on day index `day` (0 on a non-working day)."""
        key = (provider, day)
        v = self._cap_cache.get(key)
        if v is None:
            if not self.cal.is_working_index(day):
                v = 0
            else:
                k = self.cal.working_ordinal(day)
                ph = self.phase[provider]
                v = math.floor(self.rate * (k + 1) + ph) - math.floor(self.rate * k + ph)
            self._cap_cache[key] = v
        return v

    # ---- bookings --------------------------------------------------------------------------------
    def book(self, case: int, provider: int, earliest_t: float, request_t: float) -> float:
        old = self.live.get(case)
        if old is not None:  # a second booking while one is held: the first is given up, never held twice
            self._free(old, "superseded")
        first = math.floor(earliest_t)
        d = first
        while self.capacity_on(provider, d) <= self.booked[(provider, d)]:
            d += 1
            if d - first > SEARCH_LIMIT_DAYS:
                raise ParamError("capacity: no free colonoscopy slot within 10 years; the slot supply is effectively zero")
        slot_t = earliest_t if d == first else d + (earliest_t - first)
        b = Booking(case, provider, d, request_t, earliest_t, slot_t)
        self.booked[(provider, d)] += 1
        self.live[case] = b
        self.log.append(b)
        return slot_t

    def _free(self, b: Booking, outcome: str) -> None:
        self.booked[(b.provider, b.day)] -= 1
        b.outcome = outcome
        self.live.pop(b.case, None)

    def cancel(self, case: int) -> None:
        b = self.live.get(case)
        if b is not None:
            self._free(b, "cancelled")

    def held_day(self, case: int) -> int | None:
        b = self.live.get(case)
        return None if b is None else b.day

    def resolve(self, case: int, outcome: str) -> None:
        """The appointment day came: the slot is used (attended), or a no-show refilled or left empty."""
        b = self.live.pop(case, None)
        if b is not None:
            b.outcome = outcome

    # ---- metrics ---------------------------------------------------------------------------------
    def window_days(self) -> list[int]:
        return [d for d in range(self.cap.utilisation_window_start_day, self.cap.utilisation_window_end_day)
                if self.cal.is_working_index(d)]

    def summary(self, n_cases: int) -> dict[str, float | None]:
        days = self.window_days()
        wset = set(days)
        offered = sum(self.capacity_on(p, d) for p in range(self.cap.providers) for d in days)
        held = [b for b in self.log if b.outcome not in ("cancelled", "superseded")]
        in_win = [b for b in held if b.day in wset]
        used = [b for b in in_win if b.outcome in ("attended", "dna_backfilled")]
        waits = [b.slot_t - b.request_t for b in held]
        delays = [b.slot_t - b.earliest_t for b in held]
        out: dict[str, float | None] = {
            "slots_offered": float(offered),
            "slots_booked": float(len(in_win)),
            "slot_utilisation": len(in_win) / offered if offered else None,
            "slot_used_share": len(used) / offered if offered else None,
            "bookings_per_100_cases": 100.0 * len(held) / n_cases if n_cases else None,
            "cancellations_per_100_cases": 100.0 * sum(1 for b in self.log if b.outcome == "cancelled") / n_cases if n_cases else None,
            "dna_backfilled_share": (sum(1 for b in held if b.outcome == "dna_backfilled")
                                     / max(1, sum(1 for b in held if b.outcome in ("dna_backfilled", "dna_empty")))),
            "request_to_slot_wait_median": percentile(waits, 0.5),
            "request_to_slot_wait_p90": percentile(waits, 0.9),
            "capacity_delay_mean": sum(delays) / len(delays) if delays else None,
            "capacity_delay_p90": percentile(delays, 0.9),
            "booked_late_share": (sum(1 for x in delays if x >= 1.0) / len(delays)) if delays else None,
        }
        return out

    def max_load(self) -> float:
        """Largest bookings / slots over any (provider, day); at most 1.0 by construction (a test checks it)."""
        worst = 0.0
        for (p, d), n in self.booked.items():
            if n:
                worst = max(worst, n / max(1, self.capacity_on(p, d)))
        return worst


@dataclass(slots=True)
class Job:
    role: str
    request_t: float
    start_t: float
    minutes: float
    kind: str = "rung"  # rung (an escalation of the ladder) | force (the coordinator acting on a watchdog flag)
    state: str = "pending"  # pending -> acted | stale (a job whose start is after the run's end stays pending)


class StaffQueue:
    def __init__(self, cap: CapacityParams, cal: WorkingCalendar, n_cases: int):
        self.cap, self.cal = cap, cal
        k = n_cases / 1000.0 * cap.staffing_scale
        self.budget = {SUPERVISOR: cap.supervisor_minutes_per_day_per_1000_cases * k,
                       COORDINATOR: cap.coordinator_minutes_per_day_per_1000_cases * k}
        # a team can always work one escalation a day: a budget (a very small cohort, a very low staffing level) shorter than the
        # longest job is raised to it, so a job always fits in some day
        longest = cap.accept_minutes + cap.contact_minutes
        self.budget = {role: max(b, longest) for role, b in self.budget.items()}
        self._cur: dict[str, list] = {r: [None, 0.0] for r in ROLES}  # role -> [working day being filled, minutes used]
        self._last_start: dict[str, float] = {r: 0.0 for r in ROLES}  # first come first served: nobody starts before the person ahead
        self.used: dict[tuple[str, int], float] = defaultdict(float)  # (role, day) -> minutes
        self.jobs: list[Job] = []

    def job_minutes(self, awaiting_patient: bool) -> float:
        return self.cap.accept_minutes + (self.cap.contact_minutes if awaiting_patient else 0.0)

    def request(self, role: str, t: float, minutes: float, kind: str = "rung") -> Job:
        budget = self.budget[role]
        arrival = self.cal.next_working_t(t)
        d = math.floor(arrival)
        cur = self._cur[role]
        if cur[0] is None or d > cur[0]:
            cur[0], cur[1] = d, 0.0
        if cur[1] + minutes > budget + 1e-9:
            cur[0], cur[1] = self.cal.working_index_after(cur[0]), 0.0
        cur[1] += minutes
        self.used[(role, cur[0])] = cur[1]
        start = arrival if cur[0] == d else cur[0] + WORKING_TIME_OF_DAY
        start = max(start, self._last_start[role])
        self._last_start[role] = start
        job = Job(role, t, start, minutes, kind)
        self.jobs.append(job)
        return job

    # ---- metrics ---------------------------------------------------------------------------------
    def summary(self, n_cases: int, days: list[int]) -> dict[str, float | None]:
        jobs = self.jobs
        started = [j for j in jobs if j.state != "pending"]
        delays = [j.start_t - j.request_t for j in started]
        wset = set(days)
        out: dict[str, float | None] = {
            "esc_jobs_per_100_cases": 100.0 * len(jobs) / n_cases if n_cases else None,
            "esc_delay_mean": sum(delays) / len(delays) if delays else None,
            "esc_delay_p90": percentile(delays, 0.9),
            "esc_delayed_share": (sum(1 for x in delays if x >= 0.5) / len(delays)) if delays else None,
            "esc_stale_share": (sum(1 for j in started if j.state == "stale") / len(started)) if started else None,
        }
        # queue length at the end of each working day of the window: jobs requested by then and not yet started
        qlens = []
        for d in days:
            end = d + 1.0
            qlens.append(sum(1 for j in jobs if j.request_t < end <= j.start_t))
        out["esc_queue_mean"] = sum(qlens) / len(qlens) if qlens else None
        out["esc_queue_max"] = float(max(qlens)) if qlens else None
        for role, tag in ((SUPERVISOR, "supervisor"), (COORDINATOR, "coordinator")):
            tot = sum(m for (r, d), m in self.used.items() if r == role and d in wset)
            avail = self.budget[role] * len(days)
            out[f"esc_{tag}_utilisation"] = tot / avail if avail else None
        return out


@dataclass
class CapacityRun:
    """What one engine run leaves behind for tests and metrics."""
    slots: SlotBook
    staff: StaffQueue
    n_cases: int
    counters: dict = field(default_factory=dict)

    def metrics(self) -> dict[str, float | None]:
        m = self.slots.summary(self.n_cases)
        m.update(self.staff.summary(self.n_cases, self.slots.window_days()))
        return m
