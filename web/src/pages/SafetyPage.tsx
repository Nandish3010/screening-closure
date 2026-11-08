import { useId } from "react";
import { Link } from "react-router-dom";
import data from "../judge/safetyData.json";
import { DOCS, docHref, type DocLink } from "../judge/links";
import { PAGE_DOCS } from "../judge/pageLinks";
import "../judgePages.css";

/**
 * AI and safety (item 10). Static page. Every number comes from src/judge/safetyData.json, which `make judge-pages-data`
 * generates from docs/results/triage/*.json, docs/results/fairness.json and tools/triage/dataset/manifest.json. The words
 * are fixed text; there is no number in this file. Synthetic data throughout.
 */
const D = data;
type Ci = number[] | null;

export const pct = (x: number | null | undefined, digits = 1) => (x == null ? "not defined" : `${(x * 100).toFixed(digits)}%`);
export const range = (ci: Ci) => (ci ? `${pct(ci[0])} to ${pct(ci[1])}` : "no range");
const label = (s: string) => s.replace(/_/g, " ");

const KINDS = [
  {
    id: "rule", cls: "jp-kind-rule", name: "Deterministic rule",
    what: "Fixed code or configuration. The same input always gives the same answer, and a replay of the log reproduces it. You can read the rule.",
    examples: [
      "A case can only move to the next state if the pathway's state machine allows it. A colonoscopy cannot be marked completed before it was booked.",
      "A case becomes overdue when its due date passes on the pathway's clock, and the escalation ladder runs by fixed rungs.",
      "Who may do what is a table with default-deny: a role or service with no row in it can do nothing.",
    ],
  },
  {
    id: "calc", cls: "jp-kind-calc", name: "Calculated",
    what: "Arithmetic or a fitted score on top of the facts. It is reproducible and can be checked, but it is not a rule a person wrote line by line.",
    examples: [
      "The risk score orders the worklist. It never closes, escalates or decides care, and it is trained on simulator output.",
      "The dashboard's median and 90th-percentile times to closure, and the staff-minutes-by-role figures, are computed from the log and an effort model with assumed minutes.",
      "The reply classifier's confidence is a calibrated probability, used only to decide whether a person must read the reply.",
    ],
  },
  {
    id: "ai", cls: "jp-kind-ai", name: "AI-drafted",
    what: "A language model wrote or labelled something. It is always a draft or a label that a person or a fixed rule then checks. It cannot change a case on its own.",
    examples: [
      "Patient messages are drafted by a model (with a template fallback). A person approves each draft before it can be sent.",
      "A patient's free-text reply is labelled with one of nine intents by a small model. At worst it sets a flag asking a person to look; it moves no case.",
      "The agency's agents prepare briefs and recommendations. They have no permission to write any case event.",
    ],
  },
];

const GUARDS = [
  {
    name: "The closure guard",
    how: "A case closes only with a reference to evidence that exists and belongs to that case, and a clinician's signature. The state machine refuses any automated actor, whoever asks. An incomplete colonoscopy is recorded as incomplete and cannot close a case.",
    where: "model-checked invariant; decision matrix, closing a case",
  },
  {
    name: "Clinician signature",
    how: "Only the owning clinician roles can review and close, and the signature must be the reviewer's own identity. A coordinator, supervisor, lab or patient cannot close a case, by the route and again inside the state machine.",
    where: "decision matrix, physician review and closing a case",
  },
  {
    name: "Default-deny",
    how: "A route with no access rule is refused for everyone. An actor or service with no row in the permissions table cannot write any event. The HL7 adapter's token reaches one route and cannot close, sign, reassign or change consent.",
    where: "role-based access matrix; the HL7 adapter document",
  },
  {
    name: "The consent hold",
    how: "When a patient withdraws consent, outreach freezes, a person is asked to review, and a pending referral is held (and its timer stops) until a person records consent again. Nothing is sent to a withdrawn channel.",
    where: "patient app and privacy centre; decision matrix, consent changes",
  },
];

function Ci({ ci }: { ci: Ci }) {
  return <span className="muted"> ({range(ci)})</span>;
}

