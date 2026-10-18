import { useApp } from "../app/AppContext";
import { STAFF } from "../i18n/strings";

/**
 * A26 (ADR-037): the outreach policy is unsigned and the demo runs the automatic messages under a placeholder approval. While
 * `GET /health` says `outreach.demo_approval` this amber banner stays on the case page and the dashboard, with the API's own
 * text ("DEMO PLACEHOLDER (clinical sign-off pending, H1)"). It is not dismissible: a viewer must never take the automatic
 * messages for a clinically approved process. Nothing is shown when the policy is really signed or the server reports nothing.
 */
export function OutreachDemoBanner() {
  const { outreach } = useApp();
  if (!outreach || !outreach.demo_approval) return null;
  return (
    <p className="banner banner-warn" role="status" data-testid="outreach-demo-banner">
      <strong data-testid="outreach-demo-text">{outreach.approved_by ?? STAFF.outreachDemoBanner.fallback}</strong>{" "}
      {STAFF.outreachDemoBanner.explanation}
    </p>
  );
}
