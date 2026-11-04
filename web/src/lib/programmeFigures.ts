/**
 * The programme-level labour figures the UI quotes. One place; tests/programme_figures.test.ts ties them to
 * docs/results/results.json (rounded), so a regenerated evaluation cannot leave a stale number in the interface.
 */
export const PROGRAMME_LABOUR = { inReachPct: 36, programmePct: 20, targetPct: 40 } as const;
