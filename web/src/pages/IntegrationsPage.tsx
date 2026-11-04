import { useCallback, useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { IntegrationFeed } from "../api/contract";
import { DOCS, docHref, type DocLink } from "../judge/links";
import { PAGE_DOCS } from "../judge/pageLinks";
import { fmtDateTime } from "../lib/format";
import "../judgePages.css";

/**
 * Integrations (item 12). Static explanation plus one live panel: the last few HL7, FHIR and notification events of the demo
 * stack (`GET /integrations/feed`, coordinator and supervisor; the browser mock shows a fixed sample). Every statement of what is
 * built and what is not is taken from docs/governance/hl7-adapter.md, malaffi-adapter-plan.md, patient-app.md and
 * docs/submission/technical-documentation.md. There is no number on this page that is not a count of items shown.
 */
interface Row { piece: string; what: string; where: string }

const MALAFFI_BUILT: Row[] = [
  { piece: "FIT result (HL7 ORU^R01)", what: "A final or corrected positive result creates a case. A preliminary result is refused rather than guessed at, and a numeric value with no flag is refused because the adapter has no cut-off. A negative result creates nothing.", where: "api/app/hl7/handler.py" },
  { piece: "Colonoscopy report (HL7 ORU^R01)", what: "Recorded as evidence only, as FHIR resources. Whether the colonoscopy was complete must be stated; if it is missing the message is refused. A report received through the exchange never closes a case; a clinician still signs.", where: "api/app/hl7/handler.py, api/app/confirmatory.py" },
  { piece: "Appointments (HL7 SIU S12, S14, S15)", what: "Book, reschedule and cancel the confirmatory appointment, for the one matching case of the patient. None or several matches is refused, never guessed.", where: "api/app/hl7/handler.py" },
  { piece: "Contact details (HL7 ADT A08, A31)", what: "Judged only as valid or not. The phone number is not stored, logged or returned. An existing case is not changed; that is a person's decision.", where: "api/app/hl7/contacts.py" },
  { piece: "Transport and mutual TLS", what: "POST /hl7 for the message with the ACK as the answer, plus an optional MLLP listener. The listener can require TLS and a client certificate signed by a named authority, with an allow-list of client names. It fails closed: it never falls back to clear text.", where: "api/app/hl7/mllp.py, api/app/hl7/tls.py" },
  { piece: "Safety of the adapter", what: "Redelivery of the same message changes nothing. The sender must be on an allow-list. Exact identifier matching only; no fuzzy match. Logs hold the message type, the ACK code and a short hash, no content. The adapter's token reaches one route and cannot close, sign or change consent.", where: "api/tests/test_hl7_*.py" },
  { piece: "FHIR evidence lookup", what: "To close a case the clinician cites evidence. The server checks that it exists in the FHIR server, belongs to this case and is complete evidence, and refuses with an error otherwise.", where: "api/app/evidence.py" },
];

const SAHATNA_BUILT: Row[] = [
  { piece: "Notification adapter (mock)", what: "The only outbound path. Channels are SMS, WhatsApp, phone and letter. A message is sent only after a person approved the draft, and consent and contact validity are checked again at send time. It writes to a log and opens no network connection.", where: "api/app/notify.py" },
  { piece: "Patient app (installable web app)", what: "Arabic and English, at /patient. It shows one plain paragraph, the next step, the appointment (with a calendar file made on the phone) and a privacy centre with who looked at the record.", where: "web/src/pages/PatientPage.tsx" },
  { piece: "Deep links", what: "Each case has its own patient address, and the app's manifest defines two shortcuts: My care, and Privacy and choices.", where: "web/public/manifest.webmanifest" },
];

const PILOT_WORK: { item: string; why: string }[] = [
  { item: "The exchange's real message specifications", why: "The operator's segment-level specifications were not seen. Every field mapping is our reading of the standard, and the real profiles, trigger events and code sets are to be confirmed." },
  { item: "A connection to Malaffi and an agreement with its operator", why: "The adapter has not been connected to or tested with Malaffi. Whether a non-EMR workflow system may receive these messages directly or only through a provider's system is open." },
  { item: "Certificates, network path and security assessment", why: "Mutual TLS is built, but the production certificates, any VPN or gateway, and the operator's assessment material come from the operator. None is in the repository." },
  { item: "Patient identity", why: "Matching is by one exact identifier. There is no Emirates ID lookup, no name and date-of-birth match, no human queue for ambiguous matches, and no handling of Arabic transliteration." },
  { item: "Anything sent back out", why: "The only thing sent is an acknowledgement. No status, owner or due date goes to Malaffi or a provider system, so there is also no outbox, dead-letter queue or alert for a failed delivery, and no alert for a silent interface." },
  { item: "Durability and scale", why: "The contact registry is in memory and lost on restart. A patient's open cases are found by a pass over the open cases, which is fine for a pilot cohort but needs an index before volume." },
  { item: "FHIR record of the FIT result", why: "The adapter does not create the observation and report of the FIT result; the case holds a reference to a report that does not exist in the FHIR server." },
  { item: "Sahatna", why: "There is no interface to Sahatna of any kind. It appears in the repository only as one way a booking can be made in the screening programme (IFHAS). Using it as a channel would need an agreement and an integration that does not exist yet." },
  { item: "A real message channel", why: "No real message has ever been sent. A pilot needs an approved provider for SMS or WhatsApp, and native-speaker review of the Arabic text." },
];

const DOC_LIST: DocLink[] = [PAGE_DOCS.hl7Adapter, PAGE_DOCS.malaffiPlan, PAGE_DOCS.patientApp, PAGE_DOCS.pilotPlan, DOCS.tech, DOCS.limits];

function RowsTable({ caption, rows }: { caption: string; rows: Row[] }) {
  return (
    <div className="jp-tablewrap">
      <table className="jp-table">
        <caption>{caption}</caption>
        <thead><tr><th scope="col">Piece</th><th scope="col">What it does</th><th scope="col">Where in the repository</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.piece}><th scope="row">{r.piece}</th><td>{r.what}</td><td className="mono">{r.where}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

const KIND_LABEL = { hl7: "HL7", fhir: "FHIR", notification: "Message" } as const;
const DIR_LABEL = { in: "in", out: "out" } as const;

export function LiveFeed() {
  const { api, role } = useApp();
  const uid = useId();
  const [limit, setLimit] = useState(10);
  const [feed, setFeed] = useState<IntegrationFeed | null | undefined>(undefined); // undefined: loading; null: not available
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);

  const load = useCallback(() => {
    if (!api.integrationFeed) { setFeed(null); return Promise.resolve(); }
    return api.integrationFeed(limit).then((f) => { setFeed(f); setError(null); }, (e: unknown) => setError(String((e as Error).message ?? e)));
  }, [api, limit]);

  // Deferred by a tick: the app logs in as the chosen role in an effect of its own, and a request made before it would use the previous role.
  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, [load, role]);
  useEffect(() => {
    if (!auto) return undefined;
    const t = window.setInterval(() => void load(), 10_000);
    return () => window.clearInterval(t);
  }, [auto, load]);

  return (
    <section aria-labelledby={`${uid}-h`} className="jp-section" data-testid="integration-feed">
      <h2 id={`${uid}-h`}>Live message log</h2>
      <div className="jp-chips">
        <span className="chip chip-warn" data-testid="feed-simulated">Simulated feed</span>
        <span className="chip chip-synthetic">Synthetic data</span>
      </div>
      <p className="jp-note">
        The last HL7, FHIR and message events of this demo stack. Nothing here came from Malaffi, a provider or a patient. Each line holds only the type, a fixed phrase, the result and a short code:
        no message content, names or identifiers. {api.adapter === "mock" ? "This is the offline demo, so it shows a fixed sample; against the API it lists the real events of the running stack." : "It lists the events this API process handled since it started."}
      </p>
      <p>
        <label htmlFor={`${uid}-n`}>Show the last </label>
        <select id={`${uid}-n`} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[5, 10, 20, 50].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>{" "}
        <button type="button" className="btn btn-small" onClick={() => void load()}>Refresh</button>{" "}
        <label><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Refresh by itself every few seconds</label>
      </p>
      <div aria-live="polite">
        {error && <p role="alert" className="why">Could not load the feed: {error}</p>}
        {feed === undefined && !error && <p className="muted">Loading the log.</p>}
        {feed === null && <p className="banner banner-warn" data-testid="feed-unavailable">The log is not available to this role or this backend. Coordinators and supervisors can read it.</p>}
        {feed && feed.entries.length === 0 && <p className="muted" data-testid="feed-empty">Nothing has crossed an integration boundary yet. Send a sample message with <span className="mono">make hl7-demo</span>, or act on a case, and refresh.</p>}
        {feed && feed.entries.length > 0 && (
          <ol className="jp-feed" data-testid="feed-list" aria-label="Integration events, newest first">
            {feed.entries.map((e) => (
              <li key={e.seq}>
                <span className="chip">{KIND_LABEL[e.kind]} {DIR_LABEL[e.direction]}</span>
                <span className="chip chip-neutral">{e.status}</span>
                <time className="muted" dateTime={e.at}>{fmtDateTime(e.at)} (simulated time)</time>
                <span className="jp-feed-sum">{e.summary}{e.ref ? <span className="mono muted"> {e.ref}</span> : null}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

export function IntegrationsPage() {
  const uid = useId();
  return (
    <main className="container" data-testid="integrations-page">
      <div className="page-head"><h1>Integrations</h1><span className="chip chip-synthetic">Synthetic data</span></div>
      <p className="jp-lead">
        The product is built to sit between two Abu Dhabi systems: <strong>results come in from Malaffi</strong>, the health information exchange, and <strong>messages and the patient app go out</strong>
        toward the people the programme serves, where the Sahatna app is the patient-facing route the challenge owner named. This page says what is built and tested here, and what a pilot would still have to do.
      </p>
      <div className="banner banner-warn" data-testid="integrations-honesty">
        <strong>Not connected.</strong> Nothing in this repository is connected to Malaffi or Sahatna, has been tested with them, or has an agreement with their operators. Everything below was built and tested against synthetic messages.
      </div>

      <section aria-labelledby={`${uid}-flow`} className="jp-section">
        <h2 id={`${uid}-flow`}>How it fits</h2>
        <div className="jp-diagram" role="group" aria-label="Data flow: results in, engine, messages out">
          <div className="jp-card"><h3>Results in</h3><p>Malaffi or a provider's system sends HL7 v2 messages: results, appointments, contact validity.</p></div>
          <div className="jp-arrow" aria-hidden="true">HL7 v2, then FHIR R4 inside</div>
          <div className="jp-card"><h3>Screening Closure</h3><p>The engine gives every positive an owner, a clock and a signed closure. It checks evidence against the FHIR server.</p></div>
          <div className="jp-arrow" aria-hidden="true">approved drafts only</div>
          <div className="jp-card"><h3>Messages and app out</h3><p>A notification adapter and an installable patient app. The Sahatna app is the route these would one day need to reach.</p></div>
        </div>
      </section>

      <section aria-labelledby={`${uid}-m`} className="jp-section">
        <h2 id={`${uid}-m`}>Malaffi: results in</h2>
        <p className="jp-note"><span className="jp-built">Built and tested on synthetic messages.</span> Not connected to Malaffi.</p>
        <RowsTable caption="What the HL7 v2 adapter and the FHIR evidence check do" rows={MALAFFI_BUILT} />
      </section>

      <section aria-labelledby={`${uid}-s`} className="jp-section">
        <h2 id={`${uid}-s`}>Sahatna and the patient: messages and app out</h2>
        <p className="jp-note"><span className="jp-built">Built and tested on synthetic data.</span> <span className="jp-open">No interface to Sahatna exists.</span></p>
        <RowsTable caption="What goes out toward patients today" rows={SAHATNA_BUILT} />
      </section>

      <LiveFeed />

      <section aria-labelledby={`${uid}-p`} className="jp-section" data-testid="integrations-pilot-work">
        <h2 id={`${uid}-p`}>What a pilot would need</h2>
        <p className="jp-note">These are open in the repository's own documents. The list is not a plan or a promise.</p>
        <ul className="jp-cards">
          {PILOT_WORK.map((p) => (
            <li key={p.item} className="jp-card"><h3>{p.item}</h3><p>{p.why}</p></li>
          ))}
        </ul>
      </section>

      <section aria-labelledby={`${uid}-d`} className="jp-section">
        <h2 id={`${uid}-d`}>Read more</h2>
        <ul className="plain jp-links">
          {DOC_LIST.map((d) => <li key={d.path}><a href={docHref(d)} target="_blank" rel="noopener noreferrer">{d.label}</a> <span className="mono muted">{d.path}</span></li>)}
        </ul>
        <p><Link to="/judge">Back to the judge summary</Link></p>
      </section>
    </main>
  );
}
