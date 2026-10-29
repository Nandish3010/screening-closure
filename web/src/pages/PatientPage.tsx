import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { CaseDetail, ConsentChanges, Lang } from "../api/contract";
import { DEMO_PATIENT_CASE_ID } from "../api/fixtures";
import { DIR, STRINGS, roleLabel } from "../i18n/strings";
import { consentOf, hasCallbackPending, patientLang, patientOwnerOf } from "../lib/derive";
import { stateIndex } from "../lib/pathway";

const LAST_KEY = "sc.patient.last";
const LANG_KEY = "sc.lang";

function readLast(caseId: string): CaseDetail | null {
  try {
    const raw = localStorage.getItem(LAST_KEY);
    if (!raw) return null;
    const j = JSON.parse(raw) as { caseId: string; detail: CaseDetail };
    return j.caseId === caseId ? j.detail : null;
  } catch {
    return null;
  }
}
function writeLast(caseId: string, detail: CaseDetail) {
  try {
    localStorage.setItem(LAST_KEY, JSON.stringify({ caseId, detail }));
  } catch {
    /* ignore */
  }
}
function lastCaseId(): string | null {
  try {
    const raw = localStorage.getItem(LAST_KEY);
    return raw ? (JSON.parse(raw) as { caseId: string }).caseId : null;
  } catch {
    return null;
  }
}
function readLang(): Lang | null {
  try {
    const l = localStorage.getItem(LANG_KEY);
    return l === "ar" || l === "en" ? l : null;
  } catch {
    return null;
  }
}

/** /patient: the installed PWA's start page. Sends the patient to the last case they opened. */
export function PatientHome() {
  const last = lastCaseId();
  const [lang] = useState<Lang>(readLang() ?? "en");
  const t = STRINGS[lang];
  if (last) return <PatientView caseId={last} />;
  return (
    <main className="patient" lang={lang} dir={DIR[lang]} data-testid="patient-root">
      <h1>{t.homeTitle}</h1>
      <p>{t.homeBody}</p>
      <p><Link to={`/patient/${DEMO_PATIENT_CASE_ID}`}>Demo case</Link></p>
    </main>
  );
}

export function PatientPage() {
  const { caseId } = useParams();
  return <PatientView caseId={caseId ?? ""} />;
}

function progressStep(state: string): number {
  const i = stateIndex(state);
  if (i <= 0) return 0;
  if (i <= 5) return 1; // acknowledged .. confirmatory_booked (care team ready, test being booked)
  if (i <= 6) return 2; // confirmatory_completed
  return 3;
}

