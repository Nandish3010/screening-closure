/**
 * Insurance-aware referral routing, as the web shows it (ADR-054). The server decides and records the network of each referral
 * request; this file holds the words, and the mock adapter's mirror of the server's rule so a demo without a backend behaves the
 * same. The networks below are SYNTHETIC and mirror config/providers.yaml for the bowel list only.
 */
import type { CaseEvent, CaseInsurance } from "../api/contract";

export type Network = "in_network" | "out_of_network";

export const SCHEME_LABEL: Record<string, string> = { thiqa: "Thiqa", enhanced: "Enhanced", basic: "Basic", unknown: "Unknown" };
export const schemeLabel = (scheme: string | null | undefined): string => SCHEME_LABEL[scheme ?? ""] ?? "Unknown";

/** Shown beside the plan: the scheme is a demo value, the networks are invented. */
export const INSURANCE_NOTE = "Synthetic insurance data. It decides which unit a referral prefers; it never holds or blocks a case.";

/** Mock only: the schemes each bowel referral provider accepts (mirror of config/providers.yaml `referral.networks`). */
const MOCK_NETWORKS: Record<string, readonly string[]> = {
  "Corniche Gastro Unit": ["thiqa", "enhanced", "basic"],
  "Al Noor Endoscopy Centre": ["thiqa", "enhanced", "basic"],
  "Marina Digestive Health": ["thiqa", "basic"],
};
const KNOWN = ["thiqa", "enhanced", "basic"];

/** In or out of the scheme's network; null when the scheme is none or `unknown` (the question does not arise). */
export function networkOf(provider: string, scheme: string | null | undefined): Network | null {
  if (!scheme || !KNOWN.includes(scheme)) return null;
  const accepted = MOCK_NETWORKS[provider];
  return !accepted || accepted.includes(scheme) ? "in_network" : "out_of_network";
}

/** The next provider to ask: the first not yet asked that is in network; else the first not yet asked (flagged); else null. */
export function routeNext(list: readonly string[], scheme: string | null | undefined, asked: ReadonlySet<string>): { provider: string; network: Network | null } | null {
  const left = list.filter((p) => !asked.has(p));
  if (left.length === 0) return null;
  const inNet = left.find((p) => networkOf(p, scheme) === "in_network");
  if (inNet) return { provider: inNet, network: "in_network" };
  return { provider: left[0], network: networkOf(left[0], scheme) };
}

/** The plain-words answer to "why this provider?" (mirror of api/app/insurance.py `reason`). */
export function insuranceReason(scheme: string, network: Network | null, auto: boolean): string | null {
  if (!network) return null;
  const plan = `the patient's ${schemeLabel(scheme)} plan`;
  if (network === "in_network") {
    return auto ? `Chosen because it accepts ${plan} and is the first such unit in the programme's referral order.` : `This unit accepts ${plan}.`;
  }
  return auto
    ? `Out of network: no remaining unit for this service accepts ${plan}, so the referral goes to the best available unit outside it. A coordinator should check the cover with the patient. The referral is not blocked.`
    : `Out of network: this unit does not accept ${plan}. A coordinator should check the cover with the patient or choose a unit that does. The referral is not blocked.`;
}

/** Mock only: the `insurance` block of a case row from its events (the API builds the same from the genesis event). */
export function insuranceOfEvents(events: readonly CaseEvent[], list: readonly string[]): CaseInsurance | null {
  const scheme = events[0]?.payload?.insurance_scheme;
  if (typeof scheme !== "string") return null;
  const request = [...events].reverse().find((e) => e.type === "handoff_requested");
  const network = (request?.payload.network as Network | undefined) ?? null;
  const auto = request?.payload.auto === true;
  return {
    scheme, label: schemeLabel(scheme), synthetic: true,
    providers: list.map((name) => ({ name, network: networkOf(name, scheme) })),
    referral: request ? { provider: String(request.payload.to_provider), network, out_of_network: network === "out_of_network", auto, reason: insuranceReason(scheme, network, auto) } : null,
  };
}
