"""The agency's agents (A22, ADR-033): one supervisor, five department heads, fourteen executioners.

    Supervisor (system:agency:supervisor)
      reads the worklist, metrics, alerts, pending replies and open tasks; emits one dispatch per department with a
      rationale. Never emits a case event; has no tool that causes one.
    Department heads (system:agency:<department>), one agent each
      turn their dispatch into executioner tasks with explicit inputs (rules, then the planner's priority order).
    Executioners, one agent type per task type, each with one tool set and one output schema
      outreach             draft_message, followup_call_prep
      referral_scheduling  reroute_referral, prepare_handoff_pack, booking_followup
      evidence_closure     retrieve_evidence, prepare_closure_pack, flag_missing_evidence
      patient_support      triage_reply, callback_brief
      quality_ops          daily_summary, anomaly_check, fairness_watch, sla_risk_explainer

Every outcome is `done`, `needs_human` (prepared material for a person, who then acts through the ordinary routes)
or `failed`. What stays with people, structurally: accepting an escalation, approving a model-drafted message,
signing a closure, and an exhausted reroute. No executioner has a tool for any of them, the policy refuses them
for every agency identity (`AGENCY_NEVER`), and the state machine refuses any event from an agency actor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agency import config as agency_config
from app.agency.facts import CaseFacts
from app.agency.planner import DepartmentPlan, TaskOrder
from app.agency.tools import ToolBox, ToolFailed

Status = Literal["done", "needs_human", "failed"]
ROLE_OWNER = "owner"  # the case's owning clinician (primary_care_owner or specialist_owner)
CONTACT_STATES = ("owner_accepted", "patient_contacted", "lost_contact")
SUMMARY_MAX = 300


# ---------------------------------------------------------------- output schemas (strict)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(max_length=SUMMARY_MAX)


class _HumanOut(_Out):
    """Material prepared for a person. The person acts through the case page; nothing here acts."""

    title: str | None = Field(default=None, max_length=120)
    assignee_role: str | None = Field(default=None, max_length=40)
    recommended_action: str | None = Field(default=None, max_length=60, pattern=r"^[a-z][a-z_]*$")
    skipped: str | None = Field(default=None, max_length=60)


class DraftMessageOut(_HumanOut):
    draft_id: str | None = None
    purpose: str | None = None
    channel: str | None = None
    language: str | None = None
    provider: str | None = None
    model_id: str | None = None
    fallback_used: bool | None = None
    abstention_reason: str | None = None


class CallPrepOut(_HumanOut):
    language: Literal["en", "ar"]
    script: list[str] = Field(max_length=10)
    channels_allowed: list[str]
    contact_attempts: int
    messages_sent: int
    notifications_by_status: dict[str, int]


class RerouteOut(_HumanOut):
    tried: list[str]
    recommended_provider: str | None = None
    exhausted: bool = False
    requested_via: Literal["automation"] | None = None


class HandoffPackOut(_HumanOut):
    pack: dict


class BookingOut(_HumanOut):
    appointment_at: str | None = None
    appointment_passed: bool | None = None


class EvidenceOut(_HumanOut):
    verification: Literal["on", "off"]
    checked: list[str]
    found: list[str]


class ClosurePackOut(_HumanOut):
    pack: dict
    closes_case: Literal[False] = False  # a closure pack never closes: the signing clinician does


class TriageOut(_HumanOut):
    reply_event_id: str
    intent: str | None = None
    confidence: float | None = None
    classifier_model_id: str | None = None
    classified: bool = False


class CallbackOut(_HumanOut):
    brief: dict


class DailySummaryOut(_Out):
    day: str
    by_state: dict[str, int]
    open_cases: int
    overdue: int
    escalated: int
    flagged: int
    closed: int
    alerts_open: int
    agency_needs_human: int


class FlagsOut(_HumanOut):
    flags: list[dict]


class ExplainOut(_Out):
    sentence: str = Field(max_length=SUMMARY_MAX)
    factors: list[str]


@dataclass
class Outcome:
    status: Status
    output: BaseModel
    follow_ups: list[TaskOrder] = field(default_factory=list)
    provenance: dict | None = None  # overrides the tool provenance (a model draft's provider and prompt)


# ---------------------------------------------------------------- the executioners


@dataclass(frozen=True)
class ExecutionerSpec:
    department: str
    agent_type: str
    label: str
    tools: tuple[str, ...]
    output: type[BaseModel]
    replaces: tuple[str, ...]  # the human actions it absorbs (feeds B17)


class Executioner:
    spec: ExecutionerSpec

    def run(self, box: ToolBox, order: TaskOrder, now: datetime) -> Outcome:  # pragma: no cover - abstract
        raise NotImplementedError

    # helpers
    @staticmethod
    def owner_role(f: CaseFacts) -> str:
        return f.owner_role or "coordinator"

    @staticmethod
    def moved_on(f: CaseFacts, order: TaskOrder) -> str | None:
        """A summary when the case has left the state the task was planned for (or closed), else None."""
        expected = order.inputs.get("state")
        if not f.open or (expected and f.state != expected):
            return f"Nothing to do: the case is now {f.state}."
        return None


def _short(case_id: str) -> str:
    return case_id[:8]


class DraftMessage(Executioner):
    spec = ExecutionerSpec(
        "outreach", "draft_message", "Draft a patient message for approval",
        ("read_case", "read_drafts", "create_draft"), DraftMessageOut,
        ("write a reminder or first message from scratch", "pick the channel and language the patient can receive"),
    )

    def run(self, box, order, now):
        f: CaseFacts = box.call("read_case", case_id=order.case_id)
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", DraftMessageOut(summary=gone, skipped="case_moved_on"))
        if f.consent_frozen:
            return Outcome("done", DraftMessageOut(summary="Outreach is frozen (consent withdrawn).",
                                                   skipped="consent_frozen"))
        if f.owner is None:
            return Outcome("needs_human", DraftMessageOut(
                summary="No owner yet: a message speaks for its owner, so a person must accept the case first.",
                title="Case has no owner", assignee_role="coordinator", recommended_action="assign_owner"))
        pending = [d for d in box.call("read_drafts", case_id=order.case_id) if d["decision"] == "pending"]
        if pending:
            return Outcome("done", DraftMessageOut(summary="A draft is already waiting for approval.",
                                                   draft_id=pending[-1]["draft_id"], skipped="draft_pending"))
        purpose, channel, language = order.inputs["purpose"], order.inputs["channel"], order.inputs["language"]
        try:
            d = box.call("create_draft", case_id=order.case_id, purpose=purpose, channel=channel, language=language)
        except ToolFailed as exc:
            return Outcome("failed", DraftMessageOut(summary=str(exc)[:SUMMARY_MAX]))
        out = DraftMessageOut(
            summary=(f"A {language} {purpose.replace('_', ' ')} for {channel} is drafted and validated"
                     f"{' (template used: ' + d['abstention_reason'] + ')' if d['fallback_used'] else ''}. "
                     "Nothing is sent until the case owner approves it."),
            title="Approve or reject a drafted message", assignee_role=self.owner_role(f),
            recommended_action="approve_or_reject_draft", draft_id=d["draft_id"], purpose=purpose, channel=channel,
            language=language, provider=d["provider"], model_id=d["model_id"], fallback_used=d["fallback_used"],
            abstention_reason=d["abstention_reason"])
        return Outcome("needs_human", out, provenance={"prompt_hash": d["prompt_hash"], "provider": d["provider"],
                                                       "model_id": d["model_id"],
                                                       "fallback_used": bool(d["fallback_used"])})


class FollowupCallPrep(Executioner):
    spec = ExecutionerSpec(
        "outreach", "followup_call_prep", "Prepare a follow-up call",
        ("read_case", "read_notifications"), CallPrepOut,
        ("look up the contact history before calling", "write a call script that discloses no result"),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        language = f.language or "en"
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", CallPrepOut(summary=gone, skipped="case_moved_on", language=language,
                                               script=[], channels_allowed=f.channels_allowed,
                                               contact_attempts=f.contact_attempts, messages_sent=f.messages_sent,
                                               notifications_by_status={}))
        by_status: dict[str, int] = {}
        for n in box.call("read_notifications", case_id=order.case_id):
            by_status[n["status"]] = by_status.get(n["status"], 0) + 1
        script = list(agency_config.call_script(language))
        if "phone" not in f.channels_allowed:
            return Outcome("needs_human", CallPrepOut(
                summary="The patient has switched phone contact off: choose another channel.",
                title="Phone contact is off", assignee_role=self.owner_role(f), recommended_action="choose_channel",
                language=language, script=[], channels_allowed=f.channels_allowed,
                contact_attempts=f.contact_attempts, messages_sent=f.messages_sent, notifications_by_status=by_status))
        return Outcome("needs_human", CallPrepOut(
            summary=(f"{f.contact_attempts} contact attempt(s) and {f.messages_sent} message(s) so far without an "
                     "acknowledgement. A call script is ready; it says only that the result is ready and the care "
                     "team needs to talk about the next step."),
            title="Call the patient", assignee_role=self.owner_role(f), recommended_action="call_patient",
            language=language, script=script, channels_allowed=f.channels_allowed,
            contact_attempts=f.contact_attempts, messages_sent=f.messages_sent, notifications_by_status=by_status))


class RerouteReferral(Executioner):
    spec = ExecutionerSpec(
        "referral_scheduling", "reroute_referral", "Recommend where to re-route a rejected referral",
        ("read_case", "read_providers", "request_handoff"), RerouteOut,
        ("work out which provider has not yet been asked", "re-request a referral after a rejection or bounce"),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        tried = [h.to_provider for h in f.handoffs if h.outcome in ("rejected", "bounced", "pending")]
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", RerouteOut(summary=gone, skipped="case_moved_on", tried=tried))
        if not f.share_with_specialist:
            return Outcome("needs_human", RerouteOut(
                summary="The patient has not agreed to share with a specialist: a person decides the next step.",
                title="Referral needs consent to share", assignee_role="coordinator",
                recommended_action="confirm_consent_before_referral", tried=tried))
        candidates = [p for p in box.call("read_providers") if p not in tried]
        if not candidates:
            return Outcome("needs_human", RerouteOut(
                summary=f"Every provider has been tried ({', '.join(tried) or 'none'}): the reroute is exhausted.",
                title="Re-route exhausted", assignee_role="coordinator",
                recommended_action="handle_exhausted_reroute", tried=tried, exhausted=True))
        to = candidates[0]
        try:
            box.call("request_handoff", case_id=order.case_id, to_provider=to)
        except ToolFailed:
            return Outcome("needs_human", RerouteOut(
                summary=f"Recommend referring to {to} (not yet asked; tried: {', '.join(tried) or 'none'}).",
                title="Re-route the referral", assignee_role="coordinator",
                recommended_action="reassign_or_request_handoff", tried=tried, recommended_provider=to))
        return Outcome("done", RerouteOut(
            summary=f"The automation service requested the referral to {to}; its acceptance is a person's.",
            tried=tried, recommended_provider=to, requested_via="automation"))


class PrepareHandoffPack(Executioner):
    spec = ExecutionerSpec(
        "referral_scheduling", "prepare_handoff_pack", "Prepare the referral pack",
        ("read_case", "read_providers"), HandoffPackOut,
        ("assemble the referral details and deadline", "check consent to share before referring"),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", HandoffPackOut(summary=gone, skipped="case_moved_on", pack={}))
        tried = [h.to_provider for h in f.handoffs if h.outcome != "accepted"]
        options = [p for p in box.call("read_providers") if p not in tried]
        due = f.milestones.get("referral_requested")
        pack = {"case_id": f.case_id, "patient_ref": f.patient_ref, "referral_due_at": due.due_at if due else None,
                "referral_overdue": bool(due and due.overdue), "share_with_specialist": f.share_with_specialist,
                "language": f.language, "providers_tried": tried, "recommended_provider": options[0] if options else None}
        action = "request_handoff" if f.share_with_specialist else "confirm_consent_before_referral"
        return Outcome("needs_human", HandoffPackOut(
            summary=(f"Referral pack ready; refer by {pack['referral_due_at'] or 'the referral deadline'}"
                     + ("" if f.share_with_specialist else "; consent to share is withheld, confirm it first") + "."),
            title="Request the referral", assignee_role=self.owner_role(f), recommended_action=action, pack=pack))


class BookingFollowup(Executioner):
    spec = ExecutionerSpec(
        "referral_scheduling", "booking_followup", "Follow up a booked colonoscopy",
        ("read_case",), BookingOut,
        ("check whether a booked appointment has passed without a result",),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", BookingOut(summary=gone, skipped="case_moved_on"))
        if not f.appointment_at:
            return Outcome("needs_human", BookingOut(
                summary="Booked, but no appointment date is recorded.", title="Record the appointment date",
                assignee_role=self.owner_role(f), recommended_action="record_appointment_date"))
        try:
            when = datetime.fromisoformat(f.appointment_at.replace("Z", "+00:00"))
        except ValueError:
            when = None
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=now.tzinfo)
        passed = when is not None and when < now
        if not passed:
            return Outcome("done", BookingOut(summary=f"Appointment on {f.appointment_at}: nothing to do yet.",
                                              appointment_at=f.appointment_at, appointment_passed=False))
        follow = TaskOrder(agent_type="retrieve_evidence", case_id=f.case_id, key="after_appointment",
                           reason="appointment_passed", inputs={"state": f.state})
        return Outcome("needs_human", BookingOut(
            summary=f"The appointment on {f.appointment_at} has passed and no result is recorded.",
            title="Record the colonoscopy or rebook", assignee_role=self.owner_role(f),
            recommended_action="record_result_or_rebook", appointment_at=f.appointment_at,
            appointment_passed=True), follow_ups=[follow])


def _evidence_refs(f: CaseFacts) -> list[str]:
    from app import confirmatory

    refs = [r for r in (f.result_ref,) if r]
    for r in (confirmatory.procedure_ref(f.case_id), confirmatory.report_ref(f.case_id)):
        if r not in refs:
            refs.append(r)
    return refs


class RetrieveEvidence(Executioner):
    spec = ExecutionerSpec(
        "evidence_closure", "retrieve_evidence", "Look up the colonoscopy evidence in FHIR",
        ("read_case", "find_evidence"), EvidenceOut,
        ("search FHIR for the colonoscopy report before closure",),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        refs = _evidence_refs(f)
        if not f.open:
            return Outcome("done", EvidenceOut(summary=f"Nothing to do: the case is {f.state}.",
                                               skipped="case_moved_on", verification="off", checked=refs, found=[]))
        r = box.call("find_evidence", case_id=order.case_id, refs=refs)
        if r["verification"] == "off":
            return Outcome("done", EvidenceOut(
                summary="Evidence verification is off in this deployment: nothing could be checked.",
                verification="off", checked=refs, found=[]))
        if r["found"]:
            return Outcome("done", EvidenceOut(summary=f"Resolving evidence: {', '.join(r['found'])}.",
                                               verification="on", checked=refs, found=r["found"]))
        follow = TaskOrder(agent_type="flag_missing_evidence", case_id=f.case_id, reason="no_evidence_found",
                           inputs={"state": f.state})
        return Outcome("done", EvidenceOut(summary="No resolving colonoscopy evidence yet.", verification="on",
                                           checked=refs, found=[]), follow_ups=[follow])


class PrepareClosurePack(Executioner):
    spec = ExecutionerSpec(
        "evidence_closure", "prepare_closure_pack", "Prepare the closure pack for the signing clinician",
        ("read_case", "find_evidence"), ClosurePackOut,
        ("assemble the case history, milestones and evidence before signing",),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", ClosurePackOut(summary=gone, skipped="case_moved_on", pack={}))
        refs = _evidence_refs(f)
        r = box.call("find_evidence", case_id=order.case_id, refs=refs)
        milestones = {k: {"due_at": v.due_at, "met": v.met, "overdue": v.overdue} for k, v in f.milestones.items()}
        pack = {
            "case_id": f.case_id, "state": f.state, "steps": [h.model_dump() for h in f.history],
            "milestones": milestones, "escalations": f.escalations, "evidence_checked": refs,
            "evidence_found": r["found"], "evidence_verification": r["verification"],
            "signer": "the owning clinician (" + (f.owner_role or "unassigned") + ")",
            "note": "This pack does not close the case. Only the owning clinician can sign the closure, with an "
                    "evidence reference that resolves.",
        }
        follow = []
        if r["verification"] == "on" and not r["found"]:
            follow.append(TaskOrder(agent_type="flag_missing_evidence", case_id=f.case_id, reason="no_evidence_found",
                                    inputs={"state": f.state}))
        found = ", ".join(r["found"]) if r["found"] else ("not verified" if r["verification"] == "off" else "none")
        return Outcome("needs_human", ClosurePackOut(
            summary=f"Reviewed and ready for sign-off; evidence: {found}. The clinician signs; the pack does not.",
            title="Sign the closure", assignee_role=self.owner_role(f), recommended_action="review_and_sign_closure",
            pack=pack), follow_ups=follow)


class FlagMissingEvidence(Executioner):
    spec = ExecutionerSpec(
        "evidence_closure", "flag_missing_evidence", "Flag missing closure evidence",
        ("read_case", "find_evidence"), EvidenceOut,
        ("notice that a closure has no resolving evidence",),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        refs = _evidence_refs(f)
        if not f.open:
            return Outcome("done", EvidenceOut(summary=f"Nothing to do: the case is {f.state}.",
                                               skipped="case_moved_on", verification="off", checked=refs, found=[]))
        r = box.call("find_evidence", case_id=order.case_id, refs=refs)
        if r["found"]:
            return Outcome("done", EvidenceOut(summary=f"Evidence now resolves: {', '.join(r['found'])}.",
                                               verification="on", checked=refs, found=r["found"]))
        return Outcome("needs_human", EvidenceOut(
            summary="No colonoscopy Procedure or DiagnosticReport resolves for this case.",
            title="Record the colonoscopy result", assignee_role=self.owner_role(f),
            recommended_action="record_colonoscopy_result", verification=r["verification"], checked=refs, found=[]))


class TriageReply(Executioner):
    spec = ExecutionerSpec(
        "patient_support", "triage_reply", "Route a patient reply",
        ("read_case", "classify_reply"), TriageOut,
        ("read and route every incoming reply",),
    )

    def run(self, box, order, now):
        ref = order.inputs["reply_event_id"]
        box.call("read_case", case_id=order.case_id)
        try:
            labels = box.call("classify_reply", case_id=order.case_id, reply_event_id=ref)
        except ToolFailed:
            return Outcome("needs_human", TriageOut(
                summary="No reply classifier in this build: a person reads the reply.", title="Read a patient reply",
                assignee_role="coordinator", recommended_action="read_reply", reply_event_id=ref))
        out = TriageOut(summary=f"Classified as {labels['intent'] or 'unknown'}.", reply_event_id=ref,
                        intent=labels["intent"], confidence=labels["confidence"],
                        classifier_model_id=labels["model_id"], classified=True)
        if labels["needs_human"]:
            return Outcome("needs_human", out.model_copy(update={
                "title": "Read a patient reply", "assignee_role": "coordinator", "recommended_action": "read_reply"}))
        return Outcome("done", out)


class CallbackBrief(Executioner):
    spec = ExecutionerSpec(
        "patient_support", "callback_brief", "Brief a call-back",
        ("read_case", "read_notifications"), CallbackOut,
        ("gather the contact history before calling a patient back",),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        if (gone := self.moved_on(f, order)) is not None:
            return Outcome("done", CallbackOut(summary=gone, skipped="case_moved_on", brief={}))
        sent = [n for n in box.call("read_notifications", case_id=order.case_id) if n["status"] == "sent"]
        language = f.language or "en"
        brief = {"state": f.state, "owner_role": f.owner_role, "language": language,
                 "channels_allowed": f.channels_allowed, "contact_attempts": f.contact_attempts,
                 "messages_sent": len(sent), "callback_requested_at": f.callback_requested_at,
                 "opening_lines": list(agency_config.call_script(language))[:2]}
        return Outcome("needs_human", CallbackOut(
            summary=f"The patient asked for a call back at {f.callback_requested_at}; the brief is ready.",
            title="Call the patient back", assignee_role="coordinator", recommended_action="call_patient_back",
            brief=brief))


class DailySummary(Executioner):
    spec = ExecutionerSpec(
        "quality_ops", "daily_summary", "Daily operations summary",
        ("read_worklist", "read_metrics", "read_alerts", "read_ledger"), DailySummaryOut,
        ("compile the daily status report",),
    )

    def run(self, box, order, now):
        cases = box.call("read_worklist")
        box.call("read_metrics")
        alerts = box.call("read_alerts")
        waiting = box.call("read_ledger", status="needs_human")
        by_state: dict[str, int] = {}
        for c in cases:
            by_state[c.state] = by_state.get(c.state, 0) + 1
        open_ = [c for c in cases if c.open]
        out = DailySummaryOut(
            summary=(f"{len(open_)} open case(s), {sum(c.overdue for c in open_)} overdue, "
                     f"{sum(c.escalation_level > 0 for c in open_)} escalated, {len(alerts)} open alert(s), "
                     f"{len(waiting)} agency item(s) waiting for a person."),
            day=order.inputs["day"], by_state=by_state, open_cases=len(open_), overdue=sum(c.overdue for c in open_),
            escalated=sum(c.escalation_level > 0 for c in open_), flagged=sum(c.watchdog_flagged for c in open_),
            closed=sum(not c.open for c in cases), alerts_open=len(alerts), agency_needs_human=len(waiting))
        return Outcome("done", out)


class AnomalyCheck(Executioner):
    spec = ExecutionerSpec(
        "quality_ops", "anomaly_check", "Operations anomaly check",
        ("read_metrics", "read_alerts", "read_ledger"), FlagsOut,
        ("scan alerts, replay failures and unsafe closures each morning",),
    )

    def run(self, box, order, now):
        m = box.call("read_metrics")
        alerts = box.call("read_alerts")
        tasks = box.call("read_ledger")
        flags = [{"kind": "alert", "detail": a["kind"]} for a in alerts]
        if (m.get("replay_failures") or {}).get("count"):
            flags.append({"kind": "replay_failures", "detail": str(m["replay_failures"]["count"])})
        if m.get("unsafe_closures"):
            flags.append({"kind": "unsafe_closures", "detail": str(m["unsafe_closures"])})
        finished = [t for t in tasks if t["status"] in ("done", "failed", "needs_human")]
        failed = [t for t in finished if t["status"] == "failed"]
        if len(finished) >= 10 and len(failed) / len(finished) > 0.2:
            flags.append({"kind": "agency_failures", "detail": f"{len(failed)} of {len(finished)}"})
        if not flags:
            return Outcome("done", FlagsOut(summary="No anomalies.", flags=[]))
        return Outcome("needs_human", FlagsOut(
            summary="; ".join(f"{x['kind']}: {x['detail']}" for x in flags)[:SUMMARY_MAX], title="Operations anomalies",
            assignee_role="supervisor", recommended_action="review_operations", flags=flags))


class FairnessWatch(Executioner):
    spec = ExecutionerSpec(
        "quality_ops", "fairness_watch", "Fairness watch by language and channel",
        ("read_abstention", "read_worklist"), FlagsOut,
        ("check that no language or channel group is served worse",),
    )

    GAP = 0.10  # a group this far above the overall rate is flagged
    MIN_N = 5

    def run(self, box, order, now):
        ab = box.call("read_abstention")
        cases = [c for c in box.call("read_worklist") if c.open]
        flags = []
        overall = ab.get("rate")
        for dim in ("by_language", "by_channel"):
            for group, cell in (ab.get(dim) or {}).items():
                if overall is not None and cell.get("attempts", 0) >= self.MIN_N and cell.get("rate") is not None \
                        and cell["rate"] - overall > self.GAP:
                    flags.append({"kind": f"abstention_{dim[3:]}", "group": group, "rate": round(cell["rate"], 3),
                                  "overall": round(overall, 3)})
        if cases:
            base = sum(c.overdue for c in cases) / len(cases)
            for lang in ("en", "ar"):
                group = [c for c in cases if c.language == lang]
                if len(group) >= self.MIN_N:
                    rate = sum(c.overdue for c in group) / len(group)
                    if rate - base > self.GAP:
                        flags.append({"kind": "overdue_language", "group": lang, "rate": round(rate, 3),
                                      "overall": round(base, 3)})
        if not flags:
            return Outcome("done", FlagsOut(summary="No group is served measurably worse.", flags=[]))
        return Outcome("needs_human", FlagsOut(
            summary=f"{len(flags)} group(s) above the overall rate by more than {self.GAP:.0%}.",
            title="Fairness: a group is served worse", assignee_role="supervisor",
            recommended_action="review_fairness", flags=flags))


class SlaRiskExplainer(Executioner):
    spec = ExecutionerSpec(
        "quality_ops", "sla_risk_explainer", "Explain an SLA breach in one sentence",
        ("read_case", "explain_factors"), ExplainOut,
        ("explain to a colleague why a case is at the top of the worklist",),
    )

    def run(self, box, order, now):
        f = box.call("read_case", case_id=order.case_id)
        explained = box.call("explain_factors", case_id=order.case_id) or {}
        factors = [x["feature"] for x in explained.get("top_factors", [])][:3]
        what = "overdue" if f.overdue else "on time"
        ladder = f", escalation level {f.escalation_level}" if f.escalation_level else ""
        why = ", ".join(x.replace("_", " ") for x in factors) if factors else "no factor stands out"
        sentence = f"Case {_short(f.case_id)} is {what} in {f.state.replace('_', ' ')}{ladder}; " \
                   f"what moves it up the worklist: {why}."
        return Outcome("done", ExplainOut(summary=sentence[:SUMMARY_MAX], sentence=sentence[:SUMMARY_MAX],
                                          factors=factors))


EXECUTIONERS: dict[str, Executioner] = {e.spec.agent_type: e for e in (
    DraftMessage(), FollowupCallPrep(), RerouteReferral(), PrepareHandoffPack(), BookingFollowup(),
    RetrieveEvidence(), PrepareClosurePack(), FlagMissingEvidence(), TriageReply(), CallbackBrief(),
    DailySummary(), AnomalyCheck(), FairnessWatch(), SlaRiskExplainer(),
)}


# ---------------------------------------------------------------- the department heads


@dataclass(frozen=True)
class WorkItem:
    case_id: str | None
    reason: str
    facts: CaseFacts | None = None


@dataclass(frozen=True)
class HeadSpec:
    department: str
    label: str
    executioners: tuple[str, ...]
    head_tools: tuple[str, ...] = ("read_ledger",)


class DepartmentHead:
    spec: HeadSpec

    def candidates(self, work: list[WorkItem], now: datetime) -> DepartmentPlan:  # pragma: no cover
        raise NotImplementedError


def _first_channel(f: CaseFacts) -> str:
    return next((c for c in ("sms", "whatsapp", "letter") if c in f.channels_allowed), "letter")


def _labels(f: CaseFacts) -> dict:
    return {"state": f.state, "overdue": f.overdue, "escalation_level": f.escalation_level,
            "messages_sent": f.messages_sent, "contact_attempts": f.contact_attempts, "case_seq": f.last_event_seq}


class OutreachHead(DepartmentHead):
    spec = HeadSpec("outreach", "Outreach", ("draft_message", "followup_call_prep"))

    def candidates(self, work, now):
        from app.agency import compat

        templates = compat.automation_templates_available()
        orders, deferred = [], []
        for w in work:
            f = w.facts
            lang = f.language or "en"
            hard = f.messages_sent >= 2 or f.escalation_level >= 1 or f.contact_attempts >= 2
            if f.state == "lost_contact" or (f.state == "patient_contacted" and f.contact_attempts >= 2):
                orders.append(TaskOrder(agent_type="followup_call_prep", case_id=f.case_id, reason=w.reason,
                                        inputs=_labels(f)))
                continue
            purpose = "result_notification" if f.state == "owner_accepted" and f.messages_sent == 0 else "reminder"
            if templates and not hard:
                deferred.append({"case_id": f.case_id, "reason": "automation_template"})
                continue
            orders.append(TaskOrder(agent_type="draft_message", case_id=f.case_id, reason=w.reason,
                                    inputs={**_labels(f), "purpose": purpose, "channel": _first_channel(f),
                                            "language": lang}))
        return DepartmentPlan(orders=orders, deferred=deferred)


class ReferralHead(DepartmentHead):
    spec = HeadSpec("referral_scheduling", "Referral & Scheduling",
                    ("reroute_referral", "prepare_handoff_pack", "booking_followup"))

    def candidates(self, work, now):
        kind = {"referral_rejected": "reroute_referral", "referral_needed": "prepare_handoff_pack",
                "booking": "booking_followup"}
        return DepartmentPlan(orders=[TaskOrder(agent_type=kind[w.reason], case_id=w.facts.case_id, reason=w.reason,
                                                inputs=_labels(w.facts)) for w in work if w.reason in kind])


class EvidenceHead(DepartmentHead):
    spec = HeadSpec("evidence_closure", "Evidence & Closure",
                    ("retrieve_evidence", "prepare_closure_pack", "flag_missing_evidence"))

    def candidates(self, work, now):
        kind = {"evidence_check": "retrieve_evidence", "awaiting_signoff": "prepare_closure_pack",
                "appointment_passed": "retrieve_evidence"}
        return DepartmentPlan(orders=[TaskOrder(agent_type=kind[w.reason], case_id=w.facts.case_id, reason=w.reason,
                                                inputs=_labels(w.facts)) for w in work if w.reason in kind])


class SupportHead(DepartmentHead):
    spec = HeadSpec("patient_support", "Patient Support", ("triage_reply", "callback_brief"))

    def candidates(self, work, now):
        orders = []
        for w in work:
            f = w.facts
            if w.reason == "pending_reply":
                for ref in f.pending_replies:
                    orders.append(TaskOrder(agent_type="triage_reply", case_id=f.case_id, key=ref[:36].lower(),
                                            reason=w.reason, inputs={**_labels(f), "reply_event_id": ref}))
            elif w.reason == "callback_requested":
                orders.append(TaskOrder(agent_type="callback_brief", case_id=f.case_id, reason=w.reason,
                                        inputs=_labels(f)))
        return DepartmentPlan(orders=orders)


class QualityHead(DepartmentHead):
    spec = HeadSpec("quality_ops", "Quality & Operations",
                    ("daily_summary", "anomaly_check", "fairness_watch", "sla_risk_explainer"))

    def candidates(self, work, now):
        from app.calendar import default_calendar

        day = default_calendar().local_date(now).isoformat()
        orders = [TaskOrder(agent_type="daily_summary", key=f"day:{day}", reason="daily", inputs={"day": day}),
                  TaskOrder(agent_type="anomaly_check", key=f"day:{day}", reason="daily", inputs={"day": day}),
                  TaskOrder(agent_type="fairness_watch", key=f"day:{day}", reason="daily", inputs={"day": day})]
        orders += [TaskOrder(agent_type="sla_risk_explainer", case_id=w.facts.case_id, reason=w.reason,
                             inputs=_labels(w.facts)) for w in work if w.reason == "sla_breach"]
        return DepartmentPlan(orders=orders)


HEADS: dict[str, DepartmentHead] = {h.spec.department: h for h in (
    OutreachHead(), ReferralHead(), EvidenceHead(), SupportHead(), QualityHead())}


# ---------------------------------------------------------------- the supervisor

SUPERVISOR_TOOLS = ("read_worklist", "read_metrics", "read_alerts", "read_ledger")


def route_case(f: CaseFacts, now: datetime) -> list[tuple[str, str]]:
    """Which departments a case needs, and why (a reason code). Rules only; the order of the worklist is not read."""
    if not f.open or f.consent_frozen:
        return []
    out: list[tuple[str, str]] = []
    if f.state in CONTACT_STATES and f.owner is not None:
        out.append(("outreach", {"owner_accepted": "awaiting_contact", "patient_contacted": "awaiting_acknowledgement",
                                 "lost_contact": "lost_contact"}[f.state]))
    if f.state == "referral_rejected":
        out.append(("referral_scheduling", "referral_rejected"))
    referral = f.milestones.get("referral_requested")
    if f.state == "patient_acknowledged" and f.pending_handoff_to is None and not (referral and referral.met):
        out.append(("referral_scheduling", "referral_needed"))
    if f.state == "confirmatory_booked":
        out.append(("referral_scheduling", "booking"))
    if f.state == "confirmatory_completed":
        out.append(("evidence_closure", "evidence_check"))
    if f.state == "clinician_reviewed":
        out.append(("evidence_closure", "awaiting_signoff"))
    if f.pending_replies:
        out.append(("patient_support", "pending_reply"))
    if f.callback_requested_at and (f.last_human_activity_at or "") <= f.callback_requested_at:
        out.append(("patient_support", "callback_requested"))
    if f.overdue or f.escalation_level > 0:
        out.append(("quality_ops", "sla_breach"))
    return out
