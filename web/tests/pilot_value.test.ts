import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_INPUTS, PILOT, aed, compute, int, shadowCost } from "../src/lib/pilotValue";

const ROOT = resolve(__dirname, "../..");
const close = (a: number, b: number) => expect(Math.abs(a - b)).toBeLessThanOrEqual(1e-9 * Math.max(1, Math.abs(b)));

describe("pilot value calculator: the TypeScript port matches tools/pilot/cost_model.py", () => {
  it("shadowCost equals the Python model's shadow_pilot on every fixed reference input", () => {
    expect(PILOT.reference.length).toBeGreaterThanOrEqual(3);
    for (const ref of PILOT.reference) {
      const ts = shadowCost(ref.cases);
      const py = ref.shadow;
      close(ts.fits, py.fits); close(ts.cases, py.cases); close(ts.days, py.days);
      close(ts.hosting, py.hosting); close(ts.licences, py.licences); close(ts.support, py.support);
      close(ts.integration, py.integration); close(ts.governance, py.governance);
      close(ts.total, py.total); close(ts.costPerCase, py.cost_per_case);
    }
  });

  it("the default scenario is the documented one: the shadow run costs AED 105,828 for about 155 cases", () => {
    const out = compute(DEFAULT_INPUTS);
    expect(aed(out.shadow.total)).toBe("AED 105,828");
    expect(int(out.shadow.cases)).toBe("155");
    expect(aed(out.shadow.costPerCase)).toBe("AED 681");
    // the same figures are in the commercial documents the page cites
    const business = readFileSync(resolve(ROOT, "docs/commercial/business-case.md"), "utf8");
    expect(business).toContain("105,828");
    expect(business).toContain("681 per case");
  });

  it("the defaults come from the inputs file, the Abu Dhabi results and the market inputs", () => {
    const i = PILOT.inputs;
    close(DEFAULT_INPUTS.annualPositives, i.abu_dhabi_population_40_to_75.value * i.fit_participation_annual.value * i.fit_positivity_market.value);
    expect(DEFAULT_INPUTS.staffCostPerHour).toBe(i.coordinator_cost_aed_per_hour.value);
    expect(DEFAULT_INPUTS.currentCompletion).toBe(PILOT.completion.baseline_360d);
    expect(PILOT.synthetic).toBe(true);
  });

  it("extra completions are positives x (completion with the engine - current), with the engine's ratio, capped at 100%", () => {
    const base = compute({ ...DEFAULT_INPUTS, annualPositives: 1000, currentCompletion: 0.3 });
    close(base.completionWithEngine, 0.3 * PILOT.completion.ratio_360d);
    close(base.extraCompleted, 1000 * (0.3 * PILOT.completion.ratio_360d - 0.3));
    const capped = compute({ ...DEFAULT_INPUTS, annualPositives: 1000, currentCompletion: 0.95 });
    expect(capped.completionWithEngine).toBe(1);
    close(capped.extraCompleted, 50);
    expect(compute({ ...DEFAULT_INPUTS, annualPositives: 1000, currentCompletion: 0 }).extraCompleted).toBe(0);
  });

  it("staff minutes by role scale the effort model's minutes per case; the 40% target is not met", () => {
    const out = compute({ ...DEFAULT_INPUTS, annualPositives: 100, staffCostPerHour: 60 });
    for (const r of PILOT.effort.roles as (keyof typeof out.baselineMinutes)[]) {
      close(out.baselineMinutes[r], 100 * PILOT.effort.all.baseline[r]);
      close(out.engineMinutes[r], 100 * PILOT.effort.all.engine[r]);
    }
    // the results file averages the reduction over replications; the ratio of the mean minutes differs in the fifth decimal
    expect(Math.abs(out.reduction - PILOT.effort.all.reduction.labour_reduction)).toBeLessThan(1e-3);
    expect(out.reduction).toBeLessThan(PILOT.effort.target_reduction);
    // clinician and specialist minutes rise, coordinator minutes fall: the page must say so
    expect(PILOT.effort.all.reduction.owner_clinician).toBeLessThan(0);
    expect(PILOT.effort.all.reduction.specialist).toBeLessThan(0);
    expect(PILOT.effort.all.reduction.coordinator).toBeGreaterThan(0);
    close(out.baselineStaffCost, (out.baselineTotalMinutes / 60) * 60);
  });

  it("bad input cannot produce NaN, negative or infinite figures", () => {
    const out = compute({ annualPositives: Number.NaN, currentCompletion: -3, staffCostPerHour: Number.POSITIVE_INFINITY, pilotCases: -5 });
    for (const x of [out.extraCompleted, out.baselineTotalMinutes, out.engineStaffCost, out.shadow.total, out.shadow.costPerCase]) {
      expect(Number.isFinite(x)).toBe(true);
      expect(x).toBeGreaterThanOrEqual(0);
    }
  });

  it("fixed costs do not shrink with fewer cases, so the cost per case rises as cases fall", () => {
    expect(shadowCost(50).costPerCase).toBeGreaterThan(shadowCost(155).costPerCase);
    expect(shadowCost(50).total).toBeLessThan(shadowCost(155).total);
  });
});
