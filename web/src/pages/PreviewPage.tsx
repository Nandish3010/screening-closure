import { Link } from "react-router-dom";
import { PrototypeStrip } from "../components/DemoBanner";
import { BrandLockup } from "../components/Brand";
import headline from "../judge/headline.json";
import { HonestResult } from "./StartHerePage";

/**
 * "Preview without signing in" (/preview): public and read-only, for a reader who has no credentials. It holds no live data and
 * makes no API call of its own: the five screens are static screenshots (public/preview/*.webp, made by
 * `node web/scripts/preview-shots.mjs` from the app running against its mock adapter, synthetic data), and the numbers are the
 * generated headline (src/judge/headline.json) that the Judge summary and Start here already quote. Modelled, not measured.
 */
const H = headline;

export const PREVIEW_STEPS: { id: string; title: string; src: string; caption: string; alt: string }[] = [
  {
    id: "record",
    title: "1. Record",
    src: "/preview/record.webp",
    caption: "A positive result becomes a case with a named owner, a clock and a timeline of everything that happens to it.",
    alt: "Screenshot of a case page: the sign-off panel on the left and, on the right, the timeline from the positive result received to the AI draft, its approval by a person and the message sent to the patient.",
  },
  {
    id: "prioritise",
    title: "2. Prioritise",
    src: "/preview/prioritise.webp",
    caption: "The worklist shows each case's priority, age, state, owner and what is overdue. The risk score only orders the list.",
    alt: "Screenshot of the worklist table with a priority column (urgent, soon), pathway, age, state, owner, next due date and overdue flags for synthetic cases.",
  },
  {
    id: "refer",
    title: "3. Refer",
    src: "/preview/refer.webp",
    caption: "A referral shows the insurance plan, the owner and the clocks; ownership stays with the referrer until the other side accepts.",
    alt: "Screenshot of the top of a case page: owner, SLA clock, insurance plan, referral and colonoscopy deadlines, consent, and a rejected hand-off to a named service that is being re-routed.",
  },
  {
    id: "remind",
    title: "4. Reminders and navigation",
    src: "/preview/remind.webp",
    caption: "The patient's own app, in English or Arabic, says only that a result is ready and what happens next. Reminders arrive here.",
    alt: "Screenshot of the patient app in a phone frame: a card saying the screening result is ready and the care team will explain it in person or by phone, an emergency card, and an English and Arabic switch.",
  },
  {
    id: "outcome",
    title: "5. Outcome",
    src: "/preview/outcome.webp",
    caption: "The closure funnel counts cases at each step, from result recorded to outcome documented with a person's sign-off.",
    alt: "Screenshot of the closure funnel: result recorded, higher-risk people prioritised, referred, attended and outcome documented, each with a count, a share of the previous step and a median time.",
  },
];

export function PreviewPage() {
  return (
    <main className="container preview-page" id="main" data-testid="preview-page">
      <PrototypeStrip />
      <p className="signin-brand"><BrandLockup large /></p>
      <h1>Preview: screenshots of synthetic data</h1>
      <p className="lead">No sign-in needed. These are still pictures of the five steps of the programme, taken from the web app running on its built-in mock data. Nothing on this page is live, and it reads nothing from the server.</p>
      <p className="preview-actions"><Link className="btn btn-primary" to="/" data-testid="preview-back">Back to sign in</Link></p>

      <ol className="preview-steps plain" data-testid="preview-steps">
        {PREVIEW_STEPS.map((s, i) => (
          <li key={s.id} data-testid={`preview-step-${s.id}`}>
            <figure>
              <h2>{s.title}</h2>
              <img src={s.src} alt={s.alt} width={1280} height={800} loading={i < 2 ? "eager" : "lazy"} fetchPriority={i === 0 ? "high" : undefined} decoding="async" />
              <figcaption>{s.caption}</figcaption>
            </figure>
          </li>
        ))}
      </ol>

      <section aria-labelledby="preview-numbers-h" data-testid="preview-numbers">
        <h2 id="preview-numbers-h">The headline numbers, modelled and not a programme result</h2>
        <HonestResult />
        <ul className="preview-figures">
          <li>Colonoscopy completed within 180 days: <strong>{H.completion_180d.baseline.value} to {H.completion_180d.orchestrated.value}</strong> (baseline to orchestrated; a ratio of {H.completion_180d.ratio.value}, calibrated to published navigation trials). <span className="honest-tag">modelled</span></li>
          <li>Closed with evidence and a human sign-off by the end of follow-up: <strong>{H.closed_share.baseline.value} to {H.closed_share.orchestrated.value}</strong> (baseline to orchestrated, 360 days). <span className="honest-tag">modelled</span></li>
          <li>Lost to follow-up and never shown to a human, programme level: <strong>{H.silent_loss.baseline.value} to {H.silent_loss.orchestrated_programme.value}</strong> (baseline to orchestrated). <span className="honest-tag">modelled</span></li>
          <li>Human time per case, all cases: <strong>{H.labour_reduction.literature_all.value} less</strong> ({H.labour_reduction.literature_in_reach.value} among cases reached). The 40% target is not met. The minutes per action are assumed, not measured. <span className="honest-tag">modelled</span></li>
        </ul>
        <p className="muted">Literature-anchored scenario: {H.run.replications} runs of {H.run.cases_per_replication.toLocaleString("en")} synthetic cases, {H.run.follow_up_days} days of follow-up. Every figure comes from a simulator, not from patients or a trial. Nothing here diagnoses anyone.</p>
      </section>

      <p className="preview-actions"><Link className="btn" to="/">Back to sign in to try it yourself</Link></p>
    </main>
  );
}
