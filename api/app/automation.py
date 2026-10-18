"""The automation actor (A21, ADR-032): every step of the pathway that is not a human decision, done by `system:automation`.

Coordinator labour is the scaling limit of a screening-closure programme, so people should act only at final decisions
(accept an escalation, approve a model-drafted message, sign a closure, handle an exhausted reroute list). Everything else
is here. The automation writes its events in process (the ingest hook and the scheduler's scans): there is no route a
token can use to post them.

* intake        `acknowledge {auto}` when the result arrives, then `assign_ownership {owner, provider, auto}` to the default
                owner of the provider the patient is registered with (config/providers.yaml). The owner is named, not
                made to accept: the clock runs regardless, the owner still accepts and contacts the patient, and the
                escalation ladder reaches them if they do nothing.
* reroute       a rejected or bounced referral (`referral_rejected`, no request pending) is re-requested from the next
                provider in the reroute order (`handoff_requested {to_provider, auto}`); when the list is used up the
                automation raises `review_requested {reason: reroute_exhausted}` and stops: a coordinator reassigns.
* outreach      pre-approved deterministic templates (config/outreach_policy.yaml) sent through `notify.send(auto=...)`:
                no draft, no approval, `message_sent {auto, template_id, template_version}`. Two gates, neither of them in
                this module: at append time, inside the store transaction, the outreach policy (template allow-list and
                version, weekly cap over ALL outbound messages: `outreach_policy.auto_message_guard`); in the state machine,
                what the log decides (consent for the channel now, consent freeze, an open opt-out or wrong-number hold).
                This module only decides what is due, and sends nothing while a hold is open.
* evidence      HAPI is asked whether the colonoscopy exists for the case; if so `complete_confirmatory {result_ref, auto}`
                (from confirmatory_booked) and `closure_proposed {evidence_ref}`. The proposal gives the clinician a
                one-click sign-off with the evidence prefilled. Closure stays human: this actor cannot emit `close`
                (the state machine refuses every automated actor), and nothing here reads a proposal back. HAPI is asked
                only about cases whose booked appointment has begun, at most once per `EVIDENCE_NEGATIVE_TTL` of simulated
                time per case when it has nothing yet, and the scan stops at the first answer of "unavailable".

Every append has a deterministic event id (uuid5 over the case, the event that caused it and the kind), so a scan that
runs twice, or two at once, writes each event once. Every scan is a no-op while `AUTOMATION_ENABLED` is false. The scans
ask the store only for the states they can act on (a SQL filter on `current_state`, not a replay of every open case) and
fetch a case's log only when the model says something is due.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app import evidence, notify, outreach_policy, payloads, users
from app.calendar import WorkingCalendar
from app.config import settings
from app.events import CaseNotFound, Event, EventIdConflict
from app.pathway import Pathway
from app.providers import Providers, get_providers
from app.state_machine import (
    AUTOMATION_ACTOR,
    AUTO_MESSAGE_WINDOW,
    CLOSURE_PROPOSAL_STATES,
    InvalidTransition,
    channel_consent,
)

log = logging.getLogger("app.automation")

# Fixed namespace for the automation's deterministic event and notification ids (never change: it would break idempotency).
AUTOMATION_NAMESPACE = uuid.UUID("c3e1b7a4-52d8-4f0a-9b66-1d7e8a3f4c25")
EVIDENCE_SCAN_STATES = ("confirmatory_booked", *CLOSURE_PROPOSAL_STATES)
EVIDENCE_SCAN_LIMIT = 200  # cases asked about in HAPI per scan: the rest are asked on the next loop
EVIDENCE_NEGATIVE_TTL = timedelta(hours=6)  # simulated time: a case HAPI had nothing for is not asked again sooner
INTAKE_STATES = ("result_received", "acknowledged")


def eid(case_id: UUID, *parts: object) -> UUID:
    """uuid5 over the case and what caused the event (an event id, a rule, a reference): one id per cause, never a time."""
    return uuid.uuid5(AUTOMATION_NAMESPACE, "|".join([str(case_id), *map(str, parts)]))


def enabled() -> bool:
    return bool(settings.automation_enabled)


class NegativeCache:
    """Per-case memory of "HAPI had no colonoscopy for this case" (simulated time, in process, lost on restart: a restart
    only costs one extra question per case). A case HAPI could not answer for is NOT remembered, only a definite no."""

    def __init__(self, ttl: timedelta = EVIDENCE_NEGATIVE_TTL) -> None:
        self.ttl = ttl
        self._at: dict[UUID, datetime] = {}
        self._lock = threading.Lock()

    def fresh(self, case_id: UUID, now: datetime) -> bool:
        with self._lock:
            at = self._at.get(case_id)
        return at is not None and at <= now < at + self.ttl  # a clock that went back makes the entry stale

    def remember(self, case_id: UUID, now: datetime) -> None:
        with self._lock:
            self._at[case_id] = now

    def prune(self, now: datetime) -> None:
        """Forget the entries that have expired, so the cache stays the size of the cases asked about in one TTL."""
        with self._lock:
            self._at = {c: at for c, at in self._at.items() if at <= now < at + self.ttl}

    def clear(self) -> None:
        with self._lock:
            self._at.clear()

    def __len__(self) -> int:
        return len(self._at)


evidence_negatives = NegativeCache()  # the evidence scan's; tests clear it


@dataclass
class ScanResult:
    """What one scan did, as counts (no case ids or names: it is returned to the scheduler and logged)."""

    counts: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def to_json(self) -> dict:
        return {"enabled": True, **self.counts, "errors": list(self.errors)}


def _append(store, pathway: Pathway, event: Event, result: ScanResult | None, key: str) -> bool:
    """Append one automation event. True if it is in the log now (written, or already there); False if the state machine
    refused it (the case moved on, a human got there first, consent changed): logged, counted, never raised."""
    try:
        payloads.validate(event.type, event.payload)  # a bug here cannot put free text in the log
        store.append(event, pathway)
    except InvalidTransition as exc:
        log.info("case %s: automation %s refused: %s", event.case_id, event.type, exc)
        if result is not None:
            result.add(f"{key}_refused")
        return False
    except (CaseNotFound, EventIdConflict, payloads.PayloadSchemaError) as exc:
        log.error("case %s: automation %s failed: %s: %s", event.case_id, event.type, type(exc).__name__, exc)
        if result is not None:
            result.errors.append(f"{event.case_id}: {event.type}: {type(exc).__name__}")
        return False
    if result is not None:
        result.add(key)
    return True


# ---- intake: acknowledge and assign -------------------------------------------------------------------------------


def _ensure_seed_user(seed) -> None:
    """The assigned owner needs a display-name row to be shown by name (the log holds only the opaque id). Written only if
    the person has none, never over a real login's row."""
    if users.directory.get(seed.user_id) is None:
        users.directory.upsert(users.UserRecord(seed.user_id, seed.role, seed.name, seed.provider))


