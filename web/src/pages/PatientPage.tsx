import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useApp } from "../app/AppContext";
import type { AccessLog, CaseDetail, ConsentChanges, Lang } from "../api/contract";
import { DEMO_PATIENT_CASE_ID } from "../api/fixtures";
import { DIR, STRINGS, roleLabel } from "../i18n/strings";
import { consentOf, hasCallbackPending, patientLang, patientOwnerOf } from "../lib/derive";
import {
  appointmentOf, buildIcs, formatAppointment, formatWhen, historyOf, journeyStep, nextStepOf, pathwayStrings, roleName, telHref,
} from "../lib/patientApp";
import "../patient.css";

/** First-strong isolate so a Latin name does not scramble neighbouring Arabic text. */
const iso = (s: string) => `\u2068${s}\u2069`;

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
    <PhoneFrame>
      <main className="pa" lang={lang} dir={DIR[lang]} data-testid="patient-root">
        <div className="pa-body">
          <h1 className="pa-title">{t.homeTitle}</h1>
          <p className="pa-lead">{t.homeBody}</p>
          <p><Link className="btn btn-primary btn-xl" to={`/patient/${DEMO_PATIENT_CASE_ID}`}>Demo case</Link></p>
        </div>
      </main>
    </PhoneFrame>
  );
}

export function PatientPage() {
  const { caseId } = useParams();
  return <PatientView caseId={caseId ?? ""} />;
}


