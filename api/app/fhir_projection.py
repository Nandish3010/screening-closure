"""FHIR projection of the event stream (A12, ADR-024): the event log is the source of truth, HAPI
holds a standards-shaped copy that other systems (and Malaffi, later) can read.

What each event writes (resource ids are deterministic, so projecting an event twice writes the
same resources and the projection is idempotent by event id):

* every event      `Provenance/prov-<event_id>`: agent = the actor, entity = the event id, target = the
                   resources the event touched; the actor's `Practitioner/<user_id>` (or `Device/system-<name>`)
                   is created on the fly
* every event      `Task/task-<case_id>`: the case, rebuilt from the current read model: status mirrors the
                   state (`businessStatus` is the exact state), `owner` is the owner's Practitioner,
                   `focus` the screening DiagnosticReport, `for` the Patient
* handoff_requested  `ServiceRequest/sr-<event_id>` (status `draft` while the receiver has not answered);
  handoff_accepted/rejected  the same ServiceRequest, now `active` (performer = the accepting
                   Practitioner) or `revoked` (the reason is on the note)
* book_confirmatory  `Appointment/appt-<event_id>` (a stub: status `proposed`, no slot time is modelled)
* message_sent     `Communication/comm-<event_id>` (the text stays in the notifications log)
* consent_updated  `Consent/consent-<case_id>`, rebuilt from the current consent (history is HAPI's versions)

Projection runs in the API right after a successful append (`ProjectingStore`). It is best effort:
any failure is logged, the event id goes into a retry queue and the append still succeeds; a
background loop drains the queue. References to a Patient or DiagnosticReport that do not exist in HAPI
are written as identifier-only references (HAPI enforces referential integrity), so a case for a
patient who is not in the store still projects.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app import users
from app.calendar import WorkingCalendar
from app.events import Event
from app.pathway import Pathway
from app.state_machine import CASE_CREATED, CaseModel, actor_role, is_automated, replay_model
from app.readmodel import current_due

log = logging.getLogger("app.fhir_projection")

DEAD_LETTER_ALERT = "fhir_projection_dead_letter"  # derived on read by GET /alerts
SYSTEM_CASE = "urn:screening-closure:case"
SYSTEM_EVENT = "urn:screening-closure:event"
SYSTEM_USER = "urn:screening-closure:user"
SYSTEM_STATE = "urn:screening-closure:state"
SYSTEM_EVENT_TYPE = "urn:screening-closure:event-type"
SYSTEM_PATIENT_REF = "urn:screening-closure:patient-ref"
SYSTEM_NOTIFICATION = "urn:screening-closure:notification"
EXT = "urn:screening-closure:ext:"
DEFAULT_TAG = {"system": "https://screening-closure.example/projection", "code": "closure-engine"}

# Task.status mirrors the case state; Task.businessStatus carries the exact state.
TASK_STATUS = {
    "result_received": "requested",
    "acknowledged": "received",
    "owner_accepted": "accepted",
    "patient_contacted": "in-progress",
    "patient_acknowledged": "in-progress",
    "confirmatory_booked": "in-progress",
    "confirmatory_completed": "in-progress",
    "clinician_reviewed": "in-progress",
    "closed": "completed",
    "referral_rejected": "on-hold",
    "consent_withdrawn": "on-hold",
    "lost_contact": "on-hold",
    "reopened": "in-progress",
}
_BAD_ID = re.compile(r"[^A-Za-z0-9.-]")
# A reference that may be looked up in HAPI: `Type/id`, the id without a dot-only form (SEC-1). Anything else is
# never put in a URL; the projection writes it as an identifier-only reference instead.
_SAFE_REF = re.compile(r"^([A-Z][A-Za-z]{1,39})/([A-Za-z0-9.-]{1,64})$")


def is_safe_ref(ref: object) -> bool:
    m = _SAFE_REF.match(ref) if isinstance(ref, str) else None
    return bool(m) and set(m.group(2)) != {"."}


def safe_ref_url(base_url: str, ref: str) -> str:
    """`base/Type/id` with each part URL-quoted. Raises FhirError for a reference that is not `Type/id`."""
    if not is_safe_ref(ref):
        raise FhirError("not a valid FHIR reference: refused before any request")
    rtype, rid = ref.split("/", 1)
    return f"{base_url}/{urllib.parse.quote(rtype, safe='')}/{urllib.parse.quote(rid, safe='')}"


class FhirError(Exception):
    """HAPI refused or could not be reached."""


class FhirClient(Protocol):
    def transaction(self, bundle: dict) -> None:
        """POST a transaction Bundle. Raises FhirError."""
        ...

    def exists(self, ref: str) -> bool:
        """Does `Type/id` exist? Raises FhirError if it cannot tell."""
        ...


class HttpFhirClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, url: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/fhir+json", "Content-Type": "application/fhir+json"})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def transaction(self, bundle: dict) -> None:
        try:
            with self._call("POST", self.base_url, bundle) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raise FhirError(f"HAPI answered HTTP {exc.code}: {exc.read()[:400].decode(errors='replace')}") from exc
        except Exception as exc:
            raise FhirError(f"HAPI unreachable: {exc}") from exc

    def exists(self, ref: str) -> bool:
        try:
            with self._call("GET", safe_ref_url(self.base_url, ref)) as resp:
                resp.read()
                return resp.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return False
            raise FhirError(f"HAPI answered HTTP {exc.code}") from exc
        except Exception as exc:
            raise FhirError(f"HAPI unreachable: {exc}") from exc


# ---- retry queue -------------------------------------------------------------------------------------
#
# A failed projection is retried with exponential backoff and given up on after `max_attempts` (SEC-1): the row
# moves to the dead-letter list, which `GET /alerts` reports. Without that, one event HAPI will never accept sat at
# the head of an oldest-first queue for ever and, with 100 of them, starved every event behind it.


class RetryPolicy:
    """When a failed projection is tried again, and when it is given up. Delays are in seconds."""

    def __init__(self, base_seconds: float = 30.0, cap_seconds: float = 3600.0, max_attempts: int = 10) -> None:
        if base_seconds < 0 or cap_seconds < base_seconds or max_attempts < 1:
            raise ValueError("RetryPolicy: need 0 <= base <= cap and max_attempts >= 1")
        self.base_seconds, self.cap_seconds, self.max_attempts = base_seconds, cap_seconds, max_attempts

    def delay_after(self, attempts: int) -> float:
        """Seconds to wait once `attempts` attempts have failed. The first failure is retried at the
        next drain pass (the event was only just appended); after that the wait doubles up to the cap."""
        if attempts <= 1:
            return 0.0
        return min(self.cap_seconds, self.base_seconds * 2 ** (attempts - 2))

    def is_dead(self, attempts: int) -> bool:
        return attempts >= self.max_attempts


class ProjectionQueue(Protocol):
    def add(self, event_id: UUID, case_id: UUID, error: str) -> bool:
        """Record one failed attempt (a new row, or the next attempt of a queued one) and schedule the next
        retry. True if the event has now used all its attempts and was moved to the dead-letter list."""
        ...

    def pending(self, limit: int = 100, *, due_only: bool = False) -> list[tuple[UUID, UUID, int]]:
        """(event_id, case_id, attempts) ordered by when each is next due (`next_attempt_at`), earliest first.
        `due_only` keeps only those whose time has come (what the background loop asks for)."""
        ...

    def remove(self, event_id: UUID) -> None: ...

    def count(self, case_id: UUID | None = None) -> int:
        """Events waiting to be retried (dead-lettered ones are not counted)."""
        ...

    def dead_count(self) -> int: ...

    def dead_since(self) -> datetime | None:
        """When the oldest dead-lettered event was given up on (None if there is none)."""
        ...

    def revive_dead(self) -> int:
        """Put every dead-lettered event back in the queue with a fresh set of attempts; returns how many."""
        ...


class InMemoryProjectionQueue:
    def __init__(self, policy: RetryPolicy | None = None, clock: Callable[[], float] | None = None) -> None:
        self.policy = policy or RetryPolicy()
        # Monotonic seconds: a retry schedule is about HAPI's health, never about a case.
        self._now = clock or time.monotonic  # wall-clock-ok: retry backoff only
        self._lock = threading.Lock()
        self._rows: dict[UUID, dict] = {}
        self._dead: dict[UUID, dict] = {}
        self._n = 0

    def add(self, event_id, case_id, error):
        with self._lock:
            row = self._rows.get(event_id)
            if row is None:
                self._n += 1
                row = self._rows[event_id] = {"case_id": case_id, "attempts": 0, "n": self._n}
            row["attempts"] += 1
            row["error"] = error
            row["next_attempt_at"] = self._now() + self.policy.delay_after(row["attempts"])
            if self.policy.is_dead(row["attempts"]):
                del self._rows[event_id]
                self._dead[event_id] = {**row, "dead_n": len(self._dead) + 1}
                return True
            return False

    def pending(self, limit=100, *, due_only=False):
        with self._lock:
            now = self._now()
            rows = [(e, r) for e, r in self._rows.items() if not due_only or r["next_attempt_at"] <= now]
            rows.sort(key=lambda kv: (kv[1]["next_attempt_at"], kv[1]["n"]))
            return [(e, r["case_id"], r["attempts"]) for e, r in rows[:limit]]

    def remove(self, event_id):
        with self._lock:
            self._rows.pop(event_id, None)

    def count(self, case_id=None):
        with self._lock:
            return sum(1 for r in self._rows.values() if case_id is None or r["case_id"] == case_id)

    def dead_count(self):
        with self._lock:
            return len(self._dead)

    def dead_since(self):
        return None  # no wall-clock timestamp is kept in memory

    def revive_dead(self):
        with self._lock:
            revived = len(self._dead)
            for event_id, row in sorted(self._dead.items(), key=lambda kv: kv[1]["dead_n"]):
                self._rows[event_id] = {"case_id": row["case_id"], "attempts": 0, "n": row["n"],
                                        "error": row["error"], "next_attempt_at": self._now()}
            self._dead.clear()
            return revived


# ---- mapping: events -> FHIR resources (pure, apart from the existence probe) --------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _fhir_id(raw: str) -> str:
    out = _BAD_ID.sub("-", raw)[:64]
    return "unknown" if not out or set(out) == {"."} else out  # never an empty or dots-only id


def agent_of(actor: str) -> tuple[str, str, str, dict]:
    """(role, reference, id, resource-to-ensure) for an actor string."""
    role, user_id = users.split_actor(actor)
    if is_automated(actor) or role in ("system", "scheduler", "watchdog", "model"):
        rid = _fhir_id(f"system-{user_id}" if role == "system" else f"{role}-{user_id}")
        device = {"resourceType": "Device", "id": rid, "status": "active",
                  "deviceName": [{"name": actor, "type": "user-friendly-name"}]}
        return role, f"Device/{rid}", rid, device
    rid = _fhir_id(user_id)
    resolved = users.resolve_actor(actor) or {}
    practitioner = {"resourceType": "Practitioner", "id": rid, "active": True,
                    "identifier": [{"system": SYSTEM_USER, "value": user_id}],
                    "name": [{"text": resolved.get("display_name") or user_id}]}
    return role, f"Practitioner/{rid}", rid, practitioner


class Mapper:
    """Builds the resources for one event of one case. `exists` probes HAPI for references that
    must resolve (Patient, DiagnosticReport); it is the only impure part."""

    def __init__(self, pathway: Pathway, calendar: WorkingCalendar, tag: dict, exists: Callable[[str], bool]):
        self.pathway, self.calendar, self.tag, self._exists = pathway, calendar, tag, exists
        self._resolved: dict[str, bool] = {}

    def _tagged(self, resource: dict) -> dict:
        meta = {**resource.get("meta", {}), "tag": [self.tag]}
        return {**resource, "meta": meta}

    def _ref(self, ref: str | None, identifier_system: str) -> dict | None:
        """A real reference if the resource exists in HAPI, else an identifier-only reference."""
        if not ref:
            return None
        if ref not in self._resolved:
            # A reference that is not a clean `Type/id` is never looked up (it would be part of a URL); it
            # is written as an identifier, like a reference to a resource HAPI does not have (SEC-1).
            self._resolved[ref] = bool(is_safe_ref(ref) and self._exists(ref))
        if self._resolved[ref]:
            return {"reference": ref}
        return {"identifier": {"system": identifier_system, "value": ref}, "display": ref}

    # -- per-resource builders -----------------------------------------------------------------------

    def task(self, case_id: UUID, events: list[Event], model: CaseModel) -> tuple[dict, list[dict]]:
        genesis = events[0]
        ensure: list[dict] = []
        task: dict = {
            "resourceType": "Task", "id": f"task-{case_id}",
            "identifier": [{"system": SYSTEM_CASE, "value": str(case_id)}],
            "status": TASK_STATUS.get(model.state, "in-progress"),
            "businessStatus": {"coding": [{"system": SYSTEM_STATE, "code": model.state}]},
            "intent": "order",
            "priority": "urgent" if model.any_overdue else "routine",
            "code": {"text": "Positive FIT result: follow up to documented closure"},
            "authoredOn": _iso(genesis.at), "lastModified": _iso(model.last_event_at),
            "extension": [
                {"url": EXT + "overdue", "valueBoolean": bool(model.any_overdue)},
                {"url": EXT + "escalation-level", "valueInteger": int(model.escalation_level)},
                {"url": EXT + "consent-frozen", "valueBoolean": bool(model.consent_frozen)},
            ],
        }
        patient = self._ref(genesis.payload.get("patient_ref"), SYSTEM_PATIENT_REF)
        if patient:
            task["for"] = patient
        focus = self._ref(genesis.payload.get("result_ref"), SYSTEM_EVENT)
        if focus:
            task["focus"] = focus
        if model.owner:
            _, ref, _, practitioner = agent_of(model.owner)
            if ref.startswith("Practitioner/"):
                task["owner"] = {"reference": ref}
                ensure.append(practitioner)
        due = None
        if model.state in self.pathway.open_states:
            due = current_due(model, self.pathway, self.calendar)  # the state SLA or a running milestone, whichever is first
        if due is not None:
            task["restriction"] = {"period": {"end": _iso(due)}}
        return task, ensure

    def _handoff_pair(self, events: list[Event], upto: Event | None):
        """The handoff_requested events with their answers (accepted/rejected), in order."""
        pairs, open_req = [], None
        for ev in events:
            if ev.type == "handoff_requested":
                open_req = [ev, None]
                pairs.append(open_req)
            elif ev.type in ("handoff_accepted", "handoff_rejected") and open_req and open_req[1] is None:
                open_req[1] = ev
            elif ev.type == "reassign":
                open_req = None
        return [(r, a) for r, a in pairs]

    def service_request(self, patient: dict | None, request: Event, answer: Event | None) -> tuple[dict, list[dict]]:
        ensure = []
        _, req_ref, _, req_res = agent_of(request.actor)
        ensure.append(req_res)
        sr: dict = {
            "resourceType": "ServiceRequest", "id": f"sr-{request.event_id}",
            "identifier": [{"system": SYSTEM_EVENT, "value": str(request.event_id)}],
            "status": "draft", "intent": "order",
            "category": [{"text": "referral"}],
            "code": {"text": "Referral for confirmatory colonoscopy"},
            "authoredOn": _iso(request.at), "requester": {"reference": req_ref},
            "performer": [{"display": str(request.payload.get("to_provider"))}],
        }
        if patient:
            sr["subject"] = patient
        if answer is not None and answer.type == "handoff_accepted":
            _, ref, _, res = agent_of(answer.actor)
            ensure.append(res)
            sr["status"] = "active"
            sr["performer"] = [{"reference": ref, "display": str(request.payload.get("to_provider"))}]
        elif answer is not None:
            sr["status"] = "revoked"
            sr["note"] = [{"text": f"handoff_rejected: {answer.payload.get('reason')}"}]
        return sr, ensure

    def appointment(self, patient: dict | None, event: Event, request: Event | None) -> tuple[dict, list[dict]]:
        _, ref, _, res = agent_of(event.actor)
        appt: dict = {
            "resourceType": "Appointment", "id": f"appt-{event.event_id}",
            "identifier": [{"system": SYSTEM_EVENT, "value": str(event.event_id)}],
            "status": "proposed",  # a booked appointment needs start and end (FHIR app-3); no slot time is modelled
            "serviceType": [{"text": "Confirmatory colonoscopy"}],
            "description": "Confirmatory colonoscopy booked in the closure engine (stub: no slot time is modelled)",
            "created": _iso(event.at),
            "participant": [{"actor": patient if patient else {"reference": ref}, "status": "accepted"}],
        }
        if request is not None:
            appt["basedOn"] = [{"reference": f"ServiceRequest/sr-{request.event_id}"}]
        return appt, [res]

    def communication(self, patient: dict | None, event: Event) -> tuple[dict, list[dict]]:
        _, ref, _, res = agent_of(event.actor)
        comm: dict = {
            "resourceType": "Communication", "id": f"comm-{event.event_id}",
            "identifier": [{"system": SYSTEM_NOTIFICATION, "value": str(event.payload.get("notification_id"))}],
            "status": "completed",
            "category": [{"text": str(event.payload.get("purpose"))}],
            "medium": [{"coding": [{"system": "urn:screening-closure:channel", "code": str(event.payload.get("channel"))}],
                        "text": str(event.payload.get("channel"))}],
            "sent": _iso(event.at), "sender": {"reference": ref},
            # the message text stays in the notifications log (erasable); only its id is here
        }
        if patient:
            comm["subject"] = patient
        return comm, [res]

    def consent(self, case_id: UUID, patient: dict | None, model: CaseModel) -> dict:
        c = model.consent
        withdrawn = bool(c.get("withdrawn"))

        def rule(key: str) -> dict:
            return {"type": "permit" if c.get(key) else "deny", "code": [{"text": key}]}

        resource: dict = {
            "resourceType": "Consent", "id": f"consent-{case_id}",
            "identifier": [{"system": SYSTEM_CASE, "value": str(case_id)}],
            "status": "inactive" if withdrawn else "active",
            "scope": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/consentscope", "code": "patient-privacy"}]},
            "category": [{"coding": [{"system": "http://loinc.org", "code": "59284-0", "display": "Consent Document"}]}],
            # FHIR ppc-1 wants a policy or a policy rule: the programme's opt-out contact consent (ADR-017)
            "policyRule": {"coding": [{"system": "urn:screening-closure:policy", "code": "programme-contact-consent"}],
                           "text": "Screening programme contact consent: all channels allowed until the patient says otherwise"},
            "provision": {
                "type": "deny" if withdrawn else "permit",
                "provision": [rule(k) for k in ("channel_sms", "channel_whatsapp", "channel_phone", "channel_letter",
                                                "share_with_specialist")],
            },
        }
        if c.get("updated_at"):
            resource["dateTime"] = c["updated_at"]
        if patient:
            resource["patient"] = patient
        return resource

    def provenance(self, event: Event, targets: list[str], patient: dict | None) -> tuple[dict, list[dict]]:
        role, ref, _, res = agent_of(event.actor)
        who: dict = {"reference": ref}
        ensure = [res]
        if role == "patient":
            # the patient acts for themselves: the agent is the Patient the case is about, not a Practitioner
            who = patient if patient else {"display": "patient"}
            ensure = []
        prov = {
            "resourceType": "Provenance", "id": f"prov-{event.event_id}",
            "target": [{"reference": t} for t in targets],
            "recorded": _iso(event.at),
            "activity": {"coding": [{"system": SYSTEM_EVENT_TYPE, "code": event.type}]},
            "agent": [{
                "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type", "code": "author"}]},
                "role": [{"text": role}],
                "who": who,
            }],
            "entity": [{"role": "source", "what": {"identifier": {"system": SYSTEM_EVENT, "value": str(event.event_id)}}}],
        }
        return prov, ensure

    # -- one event -------------------------------------------------------------------------------------

    def resources_for(self, case_id: UUID, events: list[Event], event: Event) -> list[dict]:
        """Every resource to PUT for `event` (agents first, then the case resources, Provenance last),
        deduplicated, each tagged."""
        model = replay_model(events, self.pathway)
        genesis = events[0]
        patient = self._ref(genesis.payload.get("patient_ref"), SYSTEM_PATIENT_REF)
        out: dict[str, dict] = {}

        def put(res: dict) -> str:
            out[f"{res['resourceType']}/{res['id']}"] = res
            return f"{res['resourceType']}/{res['id']}"

        targets: list[str] = []
        task, ensure = self.task(case_id, events, model)
        for e in ensure:
            put(e)
        targets.append(put(task))

        pairs = self._handoff_pair(events, event)
        if event.type == "handoff_requested":
            request = event
        elif event.type in ("handoff_accepted", "handoff_rejected"):
            request = next((r for r, a in pairs if a is not None and a.event_id == event.event_id), None)
        else:
            request = None
        if request is not None:
            answer = next((a for r, a in pairs if r.event_id == request.event_id), None)
            sr, ensure = self.service_request(patient, request, answer)
            for e in ensure:
                put(e)
            targets.append(put(sr))
        if event.type == "book_confirmatory":
            latest_request = pairs[-1][0] if pairs else None
            appt, ensure = self.appointment(patient, event, latest_request)
            for e in ensure:
                put(e)
            targets.append(put(appt))
        if event.type == "message_sent":
            comm, ensure = self.communication(patient, event)
            for e in ensure:
                put(e)
            targets.append(put(comm))
        if event.type == "consent_updated":
            targets.append(put(self.consent(case_id, patient, model)))
        prov, ensure = self.provenance(event, targets, patient)
        for e in ensure:
            put(e)
        put(prov)
        # agents (Practitioner, Device) go first so references resolve even if the server checks in order
        ordered = sorted(out.values(), key=lambda r: {"Practitioner": 0, "Device": 0, "Task": 1, "ServiceRequest": 1,
                                                       "Appointment": 1, "Communication": 1, "Consent": 1,
                                                       "Provenance": 2}.get(r["resourceType"], 1))
        return [self._tagged(r) for r in ordered]


def case_resource_ids(pathway: Pathway, calendar: WorkingCalendar, case_id: UUID, events: list[Event]) -> dict:
    """The ids of the resources the projection writes for this case (computed, no network)."""
    mapper = Mapper(pathway, calendar, DEFAULT_TAG, exists=lambda ref: False)
    seen: dict[str, list[str]] = {}
    for ev in events:
        for res in mapper.resources_for(case_id, events, ev):
            seen.setdefault(res["resourceType"], [])
            ref = f"{res['resourceType']}/{res['id']}"
            if ref not in seen[res["resourceType"]]:
                seen[res["resourceType"]].append(ref)
    return {
        "task": (seen.get("Task") or [None])[0],
        "service_requests": seen.get("ServiceRequest", []),
        "appointments": seen.get("Appointment", []),
        "communications": seen.get("Communication", []),
        "consent": (seen.get("Consent") or [None])[0],
        "provenance": seen.get("Provenance", []),
        "practitioners": seen.get("Practitioner", []),
        "devices": seen.get("Device", []),
    }


# ---- the projector and the wrapping store -------------------------------------------------------------------


class Projector:
    """Projects events to HAPI; failures go to the retry queue and never raise."""

    def __init__(self, client: FhirClient, store, pathway: Pathway, calendar: WorkingCalendar,
                 queue: ProjectionQueue, tag: dict | None = None, backoff_seconds: float = 15.0):
        self.client, self.store, self.pathway, self.calendar, self.queue = client, store, pathway, calendar, queue
        self.tag = tag or DEFAULT_TAG
        self.backoff_seconds = backoff_seconds
        self._down_until = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _mapper(self) -> Mapper:
        return Mapper(self.pathway, self.calendar, self.tag, self.client.exists)

    def project_now(self, case_id: UUID, event: Event) -> None:
        """Write the event's resources. Raises on failure (callers queue it)."""
        events = self.store.events_for(case_id)
        if not any(e.event_id == event.event_id for e in events):
            raise FhirError(f"event {event.event_id} is not in the log of case {case_id}")
        resources = self._mapper().resources_for(case_id, events, event)
        bundle = {"resourceType": "Bundle", "type": "transaction", "entry": [
            {"resource": r, "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}} for r in resources]}
        self.client.transaction(bundle)

    def project(self, case_id: UUID, event: Event) -> bool:
        """Best effort: True if projected now, False if queued for retry. Never raises."""
        try:
            if time.monotonic() < self._down_until:  # wall-clock-ok: HAPI outage backoff only, never about a case
                raise FhirError("HAPI marked down: queued without trying")
            self.project_now(case_id, event)
            return True
        except Exception as exc:
            if isinstance(exc, FhirError) and "unreachable" in str(exc):
                self._down_until = time.monotonic() + self.backoff_seconds  # wall-clock-ok: outage backoff only
            log.warning("FHIR projection of event %s queued for retry: %s", event.event_id, exc)
            try:
                self.queue.add(event.event_id, case_id, str(exc)[:500])
            except Exception:  # the queue itself is unavailable: the log is still safe, say so loudly
                log.error("FHIR projection queue unavailable; event %s will not be projected until a rebuild", event.event_id)
            return False

    def drain(self, limit: int = 100, *, due_only: bool = False) -> dict:
        """Retry queued events, earliest due first. Returns {tried, projected, still_queued}.

        A call you make yourself retries whatever is queued, backoff or not (an operator or a test saying "try
        now"); the background loop passes `due_only=True` and so respects each event's `next_attempt_at`. Either
        way a failure pushes that event's next attempt further out, so an event HAPI keeps refusing goes to the back
        of the line and, after `max_attempts`, to the dead-letter list instead of starving the ones behind it."""
        tried = projected = 0
        self._down_until = 0.0  # a drain is an explicit retry: ignore the outage backoff
        for event_id, case_id, _ in self.queue.pending(limit, due_only=due_only):
            tried += 1
            try:
                event = next((e for e in self.store.events_for(case_id) if e.event_id == event_id), None)
                if event is not None:
                    self.project_now(case_id, event)
                self.queue.remove(event_id)
                projected += 1
            except Exception as exc:
                if self.queue.add(event_id, case_id, str(exc)[:500]):
                    log.error("FHIR projection of event %s given up after repeated failures (dead-lettered): %s",
                              event_id, exc)
                if isinstance(exc, FhirError) and "unreachable" in str(exc):
                    break  # HAPI is still down: do not hammer it
        return {"tried": tried, "projected": projected, "still_queued": self.queue.count()}

    def start_drain_loop(self, interval_seconds: float = 20.0) -> None:
        if self._thread is not None:
            return

        def loop():
            while not self._stop.wait(interval_seconds):
                try:
                    if self.queue.count():
                        self.drain(due_only=True)
                except Exception:
                    log.exception("FHIR projection drain failed")

        self._thread = threading.Thread(target=loop, name="fhir-projection-drain", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class ProjectingStore:
    """Wraps an event store: after a successful create/append, project the event. Everything else
    is delegated, so it can stand in for the store anywhere (routes, sweep, notify, consent)."""

    def __init__(self, inner, projector: Projector):
        self._inner = inner
        self.projector = projector
        projector.store = inner
        if hasattr(inner, "append_many"):  # the sweep probes for it with hasattr
            self.append_many = self._append_many

    def create_case(self, case_id, pathway_name, genesis_event, initial_state):
        try:
            self._inner.create_case(case_id, pathway_name, genesis_event, initial_state)
        except BaseException:
            self._queue_if_committed(case_id, [genesis_event])
            raise
        self.projector.project(case_id, genesis_event)

    def append(self, event, pathway, *, within=None, guard=None):
        try:
            state = self._inner.append(event, pathway, within=within, guard=guard)
        except BaseException:
            self._queue_if_committed(event.case_id, [event])
            raise
        self.projector.project(event.case_id, event)
        return state

    def _append_many(self, events, pathway, *, within=None, guard=None):
        """The sweep's batched append (F6), offered only when the inner store has one. Projects
        every event of the batch after the commit (idempotent by event id, so a retry is harmless)."""
        try:
            state = self._inner.append_many(events, pathway, within=within, guard=guard)
        except BaseException:
            for event in events:
                self._queue_if_committed(event.case_id, [event])
            raise
        for event in events:
            self.projector.project(event.case_id, event)
        return state

    def _queue_if_committed(self, case_id, events) -> None:
        """F3-1: the inner store raised. If the event nevertheless committed (the store failed after the
        insert, the connection dropped while the commit's answer was on its way back), nothing would ever
        project it, so queue it for the retry loop before the error goes on to the caller. A rejected
        append (invalid transition, unknown case, id conflict) wrote nothing and queues nothing. When the
        log cannot be read to tell, queue anyway: draining an event that is not in the log just drops the
        row. Never raises: the original error is the one the caller must see."""
        try:
            try:
                logged = {e.event_id for e in self._inner.events_for(case_id)}
            except Exception:  # cannot tell: queue speculatively
                logged = {e.event_id for e in events}
            for event in events:
                if event.event_id in logged:
                    self._enqueue(event, case_id)
        except Exception:  # pragma: no cover - the original error takes precedence
            log.exception("FHIR projection: could not check whether a failed append committed")

    def _enqueue(self, event, case_id) -> None:
        last: Exception | None = None
        for _ in range(2):  # its own retry: the queue is the only thing standing between this event and HAPI
            try:
                self.projector.queue.add(event.event_id, case_id, "append raised after the insert; queued by ProjectingStore")
                log.warning("FHIR projection of event %s queued: the store call failed after the insert", event.event_id)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
        log.error("FHIR projection queue unavailable (%s); event %s will not be projected until a rebuild", last, event.event_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def dead_letter_alert(store) -> dict | None:
    """The derived `fhir_projection_dead_letter` alert for GET /alerts: events given up on after repeated
    failures are in no log and no queue any more, so someone has to be told. None if there are none (or the
    projection is off)."""
    queue = getattr(getattr(store, "projector", None), "queue", None)
    if queue is None:
        return None
    try:
        count = queue.dead_count()
        since = queue.dead_since() if count else None
    except Exception:  # noqa: BLE001 - an alerts page must not fail because the queue is unreachable
        log.exception("could not count the dead-lettered FHIR projections")
        return None
    if not count:
        return None
    return {"id": None, "kind": DEAD_LETTER_ALERT, "since": since.isoformat() if since else None,
            "cleared_at": None, "derived": True, "detail": {"dead_lettered": count}}


def wrap_store(store, app_settings, pathway: Pathway, calendar: WorkingCalendar, pool=None, tag: dict | None = None):
    """Wire the projection around `store` when FHIR_PROJECTION is on (what `app.main` does at start-up).

    Returns the store to use (a `ProjectingStore`, or `store` itself when projection is off). The
    retry queue is the Postgres table when there is a pool, else in memory."""
    if not app_settings.fhir_projection:
        return store
    # getattr: a duck-typed settings object (tests, tools) need not carry the retry knobs; the defaults are Settings'
    policy = RetryPolicy(getattr(app_settings, "fhir_retry_base_seconds", 30.0),
                         getattr(app_settings, "fhir_retry_cap_seconds", 3600.0),
                         getattr(app_settings, "fhir_retry_max_attempts", 12))
    if pool is not None:
        from app.store_pg import PostgresProjectionQueue

        queue: ProjectionQueue = PostgresProjectionQueue(pool, policy)
    else:
        queue = InMemoryProjectionQueue(policy)
    projector = Projector(HttpFhirClient(app_settings.fhir_base_url), store, pathway, calendar, queue, tag=tag)
    wrapped = ProjectingStore(store, projector)
    projector.start_drain_loop(app_settings.fhir_projection_drain_seconds)
    return wrapped