def acknowledge_event(case_id: UUID, now: datetime) -> Event:
    return Event(case_id, "acknowledge", now, AUTOMATION_ACTOR, {"auto": True}, eid(case_id, "acknowledge"))


def assign_event(case_id: UUID, now: datetime, registered_provider: str | None, providers: Providers) -> Event:
    seed = providers.owner_for(registered_provider)
    _ensure_seed_user(seed)
    return Event(case_id, "assign_ownership", now, AUTOMATION_ACTOR,
                 {"owner": seed.actor, "provider": seed.provider, "auto": True}, eid(case_id, "assign_ownership"))


def on_ingest(store, pathway: Pathway, case_id: UUID, genesis_payload: dict, now: datetime,
              result: ScanResult | None = None) -> None:
    """Right after a case is created: acknowledge it and name its owner. Best effort: a failure here is logged and
    healed by the next intake scan; it never fails the ingest of a result."""
    if not enabled():
        return
    try:
        _append(store, pathway, acknowledge_event(case_id, now), result, "acknowledged")
        providers = get_providers()
        _append(store, pathway, assign_event(case_id, now, genesis_payload.get("registered_provider"), providers),
                result, "assigned")
    except Exception:  # noqa: BLE001 - ingest must not fail because the automation could not run
        log.exception("case %s: automatic intake failed; the next scan retries it", case_id)


