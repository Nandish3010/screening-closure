import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import { CHALLENGE_STEPS, TODAY_ROUTE, useTour } from "../judge/JudgeTour";
import { seenSteps, useGuide } from "../judge/guide";
import { DemoStatusLine, ResetDemoControl } from "../components/DemoStatus";
import { BrandLockup } from "../components/Brand";
import headline from "../judge/headline.json";
import { DOCS, docHref } from "../judge/links";

const pct = (mean: number) => `${Math.round(mean * 100)}%`;

/** What the programme did and did not achieve, up front, from the generated headline (docs/results). Modelled, never measured on patients; the 40% labour target is not met. */
export function HonestResult() {
  const h = headline;
  return (
    <section className="honest-band on-dark" aria-labelledby="honest-h" data-testid="honest-result">
      <h2 id="honest-h">The honest result</h2>
      <p className="honest-big">
        <strong data-testid="honest-completion">{pct(h.completion_180d.baseline.mean)} → {pct(h.completion_180d.orchestrated.mean)}</strong> colonoscopy within 180 days <span className="honest-tag">modelled</span>
      </p>
      <p className="honest-big">
        <strong data-testid="honest-labour">labour −{pct(h.labour_reduction.literature_all.mean)}</strong>: <strong>the 40% target is not met</strong>
      </p>
      <p className="honest-note">From a simulation on synthetic data, anchored to published studies ({h.run.replications} runs of {h.run.cases_per_replication} cases). Not measured on real patients. <a href={docHref(DOCS.results)} target="_blank" rel="noopener noreferrer">How it was computed</a></p>
    </section>
  );
}

/**
 * "Start here" (ADR-058): the first screen a judge sees after signing in to the hosted demo. One card per step of the challenge
 * owner's loop (record, prioritise, refer, remind, track the outcome) plus the safety net and privacy and consent. Each card has a
 * one-line claim and one button that opens the exact screen and an untouched case for this browser session; the guided panel there says
 * what to look for. About five minutes in all, and skippable: nothing is locked.
 */
export function StartHerePage() {
  const { api, demoDeployment, role } = useApp();
  const tour = useTour();
  const { guide, loaded } = useGuide(api, demoDeployment);
  const [seen, setSeen] = useState<Set<string>>(seenSteps);
  useEffect(() => { const t = window.setInterval(() => setSeen(seenSteps()), 1500); return () => window.clearInterval(t); }, []);
  const minutes = Math.max(1, Math.round(CHALLENGE_STEPS.reduce((n, s) => n + (s.seconds ?? 45), 0) / 60));
  const skip = role === "patient" ? "/patient" : role === "coordinator" || role === "supervisor" ? TODAY_ROUTE : "/coordinator";

  const LOOP = ["record", "prioritise", "refer", "remind", "outcome"];
  const seenCount = LOOP.filter((id) => seen.has(id)).length;
  const next = LOOP.find((id) => !seen.has(id));
  const stepCard = (s: (typeof CHALLENGE_STEPS)[number], i: number) => {
    const g = s.guide ? guide?.[s.guide] : undefined;
    const done = seen.has(s.id);
    const status = done ? "seen" : s.id === next ? "next" : "todo";
    return (
      <li key={s.id} className={`start-card${done ? " start-done" : ""}`} data-testid={`start-step-${s.id}`} data-status={status} aria-current={status === "next" ? "step" : undefined}>
        <h2>{s.title}{" "}
          {status === "seen" && <span className="chip chip-ok start-seen" data-testid={`start-tag-${s.id}`}>Seen</span>}
          {status === "next" && <span className="chip chip-warn" data-testid={`start-tag-${s.id}`}>Up next</span>}
          {status === "todo" && LOOP.includes(s.id) && <span className="chip chip-neutral" data-testid={`start-tag-${s.id}`}>Not yet</span>}
        </h2>
        <p className="start-claim">{s.claim}</p>
        <p className="muted start-meta">About {s.seconds ?? 45} s · best as the {s.role === "clinician" ? "clinician" : s.role}{loaded && guide && s.guide && !g ? " · no untouched case left, the first fitting case is used" : ""}</p>
        <button type="button" className="btn btn-primary" onClick={() => tour.open("challenge", i)} data-testid={`start-open-${s.id}`}>
          {done ? "Show me again" : "Show me"}
        </button>
      </li>
    );
  };

  return (
    <main className="container start-here" id="main-start" data-testid="start-here">
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Start here</h1>
      <p className="lead">The programme in about {minutes} minutes. {CHALLENGE_STEPS.length} steps, one button each. Every button opens the real screen on a case nobody else has touched, and tells you what to look for. Everything is synthetic and runs on simulated time.</p>
      <p className="start-meta">
        <DemoStatusLine /> <ResetDemoControl />
      </p>
      {loaded && demoDeployment && guide === null && (
        <p className="muted" role="status" data-testid="start-no-guide">The guide could not pick cases just now, so each step opens the first fitting case instead. Everything still works.</p>
      )}
      <HonestResult />
      <div className="start-progress" data-testid="start-progress">
        <div role="progressbar" aria-label="Loop steps you have opened" aria-valuemin={0} aria-valuemax={LOOP.length} aria-valuenow={seenCount} className="start-bar"><span style={{ width: `${(seenCount / LOOP.length) * 100}%` }} /></div>
        <p role="status"><strong>{seenCount} of {LOOP.length}</strong> steps of the loop seen{seenCount === LOOP.length ? ". That is the whole loop." : "."}</p>
      </div>
      <ol className="start-steps" data-testid="start-steps">
        {CHALLENGE_STEPS.map((s, i) => {
          if (!LOOP.includes(s.id)) return null;
          return stepCard(s, i);
        })}
      </ol>
      <h2 className="start-then">Then see what keeps it safe</h2>
      <ol className="start-steps" data-testid="start-safety">
        {CHALLENGE_STEPS.map((s, i) => (LOOP.includes(s.id) ? null : stepCard(s, i)))}
      </ol>
      <p className="start-skip">
        <Link className="btn" to={skip} data-testid="start-skip">Skip the guide and go to {skip === TODAY_ROUTE ? "Today" : skip === "/patient" ? "the patient app" : "the worklist"}</Link>{" "}
        <Link to="/judge">Judge summary and documents</Link>
      </p>
      <p className="muted">You can reopen the guide at any time with <strong>Start here</strong> in the menu. The side panel on each screen is not modal: use the page behind it freely, then press Next.</p>
    </main>
  );
}
