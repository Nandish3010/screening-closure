import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DEMO_OVERDUE_CASE_ID } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";
import { abstentionText } from "../src/components/DraftsPanel";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import type { Role } from "../src/api/contract";
import { renderWithApp } from "./helpers";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;
const openCase = (caseId: string, role: Role = "coordinator", api = createMockAdapter(null)) =>
  renderWithApp(<CaseDetailPage />, { api, role, route: `/coordinator/${caseId}`, path: "/coordinator/:caseId" });
const newDraft = () => screen.findByTestId(/^draft-d-mock-/);

describe("C10: requesting a draft", () => {
  it("sends the chosen purpose, channel and language, and shows the text in that language and direction", async () => {
    const user = userEvent.setup();
    const { api } = openCase(cid(7)); // the patient has not stated a language: English by default
    const form = await screen.findByTestId("draft-form");
    expect(within(form).getByLabelText("Language")).toHaveValue("en");
    await user.selectOptions(within(form).getByLabelText("Purpose"), "reminder");
    await user.selectOptions(within(form).getByLabelText("Channel"), "whatsapp");
    await user.click(screen.getByTestId("request-draft"));
    const item = await newDraft();
    expect(item).toHaveTextContent(/Reminder, WhatsApp, English/);
    expect(within(item).getByText(/reminder from your care team/i).closest("p")).toHaveAttribute("dir", "ltr");
    const [rec] = await api.listDrafts(cid(7));
    expect(rec).toMatchObject({ purpose: "reminder", channel: "whatsapp", language: "en" });
    await user.selectOptions(within(form).getByLabelText("Language"), "ar");
    await user.click(screen.getByTestId("request-draft"));
    await waitFor(() => expect(screen.getAllByTestId(/^draft-d-mock-/)).toHaveLength(2));
    const ar = screen.getAllByTestId(/^draft-d-mock-/)[1];
    expect(within(ar).getByText(/تذكير/).closest("p")).toHaveAttribute("dir", "rtl");
    expect(within(ar).getByText(/تذكير/).closest("p")).toHaveAttribute("lang", "ar");
  });

  it("defaults the language to the patient's own (case 1 answered in Arabic)", async () => {
    openCase(DEMO_OVERDUE_CASE_ID);
    expect(await within(await screen.findByTestId("draft-form")).findByLabelText("Language")).toHaveValue("ar");
  });

  it("an appointment confirmation without a booked date is refused by the engine and the reason is shown", async () => {
    const user = userEvent.setup();
    openCase(cid(7));
    await user.selectOptions(await screen.findByLabelText("Purpose"), "appointment_confirmation");
    expect(screen.getByText(/needs a booked appointment/i)).toBeInTheDocument();
    await user.click(screen.getByTestId("request-draft"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no booked appointment date/i);
  });

  it("is disabled with the reason while consent is frozen or the case is closed", async () => {
    openCase(cid(14));
    expect(await screen.findByTestId("request-draft")).toBeDisabled();
    expect(screen.getByTestId("request-draft-why")).toHaveTextContent(/consent is frozen/i);
    expect(screen.getByTestId("consent-frozen-banner")).toHaveTextContent(/flagged for human review/i);
  });
});

describe("C10: provenance on each draft", () => {
  it("shows the provider, the fallback badge, the abstention reason and the prompt hash (short, full on hover)", async () => {
    openCase(cid(2));
    const item = await screen.findByTestId("draft-d-seed-2");
    expect(within(item).getByTestId("provider-d-seed-2")).toHaveTextContent("template");
    expect(within(item).getByTestId("fallback-d-seed-2")).toHaveTextContent("template fallback used");
    expect(within(item).getByTestId("abstention-d-seed-2")).toHaveTextContent(/AI abstained.*AI provider was unavailable/);
    const hash = within(item).getByTestId("hash-d-seed-2");
    expect(hash.textContent).toHaveLength(12);
    expect(hash).toHaveAttribute("title", expect.stringMatching(/^[0-9a-f]{64}$/));
    expect(hash.getAttribute("title")!.startsWith(hash.textContent!)).toBe(true);
    // The seeded draft was drafted by the model actor, with the automated chip, and is still pending.
    expect(item).toHaveTextContent(/Waiting for the case owner/);
  });

  it("a draft with no abstention shows no abstention banner and no fallback badge", async () => {
    openCase(DEMO_OVERDUE_CASE_ID);
    const item = await screen.findByTestId("draft-d-seed-1");
    expect(within(item).queryByTestId("abstention-d-seed-1")).toBeNull();
    expect(within(item).queryByTestId("fallback-d-seed-1")).toBeNull();
    expect(within(item).getByTestId("approved-d-seed-1")).toHaveTextContent(/Approved by Dr\. Layla Haddad/);
    expect(within(item).getByTestId("sent-d-seed-1")).toBeInTheDocument();
  });

  it("turns abstention codes into plain words and keeps unknown codes visible", () => {
    expect(abstentionText("cache_miss")).toMatch(/no recorded AI output/);
    expect(abstentionText("validation_failed:forbidden_phrase:cancer,url")).toMatch(/failed the safety checks \(forbidden phrase cancer url\)/);
    expect(abstentionText("something_new")).toBe("something new");
  });
});

describe("C10: approving and rejecting", () => {
  it("the owner approves: approved by, sent, and the timeline text matches the panel", async () => {
    const user = userEvent.setup();
    openCase(DEMO_OVERDUE_CASE_ID, "owner");
    await user.click(await screen.findByTestId("request-draft"));
    const item = await newDraft();
    await user.click(within(item).getByRole("button", { name: /approve draft/i }));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent("Draft approved and sent."));
    expect(within(item).getByTestId(/^approved-d-mock/)).toHaveTextContent(/Approved by Dr\. Layla Haddad/);
    expect(within(item).getByTestId(/^sent-d-mock/)).toBeInTheDocument();
    expect(within(item).queryByRole("button", { name: /approve draft/i })).toBeNull();
    expect(screen.getByTestId("timeline")).toHaveTextContent("Message sent to the patient");
  });

  it("approved but blocked: the answer's blocked_reason is shown instead of 'Sent', with the plain-words reason", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { channel_sms: false } });
    openCase(DEMO_OVERDUE_CASE_ID, "owner", api);
    await user.click(await screen.findByTestId("request-draft"));
    const item = await newDraft();
    await user.click(within(item).getByRole("button", { name: /approve draft/i }));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/approved but blocked: consent/i));
    expect(within(item).getByTestId(/^blocked-d-mock/)).toHaveTextContent("Approved but blocked: consent");
    expect(item).toHaveTextContent(/Nothing was sent: the patient has switched that channel off/);
    expect(within(item).queryByTestId(/^sent-d-mock/)).toBeNull();
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events.at(-1)?.type).toBe("message_approved");
  });

  it("flagged contact details block the send with reason 'contact'", async () => {
    // The mock takes the flag from the case_created payload, like the notification mock does.
    const user = userEvent.setup();
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) };
    const seeded = createMockAdapter(storage);
    await seeded.getCase(cid(6)); // writes the seed
    const key = [...store.keys()][0];
    const db = JSON.parse(store.get(key)!);
    db.events[cid(6)][0].payload.contact_valid = { sms: false };
    store.set(key, JSON.stringify(db));
    openCase(cid(6), "owner", createMockAdapter(storage));
    await user.click(await screen.findByTestId("request-draft"));
    const item = await newDraft();
    await user.click(within(item).getByRole("button", { name: /approve draft/i }));
    expect(await within(item).findByTestId(/^blocked-d-mock/)).toHaveTextContent("Approved but blocked: contact");
  });

  it("reject needs a reason from the list; the draft shows who rejected it and why, and nothing is sent", async () => {
    const user = userEvent.setup();
    const { api } = openCase(DEMO_OVERDUE_CASE_ID, "owner");
    await user.click(await screen.findByTestId("request-draft"));
    const item = await newDraft();
    const reject = within(item).getByRole("button", { name: /reject draft/i });
    expect(reject).toBeDisabled();
    expect(within(item).getByTestId(/^reject-d-mock-\d+-why$/)).toHaveTextContent(/reason/i);
    const select = within(item).getByLabelText("Reason for rejecting") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["", "inaccurate", "tone", "wrong_language", "not_needed", "other_with_note"]);
    await user.selectOptions(select, "wrong_language");
    await user.click(reject);
    await waitFor(() => expect(within(item).getByTestId(/^rejected-d-mock/)).toHaveTextContent(/Rejected by Dr\. Layla Haddad: Wrong language/));
    expect(within(item).queryByRole("button", { name: /approve draft/i })).toBeNull();
    expect(item).toHaveTextContent(/Nothing was sent/);
    expect((await api.listDrafts(DEMO_OVERDUE_CASE_ID)).at(-1)).toMatchObject({ rejection_reason: "wrong_language" });
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events.at(-1)).toMatchObject({ type: "message_rejected", payload: { reason: "wrong_language" } });
  });

  it("the note box is only for Other: it is disabled otherwise, and Other cannot be sent without a note", async () => {
    const user = userEvent.setup();
    const { api } = openCase(DEMO_OVERDUE_CASE_ID, "owner");
    await user.click(await screen.findByTestId("request-draft"));
    const item = await newDraft();
    const note = within(item).getByLabelText(/^Note/) as HTMLInputElement;
    const reject = within(item).getByRole("button", { name: /reject draft/i });
    expect(note).toBeDisabled();
    await user.selectOptions(within(item).getByLabelText("Reason for rejecting"), "other_with_note");
    expect(note).toBeEnabled();
    expect(reject).toBeDisabled();
    expect(within(item).getByTestId(/^reject-d-mock-\d+-why$/)).toHaveTextContent(/note/i);
    await user.type(note, "Names the wrong clinic");
    expect(reject).toBeEnabled();
    await user.click(reject);
    await waitFor(() => expect(within(item).getByTestId(/^rejected-d-mock/)).toHaveTextContent(/Rejected by Dr\. Layla Haddad: Other: Names the wrong clinic/));
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events.at(-1)).toMatchObject({ type: "message_rejected", payload: { reason: "other_with_note" } });
    expect((await api.getCase(DEMO_OVERDUE_CASE_ID)).events.at(-1)!.payload).not.toHaveProperty("note");
  });

  it("a coordinator sees ONE inline explanation of why they cannot approve; both buttons are disabled and linked to it", async () => {
    openCase(cid(2));
    const item = await screen.findByTestId("draft-d-seed-2");
    const note = screen.getByTestId("decide-why");
    expect(note).toHaveTextContent(/can request drafts, but only the case owner can approve or reject them/i);
    for (const name of [/approve draft/i, /reject draft/i]) {
      const b = within(item).getByRole("button", { name });
      expect(b).toBeDisabled();
      expect(b).toHaveAccessibleDescription(/only the case owner/i);
    }
    expect(screen.getByTestId("request-draft")).toBeEnabled();
  });

  it("a clinician who does not own the case is told who does", async () => {
    openCase(cid(2), "clinician");
    await screen.findByTestId("draft-d-seed-2");
    expect(screen.getByTestId("decide-why")).toHaveTextContent(/do not own this case.*Sara Al Mansoori/i);
  });

  it("the owner of a case whose consent was withdrawn can reject but not approve a pending draft", async () => {
    const user = userEvent.setup();
    const api = createMockAdapter(null);
    await api.login("owner", "Dr. Layla Haddad", "Riverside Primary Care");
    const d = await api.requestDraft(DEMO_OVERDUE_CASE_ID, { purpose: "reminder", channel: "sms", language: "en" });
    await api.postEvent(DEMO_OVERDUE_CASE_ID, { type: "consent_updated", actor: "patient:x", payload: { withdrawn: true } });
    openCase(DEMO_OVERDUE_CASE_ID, "owner", api);
    const item = await screen.findByTestId(`draft-${d.draft_id}`);
    expect(within(item).getByRole("button", { name: /approve draft/i })).toBeDisabled();
    expect(within(item).getByTestId(`approve-${d.draft_id}-why`)).toHaveTextContent(/consent is frozen/i);
    await user.selectOptions(within(item).getByLabelText("Reason for rejecting"), "not_needed");
    expect(within(item).getByRole("button", { name: /reject draft/i })).toBeEnabled();
  });
});
