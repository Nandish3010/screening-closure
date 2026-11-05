import { DOCS, type DocLink } from "./links";

/** Repository documents the three judge pages (AI and safety, Integrations, Pilot value) link to. Kept apart from links.ts. */
export const PAGE_DOCS = {
  hazardLog: { label: "Hazard log", path: "docs/governance/hazard-log.md" },
  dpia: { label: "Data protection impact assessment (DPIA)", path: "docs/governance/dpia.md" },
  modelCards: { label: "Model cards: index", path: "docs/governance/model-cards/README.md" },
  responsibleAi: { label: "Responsible AI", path: "docs/governance/responsible-ai.md" },
  triageCard: { label: "Reply triage: long-form card", path: "docs/governance/reply-triage-card.md" },
  fairness: { label: "Fairness results by group", path: "docs/results/fairness.md" },
  triageEval: { label: "Reply triage evaluation (original model)", path: "docs/results/triage/eval.md" },
  triageEvalGguf: { label: "Reply triage evaluation (hosted GGUF Q8_0 build)", path: "docs/results/triage/eval-gguf-q8_0.md" },
  decisionMatrix: { label: "Who may do what: the decision matrix", path: "docs/governance/decision-matrix.md" },
  redTeam: { label: "Red team", path: "docs/governance/red-team.md" },
  hl7Adapter: { label: "HL7 v2 adapter: what is built, what is not", path: "docs/governance/hl7-adapter.md" },
  malaffiPlan: { label: "Malaffi adapter plan (a target, not a claim)", path: "docs/governance/malaffi-adapter-plan.md" },
  patientApp: { label: "Patient app and privacy centre", path: "docs/governance/patient-app.md" },
  pilotPlan: { label: "Pilot plan", path: "docs/pilot-plan.md" },
  costInputs: { label: "Cost model inputs (every input graded assumed or sourced)", path: "tools/pilot/inputs.yaml" },
  businessCase: { label: "Business case (pricing and unit economics)", path: "docs/commercial/business-case.md" },
  goToMarket: { label: "Go-to-market: the 90-day shadow run", path: "docs/commercial/go-to-market.md" },
  ...{ cardDrafting: DOCS.cardDrafting, cardTriage: DOCS.cardTriage, cardRisk: DOCS.cardRisk, cardPlanner: DOCS.cardPlanner, cardTemplate: DOCS.cardTemplate },
} satisfies Record<string, DocLink>;