export function SafetyPage() {
  const uid = useId();
  const mlx = D.original, gg = D.hosted, ds = D.dataset;
  const langs = ["en", "ar", "arabizi", "mixed"] as const;
  const langName: Record<string, string> = { all: "All replies", en: "English", ar: "Arabic", arabizi: "Arabizi", mixed: "Mixed script", ar_msa: "Arabic, modern standard", ar_gulf: "Arabic, Gulf dialect" };
  const flagged = new Set(D.fairness.triage_flags.map((f) => f.group));
  const docs: DocLink[] = [
    DOCS.cardTriage, DOCS.cardDrafting, DOCS.cardTemplate, DOCS.cardRisk, DOCS.cardPlanner, PAGE_DOCS.modelCards, PAGE_DOCS.triageCard,
    PAGE_DOCS.hazardLog, PAGE_DOCS.dpia, PAGE_DOCS.responsibleAi, PAGE_DOCS.fairness, PAGE_DOCS.triageEval, PAGE_DOCS.triageEvalGguf,
    PAGE_DOCS.decisionMatrix, PAGE_DOCS.redTeam,
  ];

  return (
    <main className="container" data-testid="safety-page">
      <div className="page-head"><h1>AI and safety</h1><span className="chip chip-synthetic">Synthetic data</span></div>
      <p className="jp-lead">
        <strong>The model drafts and labels. People and fixed rules decide.</strong> This page shows which decisions are rules, which are calculated, which are AI-drafted,
        what stops an automated step from deciding care, and how well the one classifier in the loop does on synthetic test data.
      </p>

      <section aria-labelledby={`${uid}-kinds`} className="jp-section" data-testid="safety-kinds">
        <h2 id={`${uid}-kinds`}>Three kinds of decision, with an example of each</h2>
        <ul className="jp-cards">
          {KINDS.map((k) => (
            <li key={k.id} className="jp-card" data-testid={`kind-${k.id}`}>
              <span className={`jp-kind ${k.cls}`}>{k.name}</span>
              <p>{k.what}</p>
              <ul>{k.examples.map((e) => <li key={e} className="jp-example">{e}</li>)}</ul>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby={`${uid}-guards`} className="jp-section" data-testid="safety-guards">
        <h2 id={`${uid}-guards`}>A human decides: four guards</h2>
        <p className="jp-note">No model, scheduler, watchdog or adapter can close a case, sign for a clinician, or override consent. These are enforced in code and covered by tests, not only described.</p>
        <ul className="jp-cards">
          {GUARDS.map((g) => (
            <li key={g.name} className="jp-card">
              <h3>{g.name}</h3>
              <p>{g.how}</p>
              <p className="jp-example">Where: {g.where}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby={`${uid}-eval`} className="jp-section" data-testid="safety-eval">
        <h2 id={`${uid}-eval`}>Reply triage: how well it does</h2>
        <div className="banner banner-warn" data-testid="safety-synthetic">
          <strong>Synthetic.</strong> Every message below was written by the developers from templates. The figures measure how well the model generalises to new templates of the same
          kinds of message. They do not measure performance on real patients, whose language is wider and messier. The Arabic was written by developers, not by Arabic-speaking clinicians, and has had no native-speaker review.
        </div>

        <h3>How the labels were made</h3>
        <p className="jp-lead" data-testid="safety-labels">
          Each message was generated from a hand-written template that already carries its intent, so the label is the template's intent. No person labelled real replies.
          The templates, not the messages, were split into training, validation and test, so the test set holds only templates the model never saw. The generator is seeded
          (seed {D.dataset.seed}) and the {ds.total.toLocaleString("en-US")} messages are reproducible. The test split has {ds.n.test} messages from {ds.test_templates} templates.
        </p>

        <div className="jp-tablewrap">
          <table className="jp-table" data-testid="safety-n-per-class">
            <caption>Test messages per class (n), all synthetic</caption>
            <thead><tr><th scope="col">Intent</th><th scope="col" className="num">n</th></tr></thead>
            <tbody>
              {Object.entries(ds.test_by_label).map(([k, n]) => <tr key={k}><th scope="row">{label(k)}</th><td className="num">{n}</td></tr>)}
              <tr><th scope="row">Total</th><td className="num">{ds.n.test}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="jp-tablewrap">
          <table className="jp-table" data-testid="safety-n-per-language">
            <caption>Test messages per language group (n), all synthetic</caption>
            <thead><tr><th scope="col">Group</th><th scope="col" className="num">n</th></tr></thead>
            <tbody>
              {langs.map((l) => <tr key={l}><th scope="row">{langName[l]}</th><td className="num">{ds.test_by_language[l]}</td></tr>)}
            </tbody>
          </table>
        </div>

        <h3>Headline, original and hosted build, side by side</h3>
        <div className="jp-tablewrap">
          <table className="jp-table" data-testid="safety-headline">
            <caption>Test split, synthetic. The ranges are 95% intervals where the results files give one.</caption>
            <thead><tr><th scope="col">Measure</th><th scope="col" className="num">{mlx.label}</th><th scope="col" className="num">{gg.label}</th></tr></thead>
            <tbody>
              <tr><th scope="row">Accuracy of the top label</th><td className="num">{pct(mlx.accuracy)}</td><td className="num">{pct(gg.accuracy)}</td></tr>
              <tr><th scope="row">Macro F1</th><td className="num">{mlx.macro_f1.toFixed(3)}</td><td className="num">{gg.macro_f1.toFixed(3)}</td></tr>
              <tr><th scope="row">Precision of replies handled without a person</th><td className="num">{pct(mlx.auto_precision)}<Ci ci={mlx.auto_precision_ci} /></td><td className="num">{pct(gg.auto_precision)}<Ci ci={gg.auto_precision_ci} /></td></tr>
              <tr><th scope="row">Coverage: share handled without a person</th><td className="num">{pct(mlx.coverage)}</td><td className="num">{pct(gg.coverage)}</td></tr>
              <tr><th scope="row">Opt-outs wrongly handled automatically</th><td className="num">{mlx.opt_out_leaked} of {mlx.opt_out_total}</td><td className="num">{gg.opt_out_leaked} of {gg.opt_out_total}</td></tr>
            </tbody>
          </table>
        </div>
        <p className="jp-note" data-testid="safety-models">
          The hosted demo cannot run the original build, so it serves a different file: the same fine-tuned model converted to GGUF and quantised to Q8_0 (<span className="mono">{gg.model_id}</span>), run on our own server with no third-party AI.
          It is a different artefact, so it was scored again on the same synthetic test split. For comparison, a classical text baseline (naive Bayes on character n-grams) scored {pct(D.baseline.accuracy)} accuracy on the same split.
          "Handled without a person" means the predicted intent is one of the safe ones and the confidence reaches the threshold ({gg.threshold.toFixed(3)} on the hosted build); question, other and opt-out always go to a person.
        </p>

        <h3>Per language group, through the deployed decision path</h3>
        <p className="jp-note" data-testid="safety-dialect-rule">
          <strong>The dialect rule:</strong> a reply in Gulf dialect Arabic always goes to a person, and an Arabizi reply is handled automatically only above its own, higher confidence threshold ({gg.arabizi_threshold.toFixed(3)} on the hosted build; {gg.mlx_arabizi_threshold.toFixed(3)} on the original). Without the rule these two groups fell below the precision target. The rule trades coverage for safety. The detector's word lists were written with the test split in view, so read the with-rule figures as optimistic.
        </p>
        <div className="jp-tablewrap">
          <table className="jp-table" data-testid="safety-by-language">
            <caption>Hosted build (GGUF Q8_0), test split, synthetic. A shaded row is flagged in the fairness results.</caption>
            <thead>
              <tr>
                <th scope="col">Group</th><th scope="col" className="num">n</th><th scope="col" className="num">Accuracy</th>
                <th scope="col" className="num">Handled without a person</th><th scope="col" className="num">Precision of those, rule on (range)</th>
                <th scope="col" className="num">Precision, rule off (range)</th>
              </tr>
            </thead>
            <tbody>
              {(["all", ...langs] as const).map((l) => {
                const g = gg.by_language_with_rule[l];
                const off = l === "all" ? null : gg.per_language_no_rule[l];
                return (
                  <tr key={l} className={flagged.has(l) ? "jp-flag" : undefined}>
                    <th scope="row">{langName[l]}</th><td className="num">{g.n}</td><td className="num">{pct(g.accuracy)}</td>
                    <td className="num">{pct(g.coverage)}</td>
                    <td className="num">{pct(g.auto_precision)}<Ci ci={g.auto_precision_ci} /></td>
                    <td className="num">{off ? <>{pct(off.auto_precision)}<Ci ci={off.auto_precision_ci} /></> : "not shown"}</td>
                  </tr>
                );
              })}
              {(["ar_msa", "ar_gulf"] as const).map((l) => {
                const g = gg.by_dialect_with_rule[l];
                return (
                  <tr key={l} className={flagged.has(l) ? "jp-flag" : undefined}>
                    <th scope="row">{langName[l]}</th><td className="num">{g.n}</td><td className="num">{pct(g.accuracy)}</td>
                    <td className="num">{pct(g.coverage)}</td>
                    <td className="num">{g.auto_handled_n === 0 ? "none handled: always a person" : <>{pct(g.auto_precision)}<Ci ci={g.auto_precision_ci} /></>}</td>
                    <td className="num">not shown</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="jp-note" data-testid="safety-fairness">
          Group sizes are small, so the ranges are wide. The fairness check flags {D.fairness.triage_flags.length} triage gaps against its limit of {pct(D.fairness.thresholds.macro_recall_gap, 0)} in the recall gap to the best group:{" "}
          {D.fairness.triage_flags.map((f, i) => (
            <span key={f.group}>{i > 0 ? "; " : ""}{langName[f.group] ?? f.group} ({pct(f.value)})</span>
          ))}
          . Both are the groups the dialect rule covers. The lasting fix is real dialect data and a native-speaker review, which have not happened.
        </p>
      </section>

      <section aria-labelledby={`${uid}-docs`} className="jp-section">
        <h2 id={`${uid}-docs`}>Model cards, hazard log and data protection impact assessment</h2>
        <ul className="plain jp-links" data-testid="safety-docs">
          {docs.map((d) => (
            <li key={d.path}><a href={docHref(d)} title={d.path} target="_blank" rel="noopener noreferrer">{d.label}</a></li>
          ))}
        </ul>
        <p className="jp-note">These figures are read from the published triage and fairness results files (linked above) and can be regenerated from them.</p>
        <p><Link to="/judge">Back to the judge summary</Link></p>
      </section>
    </main>
  );
}
