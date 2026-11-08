import { useState } from "react";
import { useApp } from "../app/AppContext";
import { STAFF } from "../i18n/strings";
import { stripInternalCodes } from "../lib/format";

/**
 * A26 (ADR-037): the outreach policy is unsigned and the demo runs the automatic messages under a placeholder approval. While
 * `GET /health` says `outreach.demo_approval` this amber banner stays on the case page and the dashboard, with the API's own
 * text ("DEMO PLACEHOLDER (clinical sign-off pending, H1)"). It is not dismissible: a viewer must never take the automatic
 * messages for a clinically approved process. Nothing is shown when the policy is really signed or the server reports nothing.
 */
export function OutreachDemoBanner() {
  const { outreach } = useApp();
  const [open, setOpen] = useState(false);
  if (!outreach || !outreach.demo_approval) return null;
  return (
    <div className={`banner banner-warn outreach-banner${open ? " is-open" : ""}`} role="status" data-testid="outreach-demo-banner">
      <p className="ob-full">
        <strong data-testid="outreach-demo-text">{stripInternalCodes(outreach.approved_by ?? STAFF.outreachDemoBanner.fallback)}</strong>{" "}
        {STAFF.outreachDemoBanner.explanation}
      </p>
      <span className="ob-compact" aria-hidden="true">{STAFF.outreachDemoBanner.compact}</span>
      <button type="button" className="ob-more" aria-expanded={open} onClick={() => setOpen((o) => !o)} data-testid="outreach-banner-more">
        {open ? "Less" : "More"}<span className="visually-hidden"> about the demo placeholder approval</span>
      </button>
    </div>
  );
}
