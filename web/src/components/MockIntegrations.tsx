import { useCallback, useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { IntegrationMockStatus, MalaffiReplayResult, SahatnaCallbackResult } from "../api/contract";
import { shortId } from "../lib/format";

/**
 * ADR-057. Two runnable MOCKS on the Integrations page, for the running demo stack only: a Malaffi-style inbound result (an HL7
 * ORU message the server sends to its own adapter) and a Sahatna-style booking callback (an invented JSON callback the server
 * turns into an HL7 SIU message for the same adapter). They show what the engine does with an inbound message of that shape.
 * They are not connections, and no Malaffi or Sahatna specification was seen. The API answers 404 anywhere that is not a
 * development or declared demo deployment and 403 to roles other than coordinator and supervisor; this panel says so then.
 */
const OUTCOME_WORDS: Record<string, string> = {
  case_created: "A new case was created from the message.",
  booked: "The case now has a booked appointment.",
  duplicate: "The adapter had already processed this message, so nothing changed (a redelivery is safe).",
  unchanged: "The case already had an appointment at this time, so nothing changed.",
  rejected: "The adapter refused the message, and nothing changed.",
};

type Result = ({ kind: "malaffi" } & MalaffiReplayResult) | ({ kind: "sahatna" } & SahatnaCallbackResult);

function ResultView({ r }: { r: Result }) {
  const accepted = r.ack.code === "AA";
  return (
    <div className="jp-mock-result" data-testid={`mock-result-${r.kind}`} aria-live="polite">
      <p>
        <span className="chip chip-warn">MOCK</span>{" "}
        <span className={`chip ${accepted ? "chip-ok" : "chip-neutral"}`} data-testid="mock-ack">Adapter answer: {r.ack.code}</span>{" "}
        <strong data-testid="mock-outcome">{OUTCOME_WORDS[r.outcome] ?? r.outcome}</strong>
        {r.ack.reason && !accepted ? <> Reason given: <em>{r.ack.reason}</em>.</> : null}
      </p>
      {r.case_id && (
        <p>
          Case <Link to={`/coordinator/${r.case_id}`} className="mono" data-testid="mock-case-link">{shortId(r.case_id)}</Link>
          {r.kind === "sahatna" && r.state_before && r.state_before !== r.state ? <> moved from <span className="mono">{r.state_before}</span> to <span className="mono">{r.state}</span></> : <> is at <span className="mono">{r.state}</span></>}.
          {r.kind === "sahatna" && r.event ? <> The event written is <span className="mono">{r.event}</span>, by the HL7 adapter.</> : null}
        </p>
      )}
      {r.kind === "sahatna" && (
        <details>
          <summary>The invented callback</summary>
          <pre className="judge-cmd" data-testid="mock-callback-json">{JSON.stringify(r.callback, null, 2)}</pre>
        </details>
      )}
      <details>
        <summary>The HL7 message the adapter received ({r.kind === "malaffi" ? r.message_type : r.translated_to})</summary>
        <pre className="judge-cmd" data-testid="mock-message">{r.message}</pre>
      </details>
    </div>
  );
}

export function MockIntegrations() {
  const { api, role } = useApp();
  const uid = useId();
  const supported = !!api.integrationMockStatus;
  const [status, setStatus] = useState<IntegrationMockStatus | null | undefined>(undefined); // undefined: loading; null: not available
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [caseId, setCaseId] = useState("");

  const load = useCallback(() => {
    if (!api.integrationMockStatus) { setStatus(null); return Promise.resolve(); }
    return api.integrationMockStatus().then((s) => { setStatus(s); setError(null); }, (e: unknown) => setError(String((e as Error).message ?? e)));
  }, [api]);
  // Deferred by a tick: the app logs in as the chosen role in an effect of its own (see LiveFeed).
  useEffect(() => {
    setResult(null);
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [load, role]);
  const eligible = status?.sahatna.eligible_cases ?? [];
  useEffect(() => { if (!eligible.some((c) => c.case_id === caseId)) setCaseId(eligible[0]?.case_id ?? ""); }, [eligible, caseId]);

  const run = async (id: string, fn: () => Promise<Result>) => {
    setBusy(id); setError(null);
    try { setResult(await fn()); await load(); } catch (e) { setError(String((e as Error).message ?? e)); } finally { setBusy(null); }
  };
  const replay = (redeliver: boolean) => run(redeliver ? "again" : "replay", async () => ({ kind: "malaffi" as const, ...(await api.replayMalaffiResult!(redeliver)) }));
  const book = () => run("book", async () => ({ kind: "sahatna" as const, ...(await api.sahatnaBookingCallback!(caseId)) }));

  const m = status?.malaffi;
  return (
    <section aria-labelledby={`${uid}-h`} className="jp-section" data-testid="mock-integrations">
      <h2 id={`${uid}-h`}>Try the message shapes (mock)</h2>
      <div className="jp-chips">
        <span className="chip chip-warn" data-testid="mock-chip">MOCK</span>
        <span className="chip chip-synthetic">Synthetic data</span>
      </div>
      <div className="banner banner-warn" data-testid="mock-honesty">
        <strong>This is a mock of message shapes, not a connection.</strong> The server builds a synthetic message and hands it to its own HL7 adapter, the same code a real feed
        would reach. Nothing leaves this stack. We had no access to Malaffi's or Sahatna's specifications, so the shapes are our reading of the HL7 standard; the Sahatna callback is
        invented for this demo and says nothing about how Sahatna really works.
      </div>
      {!supported && <p className="muted" data-testid="mock-offline">The buttons need the running demo stack. This offline copy of the app has no API behind it.</p>}
      {supported && status === undefined && !error && <p className="muted">Checking whether the mocks are on.</p>}
      {supported && status === null && (
        <p className="banner banner-warn" data-testid="mock-unavailable">
          The mocks are not available here. They run only on a development or demo deployment, for coordinators and supervisors.
        </p>
      )}
      {error && <p role="alert" className="why" data-testid="mock-error">Could not run it: {error}</p>}
      {status && (
        <ul className="jp-cards">
          <li className="jp-card" data-testid="mock-malaffi">
            <h3>Malaffi-style result in <span className="chip chip-warn">MOCK</span></h3>
            <p>Sends one positive FIT result as an HL7 ORU message to the adapter. It makes a case exactly as a result arriving through the exchange would. Sending the same message again links to that case and creates nothing new.</p>
            <p className="jp-note">{m?.replays_used} of {m?.replays_max} mock messages used. A demo reset brings them back.</p>
            <p>
              <button type="button" className="btn btn-primary" disabled={busy !== null || (m?.replays_used ?? 0) >= (m?.replays_max ?? 0)} onClick={() => void replay(false)} data-testid="mock-malaffi-replay">
                {busy === "replay" ? "Sending." : "Replay a positive result (mock)"}
              </button>{" "}
              <button type="button" className="btn" disabled={busy !== null || (m?.replays_used ?? 0) === 0} onClick={() => void replay(true)} data-testid="mock-malaffi-again">
                {busy === "again" ? "Sending." : "Send the same message again"}
              </button>
            </p>
            {result?.kind === "malaffi" && <ResultView r={result} />}
          </li>
          <li className="jp-card" data-testid="mock-sahatna">
            <h3>Sahatna-style booking callback in <span className="chip chip-warn">MOCK</span></h3>
            <p>Pretends the patient confirmed a slot in the app. The server turns the invented callback into an HL7 appointment message and the adapter books the case, through the same event it already uses.</p>
            {eligible.length === 0 ? (
              <p className="muted" data-testid="mock-sahatna-none">No case is waiting for an appointment (it must be at "patient acknowledged", with one open case for the patient).</p>
            ) : (
              <p>
                <label htmlFor={`${uid}-c`}>Case to book </label>
                <select id={`${uid}-c`} value={caseId} onChange={(e) => setCaseId(e.target.value)} data-testid="mock-sahatna-case">
                  {eligible.map((c) => <option key={c.case_id} value={c.case_id}>{shortId(c.case_id)} ({c.state.replace(/_/g, " ")})</option>)}
                </select>{" "}
                <button type="button" className="btn btn-primary" disabled={busy !== null || !caseId} onClick={() => void book()} data-testid="mock-sahatna-book">
                  {busy === "book" ? "Sending." : "Send booking confirmation (mock)"}
                </button>
              </p>
            )}
            {result?.kind === "sahatna" && <ResultView r={result} />}
          </li>
        </ul>
      )}
    </section>
  );
}