const ico = { className: "pa-ico", width: 26, height: 26, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
const HeartIcon = () => (
  <svg {...ico}><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" /></svg>
);
const ShieldIcon = () => (
  <svg {...ico}><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z" /><path d="M9 12l2 2 4-4" /></svg>
);

/**
 * A presentational phone frame. On a narrow screen (a real phone, an installed app) it is invisible: the app fills the screen.
 * On a wide screen (a laptop, a video) patient.css draws a phone around the app so judges see what a patient sees.
 */
function PhoneFrame({ children }: { children: ReactNode }) {
  return (
    <div className="pa-stage">
      <div className="pa-phone">{children}</div>
    </div>
  );
}

type Notice = "callback" | "ack" | "reschedule" | "cannot" | "failed" | "saved" | "saveFailed" | null;
type Tab = "home" | "privacy";

function initialTab(): Tab {
  try {
    return new URLSearchParams(window.location.search).get("tab") === "privacy" ? "privacy" : "home";
  } catch {
    return "home";
  }
}

/** Save a text file on the phone from the page (the calendar file). No upload: the file is made here. */
function saveFile(name: string, mime: string, text: string): boolean {
  try {
    if (typeof URL.createObjectURL !== "function") return false;
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return true;
  } catch {
    return false;
  }
}

function clearLocal() {
  try {
    localStorage.removeItem(LAST_KEY);
  } catch {
    /* ignore */
  }
}

export function PatientView({ caseId }: { caseId: string }) {
  const { api, refreshSim, role } = useApp();
  const [detail, setDetail] = useState<CaseDetail | null>(() => readLast(caseId));
  const [offline, setOffline] = useState(false);
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine !== false));
  const [error, setError] = useState(false);
  const [lang, setLangState] = useState<Lang>(() => readLang() ?? "en");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [access, setAccess] = useState<AccessLog | "error" | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [cleared, setCleared] = useState(false);
  const t = STRINGS[lang];
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);

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
    const on = () => {
      setOnline(true);
      void load();
    };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, [load, api, caseId, role]);

  // The access log is loaded each time the Privacy tab opens; it is never kept on the phone.
  useEffect(() => {
    if (tab !== "privacy" || !detail) return;
    let live = true;
    api.getAccessLog(caseId).then((a) => live && setAccess(a), () => live && setAccess("error"));
    return () => {
      live = false;
    };
  }, [tab, api, caseId, detail?.case_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Moving to another tab moves focus to its heading, so a screen reader starts at the top of the new screen.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [tab]);

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
  const callback = async (reason: "patient_does_not_understand" | "reschedule" | "cannot_attend", ok: Notice) =>
    setNotice((await post("callback_requested", { reason, lang })) ? ok : "failed");

  const consent = detail ? (detail.consent ?? consentOf(detail.events)) : null;
  /** Partial updates: only what the patient just changed is sent (A6). The engine's consent hold applies unchanged. */
  const saveConsent = async (changes: ConsentChanges) =>
    setNotice((await post("consent_updated", { ...changes, source: { channel: "patient_app" } })) ? "saved" : "saveFailed");

  // Large touch targets (btn-xl is 60px tall); both languages are one tap away and each is named in its own script.
  const toggle = (
    <button type="button" className="btn lang-toggle pa-lang" onClick={() => setLang(lang === "en" ? "ar" : "en")} lang={lang === "en" ? "ar" : "en"} aria-label={`${t.language}: ${STRINGS.ar.switchTo} ${STRINGS.en.switchTo}`}>
      <span className={lang === "en" ? "on" : ""} lang="en">{STRINGS.ar.switchTo}</span>{" "}
      <span className={lang === "ar" ? "on" : ""} lang="ar">{STRINGS.en.switchTo}</span>
    </button>
  );

  if (!detail) {
    return (
      <PhoneFrame>
        <main className="pa" lang={lang} dir={DIR[lang]} data-testid="patient-root">
          <header className="pa-bar">
            <h1 className="pa-title">{t.appTitle}</h1>
            {toggle}
          </header>
          <div className="pa-body">
            <p role={error ? "alert" : "status"}>{error ? t.loadError : t.loading}</p>
          </div>
        </main>
      </PhoneFrame>
    );
  }

  const contact = detail.care_contact ?? null;
  // The owner by name when the API sent the care contact (ADR-053); else by role only (an older server), or the name a staff
  // login previewing this page can read from the log.
  const owner = patientOwnerOf(detail);
  const ownerLine = contact
    ? t.ownerLine(contact.name, roleName(t, contact.role))
    : !owner
      ? t.ownerPending
      : owner.kind === "role"
        ? (Object.hasOwn(t.ownerByRole, owner.roleKey) ? (t.ownerByRole as Record<string, string>)[owner.roleKey] : t.ownerByRole.other)
        : t.ownerLine(owner.name, roleLabel(owner.role ?? "Care coordinator", lang));
  const pw = pathwayStrings(t, detail.pathway);
  const step = journeyStep(detail.state);
  const appt = appointmentOf(detail.events, detail.state);
  const withdrawn = !!consent?.withdrawn;
  const next = nextStepOf(detail.state, appt !== null, withdrawn);
  const canAck = detail.state === "patient_contacted";
  const acknowledged = notice === "ack" || detail.events.some((e) => e.type === "patient_acknowledge");
  const callbackAsked = notice === "callback" || hasCallbackPending(detail.events);
  const canSend = online && !offline;
  const sendOff = busy || !canSend;
  const history = historyOf(detail.events, t).reverse(); // newest first
  const nextText = next.kind === "arranging" ? pw.waiting : t.nextState[next.kind];

  const addToCalendar = () => {
    if (!appt) return;
    const ics = buildIcs({ uid: caseId, title: pw.calendarTitle, description: t.calendarDescription, appointment: appt });
    saveFile("appointment.ics", "text/calendar;charset=utf-8", ics);
  };

  const homePanel = (
    <>
      <section className="pa-card" aria-labelledby="p-result">
        <h2 id="p-result">{t.resultHeading}</h2>
        <p className="big">{t.resultBody}</p>
        <p data-testid="explain-in-person">{t.explainInPerson}</p>
      </section>

      <section className="pa-card" aria-labelledby="p-next" data-testid="next-step">
        <h2 id="p-next">{t.nextHeading}</h2>
        <p className="big" data-testid="next-text">{nextText}</p>
        {pathwayExplainer(pw.nextSteps)}
        <div className="pa-actions">
          {next.primary === "acknowledge" && (
            <button type="button" className="btn btn-primary btn-xl" onClick={onAcknowledge} disabled={!canAck || sendOff} data-testid="primary-action">
              {t.understand}
            </button>
          )}
          {next.primary === "calendar" && (
            <>
              <button type="button" className="btn btn-primary btn-xl" onClick={addToCalendar} data-testid="primary-action">
                {t.addToCalendar}
              </button>
              <p className="muted">{t.calendarNote}</p>
            </>
          )}
          {next.primary === "resume" && (
            <button type="button" className="btn btn-primary btn-xl" onClick={() => void saveConsent({ withdrawn: false })} disabled={sendOff} data-testid="primary-action">
              {t.resumeButton}
            </button>
          )}
          {next.kind === "booked" && (
            <>
              <button type="button" className="btn btn-xl" onClick={() => void callback("reschedule", "reschedule")} disabled={sendOff} data-testid="reschedule">
                {t.reschedule}
              </button>
              <button type="button" className="btn btn-xl" onClick={() => void callback("cannot_attend", "cannot")} disabled={sendOff} data-testid="cannot-attend">
                {t.cannotAttend}
              </button>
            </>
          )}
          {next.kind !== "closed" && (
            <button type="button" className="btn btn-xl" onClick={() => void callback("patient_does_not_understand", "callback")} disabled={sendOff}>
              {t.dontUnderstand}
            </button>
          )}
          {next.kind === "preContact" && <p className="muted">{t.understandWait}</p>}
          {!canSend && <p className="muted" role="status">{t.offlineAction}</p>}
          {acknowledged && next.kind !== "contacted" && <p className="banner banner-ok" role="status" data-testid="ack-done">{t.understandDone}</p>}
          {callbackAsked && <p className="banner banner-ok" role="status" data-testid="callback-done">{t.callbackDone}</p>}
          {notice === "reschedule" && <p className="banner banner-ok" role="status">{t.rescheduleDone}</p>}
          {notice === "cannot" && <p className="banner banner-ok" role="status">{t.cannotAttendDone}</p>}
          {notice === "failed" && <p className="banner banner-bad" role="alert">{t.actionFailed}</p>}
        </div>
      </section>

      {appt && (
        <section className="pa-card" aria-labelledby="p-appt" data-testid="appointment">
          <h2 id="p-appt">{t.appointmentHeading}</h2>
          <p className="pa-appt-name">{pw.appointmentTitle}</p>
          <p className="big" data-testid="appointment-date">{formatAppointment(appt, lang)}</p>
          {!appt.hasTime && <p className="muted">{t.appointmentTimeLater}</p>}
        </section>
      )}

      <section className="pa-card" aria-labelledby="p-progress">
        <h2 id="p-progress">{t.progressHeading}</h2>
        <ol className="pa-journey">
          {pw.progress.map((label, i) => (
            <li key={label} className={i < step ? "done" : i === step ? "current" : ""} aria-current={i === step ? "step" : undefined}>
              {label}
            </li>
          ))}
        </ol>
      </section>

      <section className="pa-card" aria-labelledby="p-owner">
        <h2 id="p-owner">{t.ownerHeading}</h2>
        <p className="big" data-testid="patient-owner">
          {ownerLine}
        </p>
        {contact && (
          <div className="pa-reach">
            <p className="pa-sub">{t.ownerReach}</p>
            <a className="btn btn-xl" href={telHref(contact.phone)} data-testid="call-care-team">
              {t.callCareTeam}: <bdi dir="ltr">{contact.phone}</bdi>
            </a>
            {contact.synthetic && <p className="muted">{t.demoNumber}</p>}
          </div>
        )}
      </section>

      <section className="pa-card" aria-labelledby="p-history" data-testid="status-timeline">
        <h2 id="p-history">{t.historyHeading}</h2>
        {history.length === 0 ? (
          <p className="muted">{t.historyEmpty}</p>
        ) : (
          <ol className="pa-history">
            {history.map((h, i) => (
              <li key={`${h.key}-${h.at}-${i}`}>
                <time dateTime={h.at}>{formatWhen(h.at, lang)}</time>
                <span>{h.text}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </>
  );

  const privacyPanel = (
    <>
      <p className="pa-lead">{t.privacyIntro}</p>

      <section className="pa-card" aria-labelledby="p-consent">
        <h2 id="p-consent">{t.consentHeading}</h2>
        {withdrawn && <p className="banner banner-warn" role="status" data-testid="consent-withdrawn">{t.withdrawnNotice}</p>}
        <fieldset className="choices">
          <legend>{t.channelLabel}</legend>
          {(["sms", "whatsapp", "phone", "letter"] as const).map((c) => (
            <label key={c} className="check">
              <input
                type="checkbox"
                checked={consent?.[`channel_${c}`] ?? true}
                onChange={(e) => void saveConsent({ [`channel_${c}`]: e.target.checked })}
                disabled={sendOff}
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
            disabled={sendOff}
          />
          <span>{t.shareLabel}</span>
        </label>
        <fieldset className="choices">
          <legend>{t.languageLabel}</legend>
          {(["en", "ar"] as const).map((l) => (
            <label key={l} className="check">
              <input type="radio" name="pa-lang" checked={lang === l} onChange={() => { setLang(l); if (canSend && !busy) void post("consent_updated", { lang: l }); }} />
              <span lang={l}>{l === "en" ? STRINGS.ar.switchTo : STRINGS.en.switchTo}</span>
            </label>
          ))}
          <p className="muted">{t.languageNote}</p>
        </fieldset>

        {withdrawn ? (
          <button type="button" className="btn btn-primary btn-xl" onClick={() => void saveConsent({ withdrawn: false })} disabled={sendOff} data-testid="consent-resume">
            {t.resumeButton}
          </button>
        ) : confirmStop ? (
          <div className="pa-confirm" role="group" aria-labelledby="p-stop-title" data-testid="consent-confirm">
            <h3 id="p-stop-title">{t.withdrawConfirmTitle}</h3>
            <p>{t.withdrawConfirmBody}</p>
            <button type="button" className="btn btn-danger btn-xl" onClick={() => { setConfirmStop(false); void saveConsent({ withdrawn: true }); }} disabled={sendOff} data-testid="consent-withdraw-yes">
              {t.withdrawConfirmYes}
            </button>
            <button type="button" className="btn btn-xl" onClick={() => setConfirmStop(false)} autoFocus>
              {t.withdrawConfirmNo}
            </button>
          </div>
        ) : (
          <>
            <p className="muted">{t.withdrawHint}</p>
            <button type="button" className="btn btn-xl" onClick={() => setConfirmStop(true)} disabled={sendOff} data-testid="consent-withdraw">
              {t.withdrawButton}
            </button>
          </>
        )}
        <p className="muted" role="status" aria-live="polite">
          {notice === "saved" ? t.saved : notice === "saveFailed" ? t.saveFailed : ""}
        </p>
      </section>

      <section className="pa-card" aria-labelledby="p-access" data-testid="access-log">
        <h2 id="p-access">{t.accessHeading}</h2>
        <p>{t.accessIntro}</p>
        {access === "error" ? (
          <p className="muted" role="alert">{t.accessLoadError}</p>
        ) : access === null ? (
          <p className="muted" role="status">{t.loading}</p>
        ) : access.entries.length === 0 ? (
          <p className="muted" data-testid="access-empty">{t.accessEmpty}</p>
        ) : (
          <ul className="pa-access">
            {access.entries.map((e, i) => (
              <li key={`${e.at}-${e.name}-${i}`} data-testid="access-row">
                <strong>{iso(e.name)}</strong>
                <span>{roleName(t, e.role)}</span>
                <time dateTime={e.at}>{formatWhen(e.at, lang)}</time>
              </li>
            ))}
          </ul>
        )}
        <p className="muted">{t.accessNote}</p>
      </section>

      <section className="pa-card" aria-labelledby="p-data" data-testid="data-panel">
        <h2 id="p-data">{t.dataHeading}</h2>
        <ul className="pa-facts">
          {t.data.map((d) => <li key={d}>{d}</li>)}
        </ul>
        <div data-testid="ai-notice" className="pa-subcard">
          <h3 id="p-ai">{t.aiNoticeHeading}</h3>
          <p>{t.aiNoticeSoftware}</p>
          <p>{t.aiNoticeStaff}</p>
          <p>{t.aiNoticePerson}</p>
          <p>{t.aiNoticeStop}</p>
        </div>
      </section>

      <section className="pa-card" aria-labelledby="p-device">
        <h2 id="p-device">{t.deviceHeading}</h2>
        <p>{t.deviceBody}</p>
        <button type="button" className="btn btn-xl" onClick={() => { clearLocal(); setCleared(true); }} data-testid="clear-local">
          {t.deviceClear}
        </button>
        {cleared && <p className="banner banner-ok" role="status">{t.deviceCleared}</p>}
      </section>
    </>
  );

  return (
    <PhoneFrame>
      <main className="pa" lang={lang} dir={DIR[lang]} data-testid="patient-root">
        <header className="pa-bar">
          <h1 className="pa-title" ref={headingRef} tabIndex={-1}>{tab === "home" ? t.appTitle : t.privacyHeading}</h1>
          {toggle}
        </header>

        <div className="pa-body">
          {offline && <p className="banner banner-warn" role="status">{t.offline}</p>}
          {tab === "home" ? homePanel : privacyPanel}
        </div>

        <nav className="pa-nav" aria-label={t.navLabel}>
          <button type="button" data-testid="tab-home" aria-current={tab === "home" ? "page" : undefined} onClick={() => setTab("home")}>
            <HeartIcon />{t.navHome}
          </button>
          <button type="button" data-testid="tab-privacy" aria-current={tab === "privacy" ? "page" : undefined} onClick={() => setTab("privacy")}>
            <ShieldIcon />{t.navPrivacy}
          </button>
        </nav>
      </main>
    </PhoneFrame>
  );
}

/** The pathway's plain three-step explainer under "My next step". */
function pathwayExplainer(steps: string[]) {
  return (
    <ol className="steps pa-steps">
      {steps.map((s) => <li key={s}>{s}</li>)}
    </ol>
  );
}
