/**
 * The guided path's fresh cases (ADR-057). `GET /demo/guide?session=` gives each browser session one untouched case per step, held
 * for it for a while, so two judges walking the same step are given different cases. The session id is random, lives in
 * sessionStorage (one per tab) and identifies no one. Outside the hosted demo the API has no such route and `api.demoGuide` is absent.
 */
import { useCallback, useEffect, useState } from "react";
import type { ApiClient, DemoGuide, GuideStepId, GuidedCase } from "../api/contract";

const SESSION_KEY = "sc.guide.session";

export function guideSession(): string {
  try {
    const have = sessionStorage.getItem(SESSION_KEY);
    if (have && /^[A-Za-z0-9_-]{8,64}$/.test(have)) return have;
    const fresh = `j${Math.random().toString(36).slice(2, 12)}${Date.now().toString(36)}`;
    sessionStorage.setItem(SESSION_KEY, fresh);
    return fresh;
  } catch {
    return `j${Math.random().toString(36).slice(2, 14)}`;
  }
}

/** The guide for this tab, and a way to ask again (after a step changed a case, or on a reset). null while loading or when the demo offers none. */
export function useGuide(api: ApiClient, enabled: boolean): { guide: Record<GuideStepId, GuidedCase | null> | null; loaded: boolean; reload: () => void } {
  const [guide, setGuide] = useState<DemoGuide["steps"] | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled || !api.demoGuide) { setLoaded(true); return; }
    let live = true;
    api.demoGuide(guideSession())
      .then((g) => live && setGuide(g.steps))
      .catch(() => live && setGuide(null)) // a failed read is the worklist fallback, not an error page
      .finally(() => live && setLoaded(true));
    return () => { live = false; };
  }, [api, enabled, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { guide, loaded, reload };
}

const SEEN_KEY = "sc.guide.seen";
/** The steps this tab has opened (for the checkmarks on Start here). */
export function seenSteps(): Set<string> {
  try { return new Set(JSON.parse(sessionStorage.getItem(SEEN_KEY) ?? "[]") as string[]); } catch { return new Set(); }
}
export function markSeen(id: string): void {
  try { const s = seenSteps(); s.add(id); sessionStorage.setItem(SEEN_KEY, JSON.stringify([...s])); } catch { /* the checkmark is a convenience */ }
}
