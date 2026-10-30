/**
 * D13 in the web, for the demo: the coordinator's "Record patient reply" panel posts the patient's reply to
 * `POST /cases/{id}/replies`. The classifier behind it is the API's (a small offline model, or "unavailable" and then every reply
 * goes to a person); the mock stands in with a few keywords. What the panel must show is the same either way: what the triage
 * read, how sure it was, and whether a person must look.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ApiClient, ReplyResult } from "../src/api/contract";
import { createMockAdapter, mockClassifyReply } from "../src/api/mockAdapter";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { renderWithApp } from "./helpers";

async function openCase(role: "coordinator" | "owner" = "coordinator", api?: ApiClient) {
  const a = api ?? createMockAdapter(null, { automation: true });
  const id = (await a.listCases()).find((c) => c.state === "owner_accepted")?.case_id ?? (await a.listCases())[0].case_id;
  renderWithApp(<CaseDetailPage />, { api: a, role, route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
  await screen.findByTestId("reply-panel");
  return { api: a, id };
}

describe("the mock's keyword stand-in for the triage", () => {
  it.each([
    ["ان شاء الله بجي يوم الأحد", "confirm", true], // Gulf dialect (dialect.py, HZ-43): always to a person
    ["تمام، سأحضر يوم الأحد إن شاء الله", "confirm", false], // standard Arabic: the usual rules
    ["yes I will come on Sunday", "confirm", false],
    ["شنو يعني هالنتيجة؟", "question", true],
    ["please stop messaging me", "opt_out", true],
    ["this is not my number", "wrong_number", false], // handled: a wrong-number flag and an outreach hold, not a person's read
    ["blah blah", "other", true],
  ])("%s -> %s (needs a person: %s)", (text, intent, needs) => {
    expect(mockClassifyReply(text)).toMatchObject({ intent, needs_human: needs });
  });
});

describe("Record patient reply", () => {
  it("a Gulf-Arabic confirmation is read as confirm but always goes to a person, and the Why panel says the rule", async () => {
    const user = userEvent.setup();
    const { api, id } = await openCase();
    await user.type(screen.getByTestId("reply-text"), "ان شاء الله بجي يوم الأحد");
    await user.click(screen.getByTestId("record-reply"));
    const res = await screen.findByTestId("reply-result");
    expect(res).toHaveAttribute("data-intent", "confirm");
    expect(res).toHaveAttribute("data-needs-human", "true");
    expect(res.textContent).toMatch(/Read as confirm/);
    expect(res.textContent).toMatch(/A person must look at this reply/);
    expect(screen.getByTestId("why-triage-reply-rule")).toHaveTextContent("Gulf dialect: a person always checks these replies (safety rule HZ-43)");
    const events = (await api.getCase(id)).events.map((e) => e.type);
    expect(events).toEqual(expect.arrayContaining(["reply_received", "reply_classified", "review_requested"]));
  });

  it("an uncertain reply flags the case for a person and says so, and the case page shows the review banner", async () => {
    const user = userEvent.setup();
    const { api, id } = await openCase();
    await user.type(screen.getByTestId("reply-text"), "شنو يعني هالنتيجة؟");
    await user.click(screen.getByTestId("record-reply"));
    const res = await screen.findByTestId("reply-result");
    expect(res).toHaveAttribute("data-needs-human", "true");
    expect(res.textContent).toMatch(/A person must look at this reply/);
    await screen.findByTestId("triage-review-banner");
    const detail = await api.getCase(id);
    expect(detail.triage_review_reason).toBe("triage_uncertain");
    expect(detail.events.some((e) => e.type === "review_requested" && e.actor === "system:triage")).toBe(true);
  });

  it("an opt-out is always a person's and opens the outreach hold", async () => {
    const user = userEvent.setup();
    const { api, id } = await openCase();
    await user.type(screen.getByTestId("reply-text"), "please stop messaging me");
    await user.click(screen.getByTestId("record-reply"));
    expect((await screen.findByTestId("reply-result")).getAttribute("data-intent")).toBe("opt_out");
    expect((await api.getCase(id)).outreach_holds).toEqual(["opt_out"]);
  });

  it("is disabled without text and for a role that is not the coordinator", async () => {
    await openCase();
    expect(screen.getByTestId("record-reply")).toBeDisabled();
    expect(screen.getByTestId("record-reply-why").textContent).toMatch(/Enter the patient's reply/);
  });

  it("the owner cannot record a reply (the route is the coordinator's)", async () => {
    await openCase("owner");
    expect(screen.getByTestId("record-reply")).toBeDisabled();
    expect(screen.getByTestId("record-reply-why").textContent).toMatch(/Switch the role to coordinator/);
  });

  it("shows the API's own answer, including a fail-safe 'unavailable' model", async () => {
    const user = userEvent.setup();
    const base = createMockAdapter(null, { automation: true });
    const failSafe: ReplyResult = { reply_id: "r1", case_id: "c", channel: "whatsapp", intent: "other", confidence: 0, model_id: "unavailable", needs_human: true, logged: true, applied: ["reply_received", "reply_classified", "review_requested"], note: null };
    const api = { ...base, postReply: async () => failSafe } as ApiClient;
    await openCase("coordinator", api);
    await user.type(screen.getByTestId("reply-text"), "anything");
    await user.click(screen.getByTestId("record-reply"));
    const res = await screen.findByTestId("reply-result");
    expect(within(res).getByText(/Read as other/)).toBeInTheDocument();
    expect(res.textContent).toMatch(/0% sure, model: unavailable/);
    await waitFor(() => expect(screen.getByTestId("flash").textContent).toMatch(/a person must read it/));
  });
});