def _intake(store, pathway: Pathway, case_id: UUID, model, now: datetime, result: ScanResult) -> None:
    if model.state == "result_received":
        _append(store, pathway, acknowledge_event(case_id, now), result, "acknowledged")
        model = store.read_model(case_id, pathway) or model
    if model.state in ("result_received", "acknowledged") and model.owner is None:
        events = store.events_for(case_id)
        genesis = events[0].payload if events else {}
        _append(store, pathway, assign_event(case_id, now, genesis.get("registered_provider"), get_providers()),
                result, "assigned")


# ---- reroute -------------------------------------------------------------------------------------------------------


def _reroute(store, pathway: Pathway, case_id: UUID, model, now: datetime, result: ScanResult) -> None:
    """A case waiting for a re-route (`referral_rejected`, nothing pending, no exhausted flag yet): ask the next provider
    in the order, or, when none is left, hand the case to a person."""
    if model.state != "referral_rejected" or model.pending_handoff is not None or model.reroute_exhausted_at is not None:
        return
    providers = get_providers()
    events = store.events_for(case_id)
    rejection = next((e for e in reversed(events) if e.type == "handoff_rejected"), None)
    if rejection is None:
        return  # reached referral_rejected another way: nothing to re-route from
    reason = rejection.payload.get("reason")
    if not providers.reroutes_reason(reason):
        result.add("reroute_left_to_humans")  # missing information, patient declined: a person decides
        return
    tried = [e.payload.get("to_provider") for e in events if e.type == "handoff_requested"]
    tried.append(model.provider)
    nxt = providers.next_provider(pathway.name, tried)
    if nxt is not None:
        _append(store, pathway, Event(case_id, "handoff_requested", now, AUTOMATION_ACTOR,
                                      {"to_provider": nxt, "auto": True}, eid(case_id, rejection.event_id, "reroute")),
                result, "rerouted")
    else:
        _append(store, pathway, Event(case_id, "review_requested", now, AUTOMATION_ACTOR,
                                      {"reason": "reroute_exhausted", "source_event_id": str(rejection.event_id)},
                                      eid(case_id, rejection.event_id, "reroute_exhausted")),
                result, "reroute_exhausted")


# ---- outreach ------------------------------------------------------------------------------------------------------


def _rule_key(rule, model, pathway: Pathway, calendar: WorkingCalendar, now: datetime):
    """The id of the thing that makes `rule` due now (the event that entered the state, or that raised the rung), else
    None. One message per key per rule, ever: the notification id is derived from it. (An open outreach hold does not
    change what is due, only whether it is sent: `_outreach`.)"""
    if model.consent_frozen or model.state not in pathway.open_states:
        return None
    if rule.state is not None and model.state != rule.state:
        return None
    if rule.trigger == "entered_state":
        return model.clock_started_event_id
    if rule.trigger == "reminder":
        if model.clock_started_at is None or model.clock_started_event_id is None:
            return None
        due = calendar.add_working_days(model.clock_started_at, rule.after_working_days)
        return model.clock_started_event_id if now >= due else None
    if rule.trigger == "escalation_rung":
        level = pathway.rung_level(rule.rung)
        if level and model.escalation_level == level and not model.escalation_accepted:
            return model.escalation_event_id
    return None


def _language(events, default: str) -> str:
    for e in reversed(events):
        if e.type in ("patient_acknowledge", "callback_requested") and e.payload.get("lang") in ("en", "ar"):
            return e.payload["lang"]
    return default


