import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../src/app/AppContext";
import { createMockAdapter } from "../src/api/mockAdapter";
import { createHttpAdapter } from "../src/api/httpAdapter";
import type { ApiClient, IntegrationMockStatus, MalaffiReplayResult, Role, SahatnaCallbackResult } from "../src/api/contract";
import App from "../src/App";

const CASE = "7c1f0c52-0000-4000-8000-000000000001";
const NEW_CASE = "9a2e0c52-0000-4000-8000-000000000002";
const status = (used = 0): IntegrationMockStatus => ({
  mock: true, enabled: true, note: "No Malaffi or Sahatna specification was available to us.",
  malaffi: { label: "MOCK: a Malaffi-style inbound result replayed by this server. Not a connection to Malaffi.", replays_used: used, replays_max: 3 },
  sahatna: { label: "MOCK: Sahatna", eligible_cases: [{ case_id: CASE, state: "patient_acknowledged", pathway: "colorectal_fit" }] },
});
const malaffi = (outcome: MalaffiReplayResult["outcome"]): MalaffiReplayResult => ({
  mock: true, label: "MOCK", note: "n", message_type: "ORU^R01", control_id: "MOCK-MALAFFI-ORU-1", message: "MSH|^~\\&|LABSYS\nPID|1",
  ack: { code: "AA", reason: outcome === "duplicate" ? "duplicate message already processed" : null }, outcome, case_id: NEW_CASE, state: "acknowledged", replays_used: 1, replays_max: 3,
});
const sahatna: SahatnaCallbackResult = {
  mock: true, label: "MOCK", note: "n", message: "MSH|^~\\&|LABSYS\nSCH|SAH-1", translated_to: "HL7 SIU^S12", ack: { code: "AA", reason: null }, outcome: "booked",
  event: "book_confirmatory", case_id: CASE, state_before: "patient_acknowledged", state: "confirmatory_booked",
  callback: { callback_id: "MOCK-SAHATNA-x", status: "confirmed", case_id: CASE, appointment_ref: "SAH-7C1F0C52", slot_start: "2026-11-19T09:30:00+04:00" },
};

function open(api: ApiClient, role: Role = "coordinator") {
  return render(<MemoryRouter initialEntries={["/integrations"]}><AppProvider api={api} initialRole={role}><App /></AppProvider></MemoryRouter>);
}
function withMocks(over: Partial<ApiClient> = {}): ApiClient {
  return { ...createMockAdapter(null), integrationMockStatus: vi.fn(async () => status()), replayMalaffiResult: vi.fn(async (r?: boolean) => malaffi(r ? "duplicate" : "case_created")),
    sahatnaBookingCallback: vi.fn(async () => sahatna), ...over } as ApiClient;
}

