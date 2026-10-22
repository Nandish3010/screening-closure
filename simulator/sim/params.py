"""Parameter loading and validation.

A parameters file is a YAML tree whose leaves are `{value, source, status}`. A node is a leaf when
it has any of those three keys; then all three are required and `source`/`status` must be non-empty
text. Anything else is a group. Several files can be layered (later files override earlier leaves),
which is how a research file of proposed values can be merged over `params.yaml`.

`Params` is the flat, validated store (dotted keys). `ModelParams` is the typed view the simulator
components read; it fails at load time, naming the key, if a required parameter is missing.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

LEAF_KEYS = ("value", "source", "status")
OPTIONAL_LEAF_KEYS = ("notes", "range", "range_basis", "fit_script", "dataset_sha256")  # notes: free text, ignored by the simulator; range: see Param
# fit_script and dataset_sha256 are required for status `empirical` (ticket B18) and ignored by the simulator.
STATUSES = ("sourced", "sourced-adjacent", "empirical", "calibrated", "assumed")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class ParamError(ValueError):
    pass


class AssumedParamsWarning(UserWarning):
    pass


@dataclass(frozen=True)
class Param:
    key: str
    value: Any
    source: str
    status: str
    notes: str = ""
    # Plausible range for the sensitivity sweep (`make sensitivity`): [low, high] in the value's own type (for a list
    # value, [low list, high list]); range_basis says where it comes from (a source, or one of the default rules in
    # simulator/sim/sensitivity.py). Not part of the parameter hash and never read by the simulator itself.
    range: tuple | None = None
    range_basis: str = ""


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _parse_range(prefix: str, node: dict, origin: str) -> tuple[tuple | None, str]:
    """`range: [low, high]` (numbers, or equal-length lists of numbers for a list value) needs a `range_basis` and must
    contain the value."""
    basis = node.get("range_basis")
    if "range" not in node:
        if basis:
            raise ParamError(f"{origin}: parameter {prefix!r} has range_basis but no range")
        return None, ""
    raw, value = node["range"], node["value"]
    if not isinstance(basis, str) or not basis.strip():
        raise ParamError(f"{origin}: parameter {prefix!r} has a range but no range_basis (say where the range comes from)")
    if not isinstance(raw, list) or len(raw) != 2:
        raise ParamError(f"{origin}: parameter {prefix!r}: range must be [low, high]")
    lo, hi = raw
    if _num(value):
        ok = _num(lo) and _num(hi) and lo <= hi and lo <= value <= hi
    elif isinstance(value, list) and value and all(_num(v) for v in value):
        ok = (isinstance(lo, list) and isinstance(hi, list) and len(lo) == len(hi) == len(value)
              and all(_num(v) for v in lo + hi) and all(a <= v <= b for a, v, b in zip(lo, value, hi)))
    else:
        ok = False
    if not ok:
        raise ParamError(f"{origin}: parameter {prefix!r}: range {raw} must be numeric, ordered low <= high, and contain the value {value!r}")
    return (tuple(lo) if isinstance(lo, list) else lo, tuple(hi) if isinstance(hi, list) else hi), basis.strip()


def _walk(node: Any, prefix: str, out: dict[str, Param], origin: str) -> None:
    if not isinstance(node, dict):
        raise ParamError(f"{origin}: {prefix or '<root>'} must be a mapping, got {type(node).__name__}")
    if any(k in node for k in LEAF_KEYS):
        missing = [k for k in LEAF_KEYS if k not in node]
        if missing:
            raise ParamError(f"{origin}: parameter {prefix!r} is missing {', '.join(missing)}")
        extra = [k for k in node if k not in LEAF_KEYS and k not in OPTIONAL_LEAF_KEYS]
        if extra:
            raise ParamError(f"{origin}: parameter {prefix!r} mixes value/source/status with child keys {extra}")
        for k in ("source", "status"):
            v = node[k]
            if not isinstance(v, str) or not v.strip():
                raise ParamError(f"{origin}: parameter {prefix!r} has an empty or non-text {k!r}")
        if node["value"] is None:
            raise ParamError(f"{origin}: parameter {prefix!r} has a null value")
        status = node["status"].strip()
        if status not in STATUSES:
            raise ParamError(f"{origin}: parameter {prefix!r} has status {status!r}, expected one of {', '.join(STATUSES)}")
        if status == "empirical":
            sha = node.get("dataset_sha256")
            if not isinstance(node.get("fit_script"), str) or not node["fit_script"].strip():
                raise ParamError(f"{origin}: empirical parameter {prefix!r} needs a fit_script")
            if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ParamError(f"{origin}: empirical parameter {prefix!r} needs dataset_sha256 (64 lowercase hex characters)")
        rng, basis = _parse_range(prefix, node, origin)
        out[prefix] = Param(prefix, node["value"], node["source"].strip(), node["status"].strip(),
                            str(node.get("notes") or "").strip(), rng, basis)
        return
    for k, child in node.items():
        _walk(child, f"{prefix}.{k}" if prefix else str(k), out, origin)


class Params:
    """Flat validated parameter store. Immutable after construction."""

    def __init__(self, params: dict[str, Param], files: tuple[str, ...] = ()):
        self._p = dict(sorted(params.items()))
        self.files = files

    # -- access --------------------------------------------------------------------------------
    def __contains__(self, key: str) -> bool:
        return key in self._p

    def param(self, key: str) -> Param:
        try:
            return self._p[key]
        except KeyError:
            raise ParamError(f"missing parameter {key!r}") from None

    def get(self, key: str) -> Any:
        return self.param(key).value

    def float(self, key: str) -> float:
        return float(self.get(key))

    def int(self, key: str) -> int:
        return int(self.get(key))

    def str(self, key: str) -> str:
        return str(self.get(key))

    def list(self, key: str) -> list:
        v = self.get(key)
        if not isinstance(v, list):
            raise ParamError(f"parameter {key!r} must be a list")
        return v

    def group(self, prefix: str) -> dict[str, Any]:
        """Direct children of a group as {child: value} (children that are groups are skipped)."""
        pre = prefix + "."
        out = {k[len(pre):]: p.value for k, p in self._p.items() if k.startswith(pre) and "." not in k[len(pre):]}
        if not out:
            raise ParamError(f"missing parameter group {prefix!r}")
        return out

    def rows(self) -> list[Param]:
        return list(self._p.values())

    def keys(self) -> list[str]:
        return list(self._p)

    # -- provenance ----------------------------------------------------------------------------
    def assumed(self) -> list[Param]:
        return [p for p in self._p.values() if p.status.lower() == "assumed"]

    def canonical(self) -> str:
        return json.dumps(
            {k: [p.value, p.source, p.status] for k, p in self._p.items()}, sort_keys=True, separators=(",", ":")
        )

    def hash(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def with_value(self, key: str, value: Any) -> "Params":
        """A copy with one parameter's value replaced (source, status and range unchanged); the sweep uses this."""
        p = self.param(key)
        new = dict(self._p)
        new[key] = Param(p.key, value, p.source, p.status, p.notes, p.range, p.range_basis)
        return Params(new, self.files)

    def sweep_hash(self) -> str:
        """Hash of every value AND range: identifies the sensitivity design (the plain `hash` ignores ranges)."""
        body = json.dumps({k: [p.value, p.status, p.range, p.range_basis] for k, p in self._p.items()},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()

    def warn_assumed(self) -> str | None:
        n, total = len(self.assumed()), len(self._p)
        if not n:
            return None
        msg = f"{n} of {total} simulator parameters are status 'assumed' (placeholders, not literature-sourced)"
        warnings.warn(msg, AssumedParamsWarning, stacklevel=3)
        return msg


def parse_params(data: Any, origin: str = "<memory>") -> dict[str, Param]:
    out: dict[str, Param] = {}
    _walk(data or {}, "", out, origin)
    return out


def load_params(*paths: str | Path, warn: bool = True) -> Params:
    """Load and layer one or more parameter files. Raises ParamError on any invalid leaf."""
    if not paths:
        raise ParamError("no parameter file given")
    merged: dict[str, Param] = {}
    for path in paths:
        text = Path(path).read_text()
        merged.update(parse_params(yaml.safe_load(text), str(path)))
    params = Params(merged, tuple(str(p) for p in paths))
    if warn:
        params.warn_assumed()
    return params


# ---------------------------------------------------------------------------------------------
# Typed view. Dataclasses are plain containers; every number comes from the Params store.
# ---------------------------------------------------------------------------------------------

def _share(params: Params, prefix: str) -> tuple[tuple[str, float], ...]:
    """A categorical distribution as sorted (name, weight) pairs, normalised to sum to 1."""
    g = params.group(prefix)
    total = sum(float(v) for v in g.values())
    if total <= 0:
        raise ParamError(f"{prefix}: shares must sum to a positive number")
    return tuple((k, float(g[k]) / total) for k in sorted(g))


def _weekend(working_week: dict) -> tuple[int, ...]:
    try:
        return tuple(sorted(WEEKDAYS[str(d).strip().lower()[:3]] for d in working_week["weekend"]))
    except (KeyError, TypeError) as exc:
        raise ParamError("working_week.weekend must list weekday names such as [Sat, Sun]") from exc


def _fgroup(params: Params, prefix: str) -> dict[str, float]:
    return {k: float(v) for k, v in params.group(prefix).items()}


def _capacity(p: "Params") -> "CapacityParams | None":
    if "capacity.enabled" not in p or not p.get("capacity.enabled"):
        return None
    return CapacityParams(
        providers=p.int("capacity.providers"),
        slots_per_session=p.float("capacity.slots_per_session"),
        sessions_per_week_per_1000_cases=p.float("capacity.sessions_per_week_per_1000_cases"),
        scale=p.float("capacity.scale"),
        dna_backfill_prob=p.float("capacity.dna_backfill_prob"),
        utilisation_window_start_day=p.int("capacity.utilisation_window_start_day"),
        utilisation_window_end_day=p.int("capacity.utilisation_window_end_day"),
        supervisor_minutes_per_day_per_1000_cases=p.float("capacity.staff.supervisor_minutes_per_day_per_1000_cases"),
        coordinator_minutes_per_day_per_1000_cases=p.float("capacity.staff.coordinator_minutes_per_day_per_1000_cases"),
        staffing_scale=p.float("capacity.staff.scale"),
        accept_minutes=p.float("capacity.staff.accept_minutes"),
        contact_minutes=p.float("capacity.staff.contact_minutes"),
    )


@dataclass(frozen=True)
class CapacityParams:
    """Finite colonoscopy slots and escalation-handling minutes (ticket B19, M6). Present only when a parameter file
    that defines `capacity.enabled: true` (simulator/params.capacity.yaml) is layered over params.yaml."""
    providers: int
    slots_per_session: float
    sessions_per_week_per_1000_cases: float
    scale: float
    dna_backfill_prob: float
    utilisation_window_start_day: int
    utilisation_window_end_day: int
    supervisor_minutes_per_day_per_1000_cases: float
    coordinator_minutes_per_day_per_1000_cases: float
    staffing_scale: float
    accept_minutes: float
    contact_minutes: float


@dataclass(frozen=True)
class ModelParams:
    # simulation / calendar
    start_date: str
    intake_window_days: float
    weekend_weekdays: tuple[int, ...]
    followup_target_working_days: int
    delay_harm_threshold_days: float
    dna_rate: float
    sms_reminder_effect: float
    completion_targets: dict[str, float]
    integrated_reference: dict[str, float]
    time_to_colonoscopy_median_days: float
    time_to_colonoscopy_iqr_days: tuple[float, float]
    navigation_effect: float
    holidays: tuple[str, ...]
    # patients
    language_share: tuple[tuple[str, float], ...]
    channel_share: tuple[tuple[str, float], ...]
    wrong_contact_rate: float
    provider_switch_prob: float
    responsiveness_sd: float
    consent_withdrawal_prob: float
    consent_withdrawal_window_days: float
    consent_withdrawal_declines_care_prob: float
    dropout_prob_per_lapse: float
    refusal_prob: float
    # staff
    staff_delay_median: dict[str, float]
    staff_delay_sigma: float
    staff_forget_prob: float
    # patient response
    response_rate: dict[str, float]
    response_delay_median: dict[str, float]
    response_delay_sigma: float
    language_mismatch_multiplier: float
    staff_call_response_rate: float
    coordinator_route_response_rate: float
    dropout_residual_multiplier: float
    human_dropout_residual_multiplier: float
    self_presentation_prob: float
    self_presentation_median_days: float
    # appointment
    no_show_rate: dict[str, float]
    booking_lead_median_days: float
    booking_lead_sigma: float
    rebook_lead_median_days: float
    reminder_days_before: tuple[float, ...]
    appointment_max_attempts: int
    responsiveness_noshow_shift: float
    # handoff
    handoff_no_response_prob: float
    referral_rejection_rate: float
    rejection_reason_share: tuple[tuple[str, float], ...]
    handoff_delay_median_days: float
    handoff_delay_sigma: float
    handoff_sla_working_days: int
    reroute_delay_median_days: float
    declined_reengage_prob: float
    handoff_max_attempts: int
    baseline_rejection_noticed_prob: float
    baseline_rejection_notice_median_days: float
    # baseline arm
    baseline_channel_share: tuple[tuple[str, float], ...]
    baseline_default_language: str
    noshow_self_rebook_prob: float
    noshow_self_rebook_median_days: float
    # orchestrated arm
    reaction_delay_median_days: float
    reaction_delay_sigma: float
    escalation_effect_prob: dict[str, float]
    contact_recovery_prob: dict[str, float]
    contact_recovery_delay_median_days: float
    delivery_failure_detect_days: dict[str, float]
    ladder_exhausted_wait_working_days: int
    signoff_sla_working_days: int
    timer_miss_prob: float
    programme_reach: float
    watchdog_idle_days: float
    watchdog_effect_prob: float
    watchdog_reaction_delay_days: float
    consent_review_delay_median_days: float
    transfer_reestablish_days: float
    # reopen
    reopen_prob: float
    reopen_lag_median_days: float
    reopen_lag_sigma: float
    reopen_resume_state: str
    # messy
    messy_multiplier: float
    duplicate_result_rate: float
    duplicate_delay_max_days: float
    conflicting_ack_rate: float
    consent_after_booking_rate: float
    switch_window_days: float
    baseline_switch_loss_prob: float
    baseline_switch_delay_days: float
    baseline_conflict_delay_days: float
    # metrics
    ltfu_idle_days: float
    escalation_acted_on_window_working_days: int
    min_group_size: int
    # finite capacity (B19); None = the unconstrained model every earlier result used
    capacity: "CapacityParams | None" = None

    @classmethod
    def from_params(cls, p: Params) -> "ModelParams":
        # phone is called "phone_call" in the no-show table of the original params file
        no_show = _fgroup(p, "no_show_rate_by_channel")
        if "phone_call" in no_show:
            no_show.setdefault("phone", no_show["phone_call"])
        mp = cls(
            start_date=p.str("simulation.start_date"),
            intake_window_days=p.float("simulation.intake_window_days"),
            weekend_weekdays=_weekend(p.get("working_week")),
            followup_target_working_days=p.int("followup_target_working_days"),
            delay_harm_threshold_days=p.float("delay_harm_threshold_days"),
            dna_rate=p.float("dna_rate"),
            sms_reminder_effect=p.float("sms_reminder_effect"),
            completion_targets={"90": p.float("colonoscopy_completion_3m"), "180": p.float("colonoscopy_completion_6m"),
                                "360": p.float("colonoscopy_completion_12m")},
            integrated_reference={"90": p.float("colonoscopy_completion_3m_integrated_ref"),
                                  "180": p.float("colonoscopy_completion_6m_integrated_ref"),
                                  "360": p.float("colonoscopy_completion_12m_integrated_ref")},
            time_to_colonoscopy_median_days=p.float("time_to_colonoscopy_median_days"),
            time_to_colonoscopy_iqr_days=tuple(float(x) for x in p.list("time_to_colonoscopy_iqr_days")),  # type: ignore[arg-type]
            navigation_effect=p.float("navigation_effect"),
            holidays=tuple(str(x) for x in p.list("calendar.holidays")),
            language_share=_share(p, "patients.language_share"),
            channel_share=_share(p, "patients.channel_share"),
            wrong_contact_rate=p.float("wrong_contact_rate"),
            provider_switch_prob=p.float("patients.provider_switch_prob"),
            responsiveness_sd=p.float("patients.responsiveness_sd"),
            consent_withdrawal_prob=p.float("patients.consent_withdrawal_prob"),
            consent_withdrawal_window_days=p.float("patients.consent_withdrawal_window_days"),
            consent_withdrawal_declines_care_prob=p.float("patients.consent_withdrawal_declines_care_prob"),
            dropout_prob_per_lapse=p.float("patients.dropout_prob_per_lapse"),
            refusal_prob=p.float("patients.refusal_prob"),
            staff_delay_median={
                **_fgroup(p, "staff.delay_median_days"),
                "acknowledge": p.float("acknowledgement_delay_days"),
            },
            staff_delay_sigma=p.float("staff.delay_sigma"),
            staff_forget_prob=p.float("staff.forget_prob"),
            response_rate=_fgroup(p, "patient_response.response_rate_by_channel"),
            response_delay_median=_fgroup(p, "patient_response.response_delay_median_days"),
            response_delay_sigma=p.float("patient_response.response_delay_sigma"),
            language_mismatch_multiplier=p.float("patient_response.language_mismatch_multiplier"),
            staff_call_response_rate=p.float("patient_response.staff_call_response_rate"),
            coordinator_route_response_rate=p.float("patient_response.coordinator_route_response_rate"),
            dropout_residual_multiplier=p.float("patient_response.dropout_residual_multiplier"),
            human_dropout_residual_multiplier=p.float("patient_response.human_dropout_residual_multiplier"),
            self_presentation_prob=p.float("patient_response.self_presentation_prob"),
            self_presentation_median_days=p.float("patient_response.self_presentation_median_days"),
            no_show_rate=no_show,
            booking_lead_median_days=p.float("appointment.booking_lead_median_days"),
            booking_lead_sigma=p.float("appointment.booking_lead_sigma"),
            rebook_lead_median_days=p.float("appointment.rebook_lead_median_days"),
            reminder_days_before=tuple(float(x) for x in p.list("appointment.reminder_days_before")),
            appointment_max_attempts=p.int("appointment.max_attempts"),
            responsiveness_noshow_shift=p.float("appointment.responsiveness_noshow_shift"),
            handoff_no_response_prob=p.float("handoff.no_response_prob"),
            referral_rejection_rate=p.float("referral_rejection_rate"),
            rejection_reason_share=_share(p, "handoff.rejection_reason_share"),
            handoff_delay_median_days=p.float("handoff.response_delay_median_days"),
            handoff_delay_sigma=p.float("handoff.response_delay_sigma"),
            handoff_sla_working_days=p.int("handoff.response_sla_working_days"),
            reroute_delay_median_days=p.float("handoff.reroute_delay_median_days"),
            declined_reengage_prob=p.float("handoff.declined_reengage_prob"),
            handoff_max_attempts=p.int("handoff.max_attempts"),
            baseline_rejection_noticed_prob=p.float("handoff.baseline_rejection_noticed_prob"),
            baseline_rejection_notice_median_days=p.float("handoff.baseline_rejection_notice_median_days"),
            baseline_channel_share=_share(p, "baseline.channel_share"),
            baseline_default_language=p.str("baseline.default_language"),
            noshow_self_rebook_prob=p.float("baseline.noshow_self_rebook_prob"),
            noshow_self_rebook_median_days=p.float("baseline.noshow_self_rebook_median_days"),
            reaction_delay_median_days=p.float("orchestration.reaction_delay_median_days"),
            reaction_delay_sigma=p.float("orchestration.reaction_delay_sigma"),
            escalation_effect_prob=_fgroup(p, "orchestration.escalation_effect_prob"),
            contact_recovery_prob=_fgroup(p, "orchestration.contact_recovery_prob"),
            contact_recovery_delay_median_days=p.float("orchestration.contact_recovery_delay_median_days"),
            delivery_failure_detect_days=_fgroup(p, "orchestration.delivery_failure_detect_days"),
            ladder_exhausted_wait_working_days=p.int("orchestration.ladder_exhausted_wait_working_days"),
            signoff_sla_working_days=p.int("orchestration.signoff_sla_working_days"),
            timer_miss_prob=p.float("orchestration.timer_miss_prob"),
            programme_reach=p.float("orchestration.programme_reach"),
            watchdog_idle_days=p.float("orchestration.watchdog_idle_days"),
            watchdog_effect_prob=p.float("orchestration.watchdog_effect_prob"),
            watchdog_reaction_delay_days=p.float("orchestration.watchdog_reaction_delay_days"),
            consent_review_delay_median_days=p.float("orchestration.consent_review_delay_median_days"),
            transfer_reestablish_days=p.float("orchestration.transfer_reestablish_days"),
            reopen_prob=p.float("reopen.after_closure_prob"),
            reopen_lag_median_days=p.float("reopen.lag_median_days"),
            reopen_lag_sigma=p.float("reopen.lag_sigma"),
            reopen_resume_state=p.str("reopen.resume_state"),
            messy_multiplier=p.float("messy.rate_multiplier"),
            duplicate_result_rate=p.float("messy.duplicate_result_rate"),
            duplicate_delay_max_days=p.float("messy.duplicate_delay_max_days"),
            conflicting_ack_rate=p.float("messy.conflicting_ack_rate"),
            consent_after_booking_rate=p.float("messy.consent_after_booking_rate"),
            switch_window_days=p.float("messy.switch_window_days"),
            baseline_switch_loss_prob=p.float("messy.baseline_switch_loss_prob"),
            baseline_switch_delay_days=p.float("messy.baseline_switch_delay_days"),
            baseline_conflict_delay_days=p.float("messy.baseline_conflict_delay_days"),
            ltfu_idle_days=p.float("metrics.ltfu_idle_days"),
            escalation_acted_on_window_working_days=p.int("metrics.escalation_precision_window_working_days"),
            min_group_size=p.int("metrics.min_group_size"),
            capacity=_capacity(p),
        )
        mp.validate()
        return mp

    def validate(self) -> None:
        def prob(name: str, v: float) -> None:
            if not 0.0 <= v <= 1.0:
                raise ParamError(f"{name} must be in [0, 1], got {v}")

        for name in (
            "wrong_contact_rate provider_switch_prob consent_withdrawal_prob "
            "consent_withdrawal_declines_care_prob dropout_prob_per_lapse refusal_prob staff_forget_prob "
            "language_mismatch_multiplier staff_call_response_rate coordinator_route_response_rate "
            "dropout_residual_multiplier human_dropout_residual_multiplier self_presentation_prob "
            "sms_reminder_effect dna_rate handoff_no_response_prob referral_rejection_rate "
            "declined_reengage_prob baseline_rejection_noticed_prob noshow_self_rebook_prob timer_miss_prob "
            "watchdog_effect_prob programme_reach reopen_prob duplicate_result_rate conflicting_ack_rate "
            "consent_after_booking_rate baseline_switch_loss_prob"
        ).split():
            prob(name, getattr(self, name))
        for table in (self.response_rate, self.no_show_rate, self.escalation_effect_prob, self.contact_recovery_prob):
            for k, v in table.items():
                prob(k, v)
        if self.appointment_max_attempts > 4:
            raise ParamError("appointment.max_attempts is capped at 4 (size of the pre-drawn latent pool)")
        if self.handoff_max_attempts > 5:
            raise ParamError("handoff.max_attempts is capped at 5 (size of the pre-drawn latent pool)")
        cp = self.capacity
        if cp is not None:
            prob("capacity.dna_backfill_prob", cp.dna_backfill_prob)
            if cp.providers < 1 or cp.slots_per_session <= 0 or cp.sessions_per_week_per_1000_cases <= 0 or cp.scale <= 0:
                raise ParamError("capacity: providers >= 1 and slots_per_session, sessions_per_week_per_1000_cases and scale must be positive")
            if cp.staffing_scale <= 0 or not 0 <= cp.utilisation_window_start_day < cp.utilisation_window_end_day:
                raise ParamError("capacity: staff.scale must be positive and 0 <= utilisation_window_start_day < utilisation_window_end_day")
        if self.handoff_no_response_prob + self.referral_rejection_rate > 1:
            raise ParamError("handoff.no_response_prob + referral_rejection_rate must be <= 1")