def _outreach(store, pathway: Pathway, case_id: UUID, model, now: datetime, calendar: WorkingCalendar,
              policy, result: ScanResult) -> None:
    due = [(r, k) for r in policy.rules if (k := _rule_key(r, model, pathway, calendar, now)) is not None]
    if not due:
        return
    if model.outreach_holds:  # an opt-out or a wrong number is flagged: nothing goes until a person clears it
        result.add("outreach_held", len(due))
        return
    from app.ai.provider import MAX_FIELD_CHARS
    from app.ai.templates import DEFAULT_NEXT_STEP, render
    from app.routers.drafts import appointment_date  # late: the drafts router imports the cases router

    events = store.events_for(case_id)
    genesis = events[0].payload if events else {}
    existing = {n.id for n in notify.log.for_case(case_id)}
    for rule, key in due:
        nid = eid(case_id, "outreach", rule.id, key)
        if nid in existing:
            continue
        recent = sum(1 for t in model.outbound_message_times if now - AUTO_MESSAGE_WINDOW < t <= now)  # automatic AND approved
        if recent >= policy.max_auto_messages_per_week:
            result.add("outreach_capped")  # still due: tried again on a later scan, once the week allows it
            continue
        owner = users.resolve_actor(model.owner) if model.owner else None
        if owner is None or not owner["display_name"] or len(owner["display_name"]) > MAX_FIELD_CHARS:
            result.add("outreach_no_owner_name")
            continue
        language = _language(events, policy.default_language)
        date = appointment_date(events, model.state, language) if rule.purpose == "appointment_confirmation" else None
        if rule.purpose == "appointment_confirmation" and date is None:
            result.add("outreach_no_date")
            continue
        channel = next((c for c in rule.channels
                        if channel_consent(model, c)[0] and notify.contact_blocked(genesis, c, model) is None), rule.channels[0])
        template = policy.templates[f"{rule.purpose}.{channel}"]
        body = render(template.purpose, channel, language, owner_name=owner["display_name"],
                      next_step=DEFAULT_NEXT_STEP[language], appointment_date=date)
        try:
            row = notify.send(case_id, channel, language, template.purpose, body, at=now, store=store, pathway=pathway,
                              notification_id=nid, auto={"template_id": template.id, "template_version": template.version})
        except InvalidTransition as exc:  # consent or a hold changed, the cap was reached, or the case moved on, between our read and the write
            log.info("case %s: automatic message refused: %s", case_id, exc)
            result.add("outreach_refused")
            continue
        result.add("outreach_sent" if row.status == "sent" else "outreach_blocked")


# ---- the scan the scheduler loop calls -----------------------------------------------------------------------------


def _scan_states(policy) -> tuple[str, ...] | None:
    """The states the automation scan can act on, for the SQL filter on `current_state`: intake, the re-route wait, and the
    state of every outreach rule. None (no filter) if a rule applies in any state."""
    states = {*INTAKE_STATES, "referral_rejected"}
    for rule in policy.rules if policy is not None else ():
        if rule.state is None:
            return None
        states.add(rule.state)
    return tuple(sorted(states))


def run_automation_scan(store, pathway: Pathway, now: datetime, calendar: WorkingCalendar, only=None) -> dict:
    """One pass over the open cases in the states it can act on: intake (acknowledge, assign), reroute, outreach.
    Idempotent. Returns counts."""
    if not enabled():
        return {"enabled": False}
    result = ScanResult()
    try:
        policy = outreach_policy.get_policy()
    except outreach_policy.OutreachPolicyError as exc:
        policy = None  # fail closed on messaging only: the rest of the automation does not depend on templates
        result.errors.append(f"outreach policy unusable, no automatic messages: {exc}")
        log.error("outreach policy unusable, no automatic messages: %s", exc)
    states = _scan_states(policy)
    candidates = store.iter_open_case_models(pathway, only=only, **({"states": states} if states is not None else {}))
    for case_id, model, error in candidates:
        result.add("cases_evaluated")
        if model is None:
            result.errors.append(f"{case_id}: {error}")
            continue
        try:
            if model.state in INTAKE_STATES:
                _intake(store, pathway, case_id, model, now, result)
            _reroute(store, pathway, case_id, model, now, result)
            if policy is not None:
                _outreach(store, pathway, case_id, model, now, calendar, policy, result)
        except Exception as exc:  # noqa: BLE001 - one bad case must not stop the scan for the others
            log.exception("case %s: automation scan failed", case_id)
            result.errors.append(f"{case_id}: {type(exc).__name__}")
    return result.to_json()


# ---- evidence ------------------------------------------------------------------------------------------------------


def _booking_time(events) -> datetime | None:
    for e in reversed(events):
        if e.type == "book_confirmatory":
            return e.at
    return None


