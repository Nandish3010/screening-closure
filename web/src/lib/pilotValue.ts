/**
 * Pilot value calculator (item 13): a port of the repo's own cost model, tools/pilot/cost_model.py (`shadow_pilot`), plus
 * the completion and staff-minute arithmetic described on the page. Every constant comes from src/judge/pilotValue.json,
 * which `make judge-pages-data` generates from tools/pilot/inputs.yaml and docs/results/*.json. No number is typed here.
 * Illustrative and synthetic: the inputs are assumptions, the effects are simulator output.
 *
 * web/tests/pilot_value.test.ts checks this port against the Python model's own outputs (the `reference` block).
 */
import data from "../judge/pilotValue.json";

export type PilotData = typeof data;
export const PILOT: PilotData = data;

export interface PilotInputs {
  annualPositives: number;
  currentCompletion: number; // 0..1
  staffCostPerHour: number;
  pilotCases: number;
}

export const DEFAULT_INPUTS: PilotInputs = {
  annualPositives: PILOT.defaults.annual_positives,
  currentCompletion: PILOT.defaults.current_completion,
  staffCostPerHour: PILOT.defaults.staff_cost_per_hour,
  pilotCases: PILOT.defaults.pilot_cases,
};

export interface ShadowCost {
  fits: number;
  cases: number;
  days: number;
  hosting: number;
  licences: number;
  support: number;
  integration: number;
  governance: number;
  total: number;
  costPerCase: number;
}

/** `cost_model.shadow_pilot` for a given number of positive cases (providers held at the documented number). */
export function shadowCost(cases: number): ShadowCost {
  const v = (k: keyof PilotData["inputs"]) => PILOT.inputs[k].value;
  const n = v("pilot_providers");
  const days = v("shadow_days");
  const fits = cases / v("positivity_rate");
  const hosting = v("shadow_hosting_share_of_full") * (v("hosting_fixed_aed_per_month") * 12 * days / 365
    + v("hosting_variable_aed_per_1000_cases_per_year") * cases / 1000);
  const licences = v("third_party_licences_aed_per_year") * v("shadow_licences_share_of_full") * days / 365;
  const support = v("support_fte_loaded_cost_aed_per_year") * v("shadow_support_fte") * days / 365;
  const integration = n * v("shadow_integration_one_off_aed_per_provider");
  const governance = v("shadow_governance_review_one_off_aed");
  const total = hosting + licences + support + integration + governance;
  return { fits, cases, days, hosting, licences, support, integration, governance, total, costPerCase: cases > 0 ? total / cases : 0 };
}

export interface RoleMinutes { coordinator: number; owner_clinician: number; specialist: number }
export const ROLE_LABEL: Record<keyof RoleMinutes, string> = {
  coordinator: "Coordinator",
  owner_clinician: "Owner clinician",
  specialist: "Specialist",
};

export interface PilotOutputs {
  completionWithEngine: number;
  extraCompleted: number;
  baselineMinutes: RoleMinutes;
  engineMinutes: RoleMinutes;
  baselineTotalMinutes: number;
  engineTotalMinutes: number;
  /** Positive = fewer minutes with the engine. Matches the effort model's labour_reduction. */
  reduction: number;
  /** Staff cost of all roles' minutes at the one hourly rate the user entered (the repo's own simplification). */
  baselineStaffCost: number;
  engineStaffCost: number;
  shadow: ShadowCost;
}

const sum = (m: RoleMinutes) => m.coordinator + m.owner_clinician + m.specialist;
const scale = (m: RoleMinutes, k: number): RoleMinutes => ({
  coordinator: m.coordinator * k, owner_clinician: m.owner_clinician * k, specialist: m.specialist * k,
});

/**
 * Completion: the engine multiplies the current 360-day completion by the simulated ratio of the Abu Dhabi scenario
 * (orchestrated over baseline, docs/results/abudhabi.json), capped at 100%. Minutes: the effort model's minutes per case
 * by role (docs/results/results.json, literature-anchored, all cases) times the annual positives.
 */
export function compute(input: PilotInputs): PilotOutputs {
  const clamp = (x: number) => Math.min(1, Math.max(0, Number.isFinite(x) ? x : 0));
  const positives = Math.max(0, Number.isFinite(input.annualPositives) ? input.annualPositives : 0);
  const current = clamp(input.currentCompletion);
  const completionWithEngine = Math.min(1, current * PILOT.completion.ratio_360d);
  const extraCompleted = positives * (completionWithEngine - current);
  const baselineMinutes = scale(PILOT.effort.all.baseline, positives);
  const engineMinutes = scale(PILOT.effort.all.engine, positives);
  const rate = Math.max(0, Number.isFinite(input.staffCostPerHour) ? input.staffCostPerHour : 0);
  const b = sum(baselineMinutes);
  const e = sum(engineMinutes);
  return {
    completionWithEngine, extraCompleted, baselineMinutes, engineMinutes,
    baselineTotalMinutes: b, engineTotalMinutes: e, reduction: b > 0 ? 1 - e / b : 0,
    baselineStaffCost: b / 60 * rate, engineStaffCost: e / 60 * rate,
    shadow: shadowCost(Math.max(0, Number.isFinite(input.pilotCases) ? input.pilotCases : 0)),
  };
}

export const aed = (x: number) => `AED ${Math.round(x).toLocaleString("en-US")}`;
export const int = (x: number) => Math.round(x).toLocaleString("en-US");
export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
