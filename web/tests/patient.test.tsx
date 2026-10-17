import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DEMO_PATIENT_CASE_ID } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";
import { ApiError } from "../src/api/contract";
import { STRINGS } from "../src/i18n/strings";
import { PatientView } from "../src/pages/PatientPage";
import { renderWithApp } from "./helpers";

const open = async (api = createMockAdapter(null)) => {
  renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "patient" });
  await screen.findByText(STRINGS.en.resultBody);
  return api;
};

describe("Patient page", () => {
  it("renders the plain-language result, next steps and owner", async () => {
    await open();
    expect(screen.getByText(/does not mean you have cancer/i)).toBeInTheDocument();
    expect(screen.getByTestId("patient-owner")).toHaveTextContent("Sara Al Mansoori");
    expect(screen.getByTestId("patient-owner")).toHaveTextContent("Care coordinator");
    expect(screen.getAllByRole("listitem").length).toBeGreaterThan(3);
  });

  it("Arabic toggle sets dir=rtl and lang=ar and swaps strings; toggling back restores ltr", async () => {
    const user = userEvent.setup();
    await open();
    const root = screen.getByTestId("patient-root");
    expect(root).toHaveAttribute("dir", "ltr");
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    expect(root).toHaveAttribute("dir", "rtl");
    expect(root).toHaveAttribute("lang", "ar");
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(screen.getByText(STRINGS.ar.resultBody)).toBeInTheDocument();
    expect(screen.queryByText(STRINGS.en.resultBody)).toBeNull();
    expect(screen.getByRole("button", { name: STRINGS.ar.dontUnderstand })).toBeInTheDocument();
    expect(screen.getByTestId("patient-owner").textContent).toContain("منسق الرعاية");
    await user.click(screen.getByRole("button", { name: /English/ }));
    expect(root).toHaveAttribute("dir", "ltr");
    expect(screen.getByText(STRINGS.en.resultBody)).toBeInTheDocument();
  });

  it('"I don\'t understand this" posts callback_requested (with the current language)', async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByRole("button", { name: STRINGS.en.dontUnderstand }));
    await screen.findByTestId("callback-done");
    const last = (await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)!;
    expect(last.type).toBe("callback_requested");
    expect(last.actor).toBe(`patient:${DEMO_PATIENT_CASE_ID}`);
    expect(last.payload).toMatchObject({ reason: "patient_does_not_understand", lang: "en" });
  });

  it("posts the callback in Arabic with lang=ar", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    await user.click(screen.getByRole("button", { name: STRINGS.ar.dontUnderstand }));
    await screen.findByText(STRINGS.ar.callbackDone);
    expect((await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)?.payload.lang).toBe("ar");
  });

  it("acknowledge posts patient_acknowledge and moves the case on", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByRole("button", { name: STRINGS.en.understand }));
    await screen.findByTestId("ack-done");
    const d = await api.getCase(DEMO_PATIENT_CASE_ID);
    expect(d.state).toBe("patient_acknowledged");
    expect(d.events.at(-1)?.type).toBe("patient_acknowledge");
  });

  it("consent controls post partial consent_updated booleans (A6)", async () => {
    const user = userEvent.setup();
    const api = await open();
    const last = async () => (await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)!;
    // Everything is allowed until the patient says otherwise.
    for (const c of ["Text message", "WhatsApp", "Phone call", "Letter"]) expect(screen.getByRole("checkbox", { name: c })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "Text message" }));
    await waitFor(async () => expect((await last()).payload).toEqual({ channel_sms: false }));
    expect((await last()).type).toBe("consent_updated");
    await user.click(screen.getByRole("checkbox", { name: STRINGS.en.shareLabel }));
    await waitFor(async () => expect((await last()).payload).toEqual({ share_with_specialist: false }));
    const detail = await api.getCase(DEMO_PATIENT_CASE_ID);
    expect(detail.consent).toMatchObject({ channel_sms: false, channel_whatsapp: true, share_with_specialist: false, withdrawn: false });
    expect(detail.consent_history).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "Text message" })).not.toBeChecked();
  });

  it("withdrawing consent freezes outreach and flags the case for review; the patient can resume", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.click(screen.getByTestId("consent-withdraw"));
    await screen.findByTestId("consent-withdrawn");
    let d = await api.getCase(DEMO_PATIENT_CASE_ID);
    expect(d.events.at(-2)).toMatchObject({ type: "consent_updated", payload: { withdrawn: true } });
    expect(d.events.at(-1)).toMatchObject({ type: "review_requested", actor: "system:consent", payload: { reason: "consent_withdrawn" } });
    expect(d).toMatchObject({ consent_frozen: true, review_reason: "consent_withdrawn" });
    await user.click(screen.getByTestId("consent-resume"));
    await waitFor(() => expect(screen.queryByTestId("consent-withdrawn")).toBeNull());
    d = await api.getCase(DEMO_PATIENT_CASE_ID);
    expect(d).toMatchObject({ consent_frozen: false, review_reason: null });
  });

  it("the withdraw control works in Arabic and the notice is Arabic", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    await user.click(screen.getByRole("button", { name: STRINGS.ar.withdrawButton }));
    expect(await screen.findByText(STRINGS.ar.withdrawnNotice)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: STRINGS.ar.resumeButton })).toBeInTheDocument();
  });

  it("renders the last saved case when the API is unreachable", async () => {
    const api = createMockAdapter(null);
    const first = renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "patient" });
    await screen.findByText(STRINGS.en.resultBody);
    first.unmount();
    const broken = { ...api, getCase: async () => { throw new ApiError(0, "offline"); } };
    renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { api: broken, role: "patient" });
    expect(await screen.findByText(STRINGS.en.offline)).toBeInTheDocument();
    expect(within(screen.getByTestId("patient-root")).getByText(STRINGS.en.resultBody)).toBeInTheDocument();
  });
});
