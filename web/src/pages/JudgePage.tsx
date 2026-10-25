import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import { DOCS, docHref, type DocLink } from "../judge/links";
import { useTour } from "../judge/JudgeTour";

/**
 * Judge summary (C13): one static page. Nothing here is fetched. The headline figures are quoted from
 * docs/results/results.md (literature-anchored scenario, before capacity limits); they do not update when the evaluation is
 * re-run, so the page names the document and the command that regenerates it.
 */
const HEADLINE: { value: string; label: string; note: string }[] = [
  { value: "64.9% vs 51.1%", label: "Colonoscopy completed within 180 days (orchestrated vs baseline)", note: "A ratio of 1.27, which is the pooled patient-navigation risk ratio the scenario is anchored to: an input from the literature, not a finding of this project." },
  { value: "65.6% vs 52.3%", label: "Cases closed with evidence and a human sign-off by end of follow-up", note: "Orchestrated vs baseline, 360 days." },
  { value: "0.0% vs 45.7%", label: "Lost to follow-up and never shown to a human, among cases the programme reaches", note: "Zero by construction of the escalation ladder; across all cases (including those never reached) it is 20.8%." },
  { value: "18.3%", label: "Reduction in human time per case, all cases (33.4% among cases reached)", note: "The 40% target is not met. The minutes per action are assumed, not measured." },
];

const CAVEATS = [
  "Every case, patient and number is synthetic. The results come from a simulator, not from patients or a trial.",
  "The literature-anchored scenario is the one to quote; the upper-range scenario is a ceiling for the mechanism, not a forecast.",
  "Minutes saved are assumed (no time-and-motion study yet), so the labour percentage is an estimate to be sourced.",
  "The risk score only orders the worklist; it never closes, escalates or decides care, and it is trained on simulator output.",
  "AI drafts need a person's approval. The automatic messages in the demo run on a demo approval, not a clinical lead's signature.",
  "Arabic strings were written by engineering and are marked for native-speaker review before any patient use.",
  "Nothing here diagnoses anyone.",
];

const DOC_LIST: DocLink[] = [DOCS.demo, DOCS.tech, DOCS.results, DOCS.metrics, DOCS.limits, DOCS.provenance, DOCS.agency, DOCS.aiBoundary, DOCS.clinician, DOCS.riskCard, DOCS.evidence];

export function JudgePage() {
  const { api } = useApp();
  const tour = useTour();
  return (
    <main className="container" data-testid="judge-page">
      <div className="page-head"><h1>Judge summary</h1><span className="chip chip-synthetic">Synthetic data</span></div>

      <div className="judge-hero" data-testid="judge-positioning">
        <p><strong>Every actionable screening result has a named owner, a clock, an audit trail and a clinician-signed closure.</strong> Nothing is diagnosed.</p>
      </div>

      <p>
        <button type="button" className="btn btn-primary" onClick={tour.open} data-testid="judge-start-tour">Start the guided tour</button>{" "}
        <span className="muted">Seven steps on the data you are looking at (the {api.adapter} adapter). It only navigates; it posts nothing.</span>
      </p>

      <section aria-labelledby="headline-h">
        <h2 id="headline-h">Headline, with its caveats</h2>
        <p className="muted">Literature-anchored scenario, 20 replications of 2,000 synthetic cases, 360 days of follow-up, quoted from <span className="mono">docs/results/results.md</span>.</p>
        <dl className="judge-grid" data-testid="judge-headline">
          {HEADLINE.map((h) => (
            <div key={h.label} className="judge-stat">
              <dt>{h.label}</dt>
              <dd><span className="kpi-value">{h.value}</span><span className="kpi-note">{h.note}</span></dd>
            </div>
          ))}
        </dl>
        <div className="banner banner-warn" data-testid="judge-caveats">
          <strong>Read these with the numbers</strong>
          <ul>{CAVEATS.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      </section>

      <section aria-labelledby="docs-h">
        <h2 id="docs-h">Documents in the repository</h2>
        <ul className="plain judge-docs" data-testid="judge-docs">
          {DOC_LIST.map((d) => (
            <li key={d.path}><a href={docHref(d)} target="_blank" rel="noopener noreferrer">{d.label}</a> <span className="mono muted">{d.path}</span></li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="run-h">
        <h2 id="run-h">Run it and check it yourself</h2>
        <p>From a clone of the repository:</p>
        <code className="judge-cmd mono" data-testid="judge-make-check">make check</code>
        <p className="muted">The fast tier, the gate before every merge: the API and database tests, the web unit and browser tests (including the accessibility audit), the web build, the docs checks, the security and red-team tests, and a quick determinism check of the evaluation. <span className="mono">make check-full</span> adds the simulator-heavy checks, including <span className="mono">make evaluate-check</span>, which regenerates the results and fails on any difference.</p>
        <code className="judge-cmd mono">make web-install &amp;&amp; make web-dev</code>
        <p className="muted">The web app on <span className="mono">http://localhost:5173</span> with the mock adapter, fully offline. <span className="mono">make web-test</span> and <span className="mono">make web-e2e</span> run the web unit and browser tests on their own.</p>
      </section>

      <p><Link to="/coordinator">Open the worklist</Link> · <Link to="/dashboard">Open the dashboard</Link></p>
    </main>
  );
}