describe("the MOCK Malaffi and Sahatna panel (ADR-057)", () => {
  it("is labelled a mock, claims no connection and no specification access, on every state of the panel", async () => {
    open(withMocks());
    const panel = await screen.findByTestId("mock-integrations");
    expect(within(panel).getByTestId("mock-chip")).toHaveTextContent("MOCK");
    expect(within(panel).getByTestId("mock-honesty")).toHaveTextContent(/mock of message shapes, not a connection/);
    expect(within(panel).getByTestId("mock-honesty")).toHaveTextContent(/no access to Malaffi's or Sahatna's specifications/);
    expect(await within(panel).findByTestId("mock-malaffi")).toHaveTextContent(/MOCK/);
    expect(within(panel).getByTestId("mock-sahatna")).toHaveTextContent(/invented callback|made up|invented/i);
    expect(screen.getByTestId("integrations-honesty")).toHaveTextContent(/Not connected/);
  });

  it("replays a result, shows the adapter's answer and the case, and sending it again says nothing changed", async () => {
    let used = 0;
    const api = withMocks({
      integrationMockStatus: vi.fn(async () => status(used)),
      replayMalaffiResult: vi.fn(async (r?: boolean) => { used = Math.max(used, 1); return malaffi(r ? "duplicate" : "case_created"); }),
    });
    open(api);
    expect(await screen.findByTestId("mock-malaffi-again")).toBeDisabled(); // nothing to send again yet
    fireEvent.click(await screen.findByTestId("mock-malaffi-replay"));
    const res = await screen.findByTestId("mock-result-malaffi");
    expect(api.replayMalaffiResult).toHaveBeenCalledWith(false);
    expect(within(res).getByTestId("mock-ack")).toHaveTextContent("AA");
    expect(within(res).getByTestId("mock-outcome")).toHaveTextContent(/new case was created/);
    expect(within(res).getByTestId("mock-case-link")).toHaveAttribute("href", `/coordinator/${NEW_CASE}`);
    expect(within(res).getByTestId("mock-message")).toHaveTextContent(/MSH/);
    await waitFor(() => expect(screen.getByTestId("mock-malaffi-again")).toBeEnabled());
    fireEvent.click(screen.getByTestId("mock-malaffi-again"));
    await waitFor(() => expect(screen.getByTestId("mock-outcome")).toHaveTextContent(/already processed/));
    expect(api.replayMalaffiResult).toHaveBeenLastCalledWith(true);
  });

  it("sends a booking callback for the chosen case and shows the case moving to booked by book_confirmatory", async () => {
    const api = withMocks();
    open(api);
    expect(await screen.findByTestId("mock-sahatna-case")).toHaveValue(CASE);
    fireEvent.click(screen.getByTestId("mock-sahatna-book"));
    const res = await screen.findByTestId("mock-result-sahatna");
    expect(api.sahatnaBookingCallback).toHaveBeenCalledWith(CASE);
    expect(res).toHaveTextContent(/confirmatory_booked/);
    expect(res).toHaveTextContent(/book_confirmatory/);
    expect(within(res).getByTestId("mock-callback-json")).toHaveTextContent(/MOCK-SAHATNA-x/);
  });

  it("disables the replay when the mock messages are used up, and says when no case can be booked", async () => {
    const api = withMocks({ integrationMockStatus: vi.fn(async () => ({ ...status(3), sahatna: { label: "x", eligible_cases: [] } })) });
    open(api);
    expect(await screen.findByTestId("mock-malaffi-replay")).toBeDisabled();
    expect(screen.getByTestId("mock-sahatna-none")).toBeInTheDocument();
    expect(screen.queryByTestId("mock-sahatna-book")).toBeNull();
  });

  it("shows a refusal from the adapter as a refusal, and an API error as an alert", async () => {
    const refused: SahatnaCallbackResult = { ...sahatna, outcome: "rejected", ack: { code: "AE", reason: "the event is not valid in the case's current state" }, event: null, state_before: "acknowledged", state: "acknowledged" };
    open(withMocks({ sahatnaBookingCallback: vi.fn(async () => refused) }));
    fireEvent.click(await screen.findByTestId("mock-sahatna-book"));
    expect(await screen.findByTestId("mock-outcome")).toHaveTextContent(/refused the message/);
    expect(screen.getByTestId("mock-result-sahatna")).toHaveTextContent(/not valid in the case's current state/);
    expect(screen.getByTestId("mock-ack")).toHaveTextContent("AE");
  });

  it("explains itself where the routes are off (404/403: status null) and in the offline copy (no API methods)", async () => {
    const off = open(withMocks({ integrationMockStatus: vi.fn(async () => null) }));
    expect(await screen.findByTestId("mock-unavailable")).toHaveTextContent(/development or demo deployment/);
    expect(screen.queryByTestId("mock-malaffi")).toBeNull();
    off.unmount();
    open(createMockAdapter(null));
    expect(await screen.findByTestId("mock-offline")).toHaveTextContent(/running demo stack/);
    expect(screen.queryByTestId("mock-malaffi")).toBeNull();
  });
});

describe("the http adapter's mock calls", () => {
  const fetchOf = (routes: Record<string, { status?: number; body: unknown }>) => {
    const calls: { key: string; body?: string }[] = [];
    const fn = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(url).replace("http://api.test", "")}`;
      calls.push({ key, body: init?.body as string | undefined });
      const r = routes[key];
      return new Response(JSON.stringify(r?.body ?? { detail: "no route" }), { status: r ? (r.status ?? 200) : 404 });
    });
    return { fn: fn as unknown as typeof fetch, calls };
  };
  it("posts the redelivery flag and the case id, and maps 404 and 403 on the status to null", async () => {
    const { fn, calls } = fetchOf({
      "POST /integrations/mock/malaffi/replay": { body: malaffi("case_created") },
      "POST /integrations/mock/sahatna/booking-callback": { body: sahatna },
      "GET /integrations/mock": { status: 404, body: { detail: "Not Found" } },
    });
    const api = createHttpAdapter("http://api.test", fn);
    await api.replayMalaffiResult!(true);
    await api.sahatnaBookingCallback!(CASE);
    expect(await api.integrationMockStatus!()).toBeNull();
    expect(calls.map((c) => c.key)).toEqual(["POST /integrations/mock/malaffi/replay", "POST /integrations/mock/sahatna/booking-callback", "GET /integrations/mock"]);
    expect(JSON.parse(calls[0].body!)).toEqual({ redeliver: true });
    expect(JSON.parse(calls[1].body!)).toEqual({ case_id: CASE });
  });
});
