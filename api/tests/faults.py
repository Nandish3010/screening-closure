"""Fault-injection harness for the chaos tests (F3, T-F3, T-F4).

Test-only: this module lives under api/tests and nothing in `app` imports it, so no production
code path can switch a fault on. It still honours the convention the plan asks for: faults are
named in the environment variable `FAULT_INJECT` and are read **only when `APP_ENV=test`**. With any
other `APP_ENV` (the default `dev`, or a deployment) the variable is ignored, `active()` is empty and
`wrap_store()` returns the store it was given.

    FAULT_INJECT="duplicate_delivery=8,fail_after_insert=1"   APP_ENV=test

Faults (comma separated, `name` or `name=value`):

    duplicate_delivery=N   every append is delivered N times at once (threads released together), as a
                           queue with at-least-once delivery or an impatient client would.
    fail_after_insert=K    the K-th append commits to the inner store and then raises InjectedFault
                           before the caller sees the result: "the store died after the event insert
                           but before the projection". K=0 or absent: never.
    fail_before_insert=K   the K-th append raises before anything is written.
    stale_clock=SECONDS    `skew()` returns the given time minus SECONDS: a scheduler that reads a clock
                           that is behind the one the events were stamped with.
    clock_ahead=SECONDS    `skew()` returns the given time plus SECONDS (a fast clock).

Use `wrap_store(store)` around the store under test (outside a ProjectingStore to fault before the
projection, inside to fault the store the projector reads), and `skew(now)` wherever the test passes
a time to the sweep.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from datetime import datetime, timedelta

ENV_VAR = "FAULT_INJECT"
GATE_VAR = "APP_ENV"
GATE_VALUE = "test"


class InjectedFault(RuntimeError):
    """Raised by an injected store fault. Distinct from every real error so tests can tell them apart."""


def parse(spec: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        name, _, value = part.partition("=")
        if name not in {"duplicate_delivery", "fail_after_insert", "fail_before_insert", "stale_clock", "clock_ahead"}:
            raise ValueError(f"unknown fault {name!r} in {ENV_VAR}")
        out[name] = int(value) if value else 1
    return out


def active(environ: Mapping[str, str] | None = None) -> dict[str, int]:
    """The faults currently switched on: empty unless APP_ENV=test."""
    env = os.environ if environ is None else environ
    if env.get(GATE_VAR) != GATE_VALUE:
        return {}
    return parse(env.get(ENV_VAR, ""))


def skew(now: datetime, environ: Mapping[str, str] | None = None) -> datetime:
    faults = active(environ)
    return now - timedelta(seconds=faults.get("stale_clock", 0)) + timedelta(seconds=faults.get("clock_ahead", 0))


class FaultyStore:
    """Delegates to a store and applies the append faults. Everything else passes straight through."""

    def __init__(self, inner, faults: dict[str, int]) -> None:
        self._inner = inner
        self.faults = faults
        self._lock = threading.Lock()
        self.appends = 0  # calls seen (a duplicated delivery counts once)
        self.delivered = 0  # calls that reached the inner store
        self.injected = 0
        if hasattr(inner, "append_many"):
            self.append_many = self._append_many

    def _tick(self) -> int:
        with self._lock:
            self.appends += 1
            return self.appends

    def append(self, event, pathway, *, within=None):
        n = self._tick()
        if self.faults.get("fail_before_insert") == n:
            self.injected += 1
            raise InjectedFault(f"injected: append #{n} refused before the insert")
        copies = self.faults.get("duplicate_delivery", 1)
        result = self._deliver(copies, lambda: self._inner.append(event, pathway, within=within))
        if self.faults.get("fail_after_insert") == n:
            self.injected += 1
            raise InjectedFault(f"injected: append #{n} committed, then the store failed before returning")
        return result

    def _append_many(self, events, pathway, *, within=None):
        n = self._tick()
        if self.faults.get("fail_before_insert") == n:
            self.injected += 1
            raise InjectedFault(f"injected: append_many #{n} refused before the insert")
        copies = self.faults.get("duplicate_delivery", 1)
        result = self._deliver(copies, lambda: self._inner.append_many(events, pathway, within=within))
        if self.faults.get("fail_after_insert") == n:
            self.injected += 1
            raise InjectedFault(f"injected: append_many #{n} committed, then the store failed before returning")
        return result

    def _deliver(self, copies: int, call):
        """Run `call` `copies` times with all copies released together; return the first outcome,
        re-raising its exception if it failed (every copy of an identical event must agree)."""
        if copies <= 1:
            with self._lock:
                self.delivered += 1
            return call()
        barrier = threading.Barrier(copies)
        outcomes: list = [None] * copies

        def run(i: int) -> None:
            barrier.wait()
            try:
                outcomes[i] = ("ok", call())
            except Exception as exc:  # noqa: BLE001 - reported to the caller below
                outcomes[i] = ("err", exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(copies)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with self._lock:
            self.delivered += copies
        self.outcomes = outcomes
        for kind, value in outcomes:
            if kind == "ok":
                return value
        raise outcomes[0][1]

    def __getattr__(self, name):
        return getattr(self._inner, name)


def wrap_store(store, environ: Mapping[str, str] | None = None):
    """`store` itself unless a store fault is active (and APP_ENV=test)."""
    faults = {k: v for k, v in active(environ).items() if k in {"duplicate_delivery", "fail_after_insert", "fail_before_insert"}}
    return FaultyStore(store, faults) if faults else store
