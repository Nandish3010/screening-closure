import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type ApiClient, type Role } from "../api/contract";

/**
 * S1 / S4: the hosted demo's one-click sign-in, shared by the role cards on the sign-in page, the header's "Sign in as" switch and the
 * Judge tour's role switch. It copes with a demo that is still starting (VM restart) and with a shared network that has used its
 * allowance, and it never falls back to the password flow: the password route is for operators only and has its own link.
 */
export const SIGNIN_RETRY_MAX_MS = 120_000; // keep trying on its own for about two minutes, then hand the button back
const FINAL_SIGNIN_CODES = new Set(["signin_locked", "signin_refused", "signin_not_configured", "signin_unknown_role"]);

/** True when the identity provider (or the edge in front of it) is not ready yet: 502/503/504 or no answer at all, unless the API said it is final. */
export function signInIsWarming(e: unknown): boolean {
  return e instanceof ApiError && !(e.code && FINAL_SIGNIN_CODES.has(e.code)) && [0, 502, 503, 504].includes(e.status);
}

/** 3, 3, 4, 4, 5, 5, ... seconds (never less than the server's Retry-After, never more than 5 s unless it asks for more). */
export function signInRetryDelayMs(attempt: number, retryAfter?: number): number {
  return Math.max(Math.min(5, 3 + Math.floor(attempt / 2)), retryAfter && retryAfter <= 10 ? retryAfter : 0) * 1000;
}

export type SignInPhase =
  | { kind: "idle" }
  | { kind: "signing"; role: Role }
  | { kind: "warming"; role: Role }
  | { kind: "gave-up"; role: Role }
  | { kind: "limited"; seconds: number };

export interface DemoSignIn {
  phase: SignInPhase;
  /** A plain-words reason when the last try failed for good (not warming up, not rate-limited). */
  failed: string | null;
  /** One click: try, and while the demo is starting up retry by itself. `onDone` runs once the tokens are stored. */
  go: (role: Role) => void;
  /** Stop retrying and hand control back. */
  stop: () => void;
  /** The role being signed in right now (or being retried), if any. */
  busyRole: Role | null;
  /** True while a try, a retry or a rate-limit wait is under way: sign-in buttons should be off. */
  locked: boolean;
}

export function useDemoSignIn(api: ApiClient, onDone?: (role: Role) => void): DemoSignIn {
  const [phase, setPhase] = useState<SignInPhase>({ kind: "idle" });
  const [failed, setFailed] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);
  const done = useRef(onDone);
  done.current = onDone;
  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      clear();
    };
  }, [clear]);

  const run = useCallback(async (role: Role, attempt: number, startedAt: number) => {
    clear();
    setFailed(null);
    setPhase(attempt === 0 ? { kind: "signing", role } : { kind: "warming", role });
    try {
      await api.demoSignIn!(role);
      if (alive.current) setPhase({ kind: "idle" }); // normally the page is replaced by the signed-in app before this matters
      done.current?.(role);
    } catch (e) {
      if (!alive.current) return;
      if (signInIsWarming(e)) {
        if (Date.now() - startedAt >= SIGNIN_RETRY_MAX_MS) {
          setPhase({ kind: "gave-up", role });
          return;
        }
        setPhase({ kind: "warming", role });
        timer.current = setTimeout(() => void run(role, attempt + 1, startedAt), signInRetryDelayMs(attempt, e instanceof ApiError ? e.retryAfter : undefined));
      } else if (e instanceof ApiError && e.status === 429) {
        setPhase({ kind: "limited", seconds: e.retryAfter ?? 60 });
      } else {
        setFailed(e instanceof Error && e.message ? e.message : "One-click sign-in did not work.");
        setPhase({ kind: "idle" });
      }
    }
  }, [api, clear]);

  const go = useCallback((role: Role) => void run(role, 0, Date.now()), [run]);
  const stop = useCallback(() => {
    clear();
    setPhase({ kind: "idle" });
  }, [clear]);

  // The "please wait N seconds" count-down for a 429; the buttons come back at zero.
  const waiting = phase.kind === "limited";
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => setPhase((p) => (p.kind !== "limited" ? p : p.seconds <= 1 ? { kind: "idle" } : { kind: "limited", seconds: p.seconds - 1 })), 1000);
    return () => clearInterval(id);
  }, [waiting]);

  const busyRole = phase.kind === "signing" || phase.kind === "warming" || phase.kind === "gave-up" ? phase.role : null;
  const locked = phase.kind !== "idle" && phase.kind !== "gave-up";
  return { phase, failed, go, stop, busyRole, locked };
}

/** The status messages for the warming-up, gave-up and rate-limited phases, with the Retry button. Shared by every place that signs in. */
export function SignInNotice({ s, compact = false }: { s: DemoSignIn; compact?: boolean }) {
  const { phase } = s;
  const cls = compact ? "why signin-notice" : "why";
  return (
    <>
      {phase.kind === "warming" && (
        <div role="status" className={cls} data-testid="signin-warming">
          <p>The demo is starting up. Retrying automatically...</p>
          <button type="button" className="btn btn-small" onClick={() => s.go(phase.role)}>Retry now</button>{" "}
          <button type="button" className="linklike" onClick={s.stop}>Stop retrying</button>
        </div>
      )}
      {phase.kind === "gave-up" && (
        <div role="alert" className={cls} data-testid="signin-gave-up">
          <p>The demo is taking longer than usual to start. Please try again in a minute.</p>
          <button type="button" className="btn btn-small" onClick={() => s.go(phase.role)}>Retry</button>
        </div>
      )}
      {phase.kind === "limited" && (
        <p role="status" className={cls} data-testid="signin-limited">Too many sign-ins from this network, please wait {phase.seconds} second{phase.seconds === 1 ? "" : "s"}, then try again.</p>
      )}
      {s.failed && <p role="alert" className={cls}>{s.failed}</p>}
    </>
  );
}
