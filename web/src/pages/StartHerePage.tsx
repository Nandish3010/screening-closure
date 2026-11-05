import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import { CHALLENGE_STEPS, TODAY_ROUTE, useTour } from "../judge/JudgeTour";
import { seenSteps, useGuide } from "../judge/guide";
import { DemoStatusLine, ResetDemoControl } from "../components/DemoStatus";
import { BrandLockup } from "../components/Brand";

/**
 * "Start here" (ADR-057): the first screen a judge sees after signing in to the hosted demo. One card per step of the challenge
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

  return (
    <main className="container start-here" id="main-start" data-testid="start-here">
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Start here</h1>
      <p className="lead">The programme in about {minutes} minutes. Seven steps, one button each. Every button opens the real screen on a case nobody else has touched, and tells you what to look for. Everything is synthetic and runs on simulated time.</p>
      <p className="start-meta">
        <DemoStatusLine /> <ResetDemoControl />
      </p>
      {loaded && demoDeployment && guide === null && (
        <p className="muted" role="status" data-testid="start-no-guide">The guide could not pick cases just now, so each step opens the first fitting case instead. Everything still works.</p>
      )}
      <ol className="start-steps" data-testid="start-steps">
        {CHALLENGE_STEPS.map((s, i) => {
          const g = s.guide ? guide?.[s.guide] : undefined;
          const done = seen.has(s.id);
          return (
            <li key={s.id} className={`start-card${done ? " start-done" : ""}`} data-testid={`start-step-${s.id}`}>
              <h2>{s.title}{done && <span className="chip chip-ok start-seen"> seen</span>}</h2>
              <p className="start-claim">{s.claim}</p>
              <p className="muted start-meta">About {s.seconds ?? 45} s · best as the {s.role === "clinician" ? "clinician" : s.role}{loaded && guide && s.guide && !g ? " · no untouched case left, the first fitting case is used" : ""}</p>
              <button type="button" className="btn btn-primary" onClick={() => tour.open("challenge", i)} data-testid={`start-open-${s.id}`}>
                {done ? "Show me again" : "Show me"}
              </button>
            </li>
          );
        })}
      </ol>
      <p className="start-skip">
        <Link className="btn" to={skip} data-testid="start-skip">Skip the guide and go to {skip === TODAY_ROUTE ? "Today" : skip === "/patient" ? "the patient app" : "the worklist"}</Link>{" "}
        <Link to="/judge">Judge summary and documents</Link>
      </p>
      <p className="muted">You can reopen the guide at any time with <strong>Start here</strong> in the menu. The side panel on each screen is not modal: use the page behind it freely, then press Next.</p>
    </main>
  );
}
