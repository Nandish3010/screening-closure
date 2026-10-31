import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { PROGRAMME_LABOUR } from "../src/lib/programmeFigures";
import { STAFF } from "../src/i18n/strings";

const results = JSON.parse(readFileSync(resolve(process.cwd(), "../docs/results/results.json"), "utf-8"));
const eff = results.effort.scenarios;

describe("the programme figures the interface quotes are the committed results", () => {
  it("matches docs/results/results.json (rounded)", () => {
    expect(Math.round(eff.literature_anchored.in_reach.reduction.labour_reduction.mean * 100)).toBe(PROGRAMME_LABOUR.inReachPct);
    expect(Math.round(eff.literature_anchored.all.reduction.labour_reduction.mean * 100)).toBe(PROGRAMME_LABOUR.programmePct);
    expect(PROGRAMME_LABOUR.targetPct).toBe(40);
    expect(eff.literature_anchored.in_reach.reduction.labour_reduction.mean).toBeLessThan(0.4);
    expect(eff.literature_anchored.all.reduction.labour_reduction.mean).toBeLessThan(0.4);
  });
  it("is what the effort card says, with the target not met", () => {
    expect(STAFF.effortCard.programme).toContain(`${PROGRAMME_LABOUR.inReachPct}% fewer`);
    expect(STAFF.effortCard.programme).toContain(`${PROGRAMME_LABOUR.programmePct}% across`);
    expect(STAFF.effortCard.programme).toMatch(/target is not met/);
  });
});