export function PatientView({ caseId }: { caseId: string }) {
  const { api, refreshSim, role } = useApp();
  const [detail, setDetail] = useState<CaseDetail | null>(() => readLast(caseId));
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState(false);
  const [lang, setLangState] = useState<Lang>(() => readLang() ?? "en");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<"callback" | "ack" | "failed" | "saved" | "saveFailed" | null>(null);
  const t = STRINGS[lang];

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = DIR[lang];
    return () => {
      document.documentElement.lang = "en";
      document.documentElement.dir = "ltr";
    };
  }, [lang]);

  const load = useCallback(async () => {
    try {
      const d = await api.getCase(caseId);
      setDetail(d);
      writeLast(caseId, d);
      setOffline(false);
      setError(false);
      if (!readLang()) {
        const l = patientLang(d.events);
        if (l) setLangState(l);
      }
    } catch {
      // Network or API failure: keep whatever we last saved on this device.
      if (readLast(caseId)) setOffline(true);
      else setError(true);
    }
  }, [api, caseId]);

  useEffect(() => {
    // The real API wants a patient_ref for a patient login: read it from this case (http adapter, demo only).
    const ready = role === "patient" && api.loginPatientFor ? api.loginPatientFor(caseId).catch(() => undefined) : Promise.resolve();
    void ready.then(load);
    const on = () => void load();
    window.addEventListener("online", on);
    return () => window.removeEventListener("online", on);
  }, [load, api, caseId, role]);

  const setLang = (l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(LANG_KEY, l);
    } catch {
      /* ignore */
    }
  };

  const post = async (type: string, payload: Record<string, unknown>) => {
    setBusy(true);
    try {
      const d = await api.postEvent(caseId, { type, actor: `patient:${caseId}`, payload });
      setDetail(d);
      writeLast(caseId, d);
      refreshSim();
      return true;
    } catch {
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onAcknowledge = async () => setNotice((await post("patient_acknowledge", { lang })) ? "ack" : "failed");
  const onCallback = async () =>
    setNotice((await post("callback_requested", { reason: "patient_does_not_understand", lang })) ? "callback" : "failed");

  const consent = detail ? (detail.consent ?? consentOf(detail.events)) : null;
  /** Partial updates: only what the patient just changed is sent (A6). */
  const saveConsent = async (changes: ConsentChanges) =>
    setNotice((await post("consent_updated", changes)) ? "saved" : "saveFailed");

  const toggle = (
    <button type="button" className="btn lang-toggle" onClick={() => setLang(lang === "en" ? "ar" : "en")} lang={lang === "en" ? "ar" : "en"} aria-label={`${t.language}: ${t.switchTo}`}>
      {t.switchTo}
    </button>
  );

  if (!detail) {
    return (
      <main className="patient" lang={lang} dir={DIR[lang]} data-testid="patient-root">
        <div className="patient-top">{toggle}</div>
        <p role={error ? "alert" : "status"}>{error ? t.loadError : t.loading}</p>
      </main>
    );
  }

  const owner = patientOwnerOf(detail);
  const ownerLine = !owner
    ? t.ownerPending
    : owner.kind === "role"
      ? (Object.hasOwn(t.ownerByRole, owner.roleKey) ? (t.ownerByRole as Record<string, string>)[owner.roleKey] : t.ownerByRole.other)
      : t.ownerLine(owner.name, roleLabel(owner.role ?? "Care coordinator", lang));
  const step = progressStep(detail.state);
  const idx = stateIndex(detail.state);
  const acknowledged = idx >= stateIndex("patient_acknowledged");
  const canAck = detail.state === "patient_contacted";
  const callbackAsked = notice === "callback" || hasCallbackPending(detail.events);

  return (
    <main className="patient" lang={lang} dir={DIR[lang]} data-testid="patient-root">
      <div className="patient-top">
        <h1>{t.appTitle}</h1>
        {toggle}
      </div>

      {offline && <p className="banner banner-warn" role="status">{t.offline}</p>}

      <section aria-labelledby="p-result">
        <h2 id="p-result">{t.resultHeading}</h2>
        <p className="big">{t.resultBody}</p>
        <p data-testid="explain-in-person">{t.explainInPerson}</p>
      </section>

      <section aria-labelledby="p-progress">
        <h2 id="p-progress">{t.progressHeading}</h2>
        <ol className="progress">
          {t.progress.map((label, i) => (
            <li key={label} className={i < step ? "done" : i === step ? "current" : ""} aria-current={i === step ? "step" : undefined}>
              {label}
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="p-next">
        <h2 id="p-next">{t.nextHeading}</h2>
        <ol className="steps">
          {t.nextSteps.map((s) => <li key={s}>{s}</li>)}
        </ol>
      </section>

      <section aria-labelledby="p-owner">
        <h2 id="p-owner">{t.ownerHeading}</h2>
        <p className="big" data-testid="patient-owner">
          {ownerLine}
        </p>
      </section>

      <section aria-labelledby="p-ai" data-testid="ai-notice">
        <h2 id="p-ai">{t.aiNoticeHeading}</h2>
        <p>{t.aiNoticeSoftware}</p>
        <p>{t.aiNoticeStaff}</p>
        <p>{t.aiNoticePerson}</p>
        <p>{t.aiNoticeStop}</p>
      </section>

      <section className="actions" aria-label="Actions">
        {acknowledged ? (
          <p className="banner banner-ok" role="status" data-testid="ack-done">{t.understandDone}</p>
        ) : (
          <>
            <button type="button" className="btn btn-primary btn-xl" onClick={onAcknowledge} disabled={!canAck || busy} aria-describedby="ack-hint">
              {t.understand}
            </button>
            {!canAck && <p id="ack-hint" className="muted">{t.understandWait}</p>}
          </>
        )}
        <button type="button" className="btn btn-xl" onClick={onCallback} disabled={busy}>
          {t.dontUnderstand}
        </button>
        {callbackAsked && <p className="banner banner-ok" role="status" data-testid="callback-done">{t.callbackDone}</p>}
        {notice === "failed" && <p className="banner banner-bad" role="alert">{t.actionFailed}</p>}
      </section>

      <section aria-labelledby="p-consent">
        <h2 id="p-consent">{t.consentHeading}</h2>
        {consent?.withdrawn && <p className="banner banner-warn" role="status" data-testid="consent-withdrawn">{t.withdrawnNotice}</p>}
        <fieldset className="choices">
          <legend>{t.channelLabel}</legend>
          {(["sms", "whatsapp", "phone", "letter"] as const).map((c) => (
            <label key={c} className="check">
              <input
                type="checkbox"
                checked={consent?.[`channel_${c}`] ?? true}
                onChange={(e) => void saveConsent({ [`channel_${c}`]: e.target.checked })}
                disabled={busy}
              />
              <span>{t.channels[c]}</span>
            </label>
          ))}
        </fieldset>
        <label className="check">
          <input
            type="checkbox"
            checked={consent?.share_with_specialist ?? true}
            onChange={(e) => void saveConsent({ share_with_specialist: e.target.checked })}
            disabled={busy}
          />
          <span>{t.shareLabel}</span>
        </label>
        {consent?.withdrawn ? (
          <button type="button" className="btn btn-xl" onClick={() => void saveConsent({ withdrawn: false })} disabled={busy} data-testid="consent-resume">
            {t.resumeButton}
          </button>
        ) : (
          <>
            <p className="muted">{t.withdrawHint}</p>
            <button type="button" className="btn btn-xl" onClick={() => void saveConsent({ withdrawn: true })} disabled={busy} data-testid="consent-withdraw">
              {t.withdrawButton}
            </button>
          </>
        )}
        <p className="muted" role="status" aria-live="polite">
          {notice === "saved" ? t.saved : notice === "saveFailed" ? t.saveFailed : ""}
        </p>
      </section>
    </main>
  );
}
