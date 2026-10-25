/**
 * Repository documents the judge pages point to. These are plain links (nothing is fetched by the app): the GitHub blob URL of
 * the public repository, plus the repo-relative path shown next to it so the document is found in a local checkout too.
 */
export const REPO_BLOB = "https://github.com/Nandish3010/screening-closure/blob/main/";

export interface DocLink { label: string; path: string }

export const DOCS = {
  evidence: { label: "Evidence base", path: "docs/evidence.md" },
  results: { label: "Evaluation results (baseline vs orchestrated)", path: "docs/results/results.md" },
  metrics: { label: "Metric definitions", path: "docs/results/metrics.md" },
  limits: { label: "Assumptions and limitations", path: "docs/assumptions-limitations.md" },
  provenance: { label: "Data provenance (all data synthetic)", path: "docs/data-provenance.md" },
  agency: { label: "The agency: agents prepare, people decide", path: "docs/governance/agency.md" },
  aiBoundary: { label: "AI boundary", path: "docs/governance/ai-boundary.md" },
  clinician: { label: "Clinician review and closure", path: "docs/governance/clinician-review.md" },
  riskCard: { label: "Risk score card", path: "docs/governance/risk-score-card.md" },
  demo: { label: "Demo script", path: "docs/submission/demo-script.md" },
  tech: { label: "Technical documentation", path: "docs/submission/technical-documentation.md" },
} satisfies Record<string, DocLink>;

export const docHref = (d: DocLink) => REPO_BLOB + d.path;