def _appointment_begun(events, now: datetime) -> bool:
    """False only when the booked appointment is known and still ahead: a colonoscopy that has not happened has no report
    in HAPI, so there is nothing to ask. A date-only appointment has begun at the start of its day (UTC); no date on the
    booking means we cannot tell, so we ask."""
    for e in reversed(events):
        if e.type != "book_confirmatory":
            continue
        raw = e.payload.get("appointment_at") or e.payload.get("appointment_date")
        if not isinstance(raw, str):
            return True
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            return True
        return (when if when.tzinfo else when.replace(tzinfo=UTC)) <= (now if now.tzinfo else now.replace(tzinfo=UTC))
    return True


def scan_evidence(store, pathway: Pathway, now: datetime, finder=None, verifier=None,
                  limit: int = EVIDENCE_SCAN_LIMIT, only=None, negatives: NegativeCache | None = None) -> dict:
    """Ask HAPI for the colonoscopy of every case that is booked or completed and has no closure proposal yet. When one is
    found and belongs to the case (the verifier `close` uses), complete the confirmatory step if it is still open and
    propose the closure. A person still reviews and signs.

    Kind to HAPI: only the cases in the three states that can take a proposal are read (a filter on `current_state` in
    the store); a booked case whose appointment is still ahead is not asked about; a case HAPI had nothing for is not
    asked again for `EVIDENCE_NEGATIVE_TTL` of simulated time; and the first answer of "unavailable" ends the scan (every
    further question would meet the same outage), to be tried again on the next loop."""
    if not enabled():
        return {"enabled": False}
    finder = finder if finder is not None else evidence.finder
    verifier = verifier if verifier is not None else evidence.verifier
    negatives = negatives if negatives is not None else evidence_negatives
    negatives.prune(now)
    result = ScanResult()
    asked = 0
    for case_id, model, error in store.iter_open_case_models(pathway, only=only, states=EVIDENCE_SCAN_STATES):
        if model is None or model.state not in EVIDENCE_SCAN_STATES or model.closure_proposal is not None:
            continue
        if negatives.fresh(case_id, now):
            result.add("skipped_recent_no")
            continue
        events = store.events_for(case_id)
        if model.state == "confirmatory_booked" and not _appointment_begun(events, now):
            result.add("not_yet_due")
            continue
        if asked >= limit:
            result.add("deferred")
            continue
        asked += 1
        patient_ref = events[0].payload.get("patient_ref") if events else None
        try:
            ref = next((r for r in finder.find(case_id=case_id, patient_ref=patient_ref, after=_booking_time(events))
                        if verifier.exists(r, case_id=case_id, patient_ref=patient_ref)), None)
        except evidence.EvidenceUnavailable as exc:
            result.add("unavailable")
            log.warning("evidence scan: FHIR server cannot answer, stopping this scan: %s", exc)
            break
        result.add("asked")
        if ref is None:
            negatives.remember(case_id, now)
            continue
        result.add("found")
        if model.state == "confirmatory_booked":
            done = _append(store, pathway, Event(case_id, "complete_confirmatory", now, AUTOMATION_ACTOR,
                                                 {"result_ref": ref, "auto": True},
                                                 eid(case_id, model.clock_started_event_id, "evidence_complete")),
                           result, "completed")
            if not done:
                continue
        _append(store, pathway, Event(case_id, "closure_proposed", now, AUTOMATION_ACTOR,
                                      {"evidence_ref": ref, "auto": True}, eid(case_id, ref, "closure_proposed")),
                result, "closure_proposed")
    return result.to_json()


def configure(app_settings) -> None:
    """At start-up: say what the automation will do, and check its two files now rather than at the first case. A bad
    outreach policy turns automatic messaging off (logged as an error); the rest of the automation still runs."""
    if not app_settings.automation_enabled:
        log.warning("automation is OFF (AUTOMATION_ENABLED is not true): no automatic acknowledge, assignment, reroute, "
                    "outreach or evidence proposal")
        return
    providers = get_providers()
    log.info("automation is ON: %d registration provider(s), reroute order for %s", len(providers.owners),
             sorted(providers.reroute_order))
    try:
        policy = outreach_policy.get_policy()
        log.info("automatic outreach: %d approved template(s), at most %d per patient per week (approval: %s)",
                 len(policy.templates), policy.max_auto_messages_per_week, policy.status)
    except outreach_policy.OutreachPolicyError as exc:
        log.error("automatic outreach is OFF: the outreach policy is unusable: %s", exc)
