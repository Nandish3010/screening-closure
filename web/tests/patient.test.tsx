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

  it("consent controls post consent_updated", async () => {
    const user = userEvent.setup();
    const api = await open();
    await user.selectOptions(screen.getByLabelText(STRINGS.en.channelLabel), "whatsapp");
    await waitFor(async () => expect((await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)?.type).toBe("consent_updated"));
    await user.click(screen.getByRole("checkbox"));
    await waitFor(async () => {
      expect((await api.getCase(DEMO_PATIENT_CASE_ID)).events.at(-1)?.payload).toEqual({ channel: "whatsapp", share_with_specialist: true });
    });
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
