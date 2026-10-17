import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DEMO_OVERDUE_CASE_ID } from "../src/api/fixtures";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { SignOffPanel } from "../src/components/SignOffPanel";
import { renderWithApp } from "./helpers";

describe("SignOffPanel", () => {
  it("is disabled until evidence ref AND signer are present, and explains why", async () => {
    const user = userEvent.setup();
    const onSignOff = vi.fn();
    renderWithApp(<SignOffPanel role="clinician" onSignOff={onSignOff} />);
    const btn = screen.getByTestId("signoff");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("signoff-why")).toHaveTextContent(/evidence reference/i);
    expect(screen.getByTestId("signoff-why")).toHaveTextContent(/name of the clinician/i);
    expect(btn).toHaveAccessibleDescription(/evidence reference/i);

    await user.type(screen.getByLabelText("Evidence reference"), "DiagnosticReport/c-1");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("signoff-why")).not.toHaveTextContent(/evidence reference/i);
    expect(screen.getByTestId("signoff-why")).toHaveTextContent(/name of the clinician/i);

    await user.type(screen.getByLabelText("Signed by"), "Dr. Huda Rahman");
    expect(btn).toBeEnabled();
    expect(screen.queryByTestId("signoff-why")).toBeNull();

    await user.click(btn);
    expect(onSignOff).toHaveBeenCalledWith("DiagnosticReport/c-1", "Dr. Huda Rahman");
  });

  it("stays disabled for non-clinician roles even with both fields filled", async () => {
    const user = userEvent.setup();
    renderWithApp(<SignOffPanel role="coordinator" onSignOff={() => undefined} />);
    await user.type(screen.getByLabelText("Evidence reference"), "x");
    await user.type(screen.getByLabelText("Signed by"), "y");
    expect(screen.getByTestId("signoff")).toBeDisabled();
    expect(screen.getByTestId("signoff-why")).toHaveTextContent(/only a clinician/i);
  });

  it("trims whitespace-only input as missing", async () => {
    const user = userEvent.setup();
    renderWithApp(<SignOffPanel role="clinician" onSignOff={() => undefined} />);
    await user.type(screen.getByLabelText("Evidence reference"), "   ");
    await user.type(screen.getByLabelText("Signed by"), "Dr X");
    expect(screen.getByTestId("signoff")).toBeDisabled();
  });
});

describe("Case detail: sign-off through the client", () => {
  it("clinician review + close are posted, case ends up closed", async () => {
    const user = userEvent.setup();
    // Dr. Layla Haddad (the "owner" login) owns this case, so she may sign it off.
    const { api } = renderWithApp(<CaseDetailPage />, {
      role: "owner", route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId",
    });
    await screen.findByTestId("signoff");
    const evidence = screen.getByLabelText("Evidence reference");
    await user.clear(evidence); // prefilled from the confirmatory result on the case
    await user.type(evidence, "DiagnosticReport/colonoscopy-0001");
    await user.type(screen.getByLabelText("Signed by"), "Dr. Layla Haddad");
    await user.click(screen.getByTestId("signoff"));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/closed/i));
    const d = await api.getCase(DEMO_OVERDUE_CASE_ID);
    expect(d.state).toBe("closed");
    expect(d.events.slice(-2).map((e) => e.type)).toEqual(["clinician_review", "close"]);
    expect(d.events.at(-1)?.payload).toMatchObject({ evidence_ref: "DiagnosticReport/colonoscopy-0001", signed_by: "Dr. Layla Haddad" });
    expect(d.events.at(-1)?.actor).toBe("primary_care_owner:Dr. Layla Haddad");
  });

  it("a clinician who does not own the case cannot sign it off, and is told why (owner scoping, A5)", async () => {
    renderWithApp(<CaseDetailPage />, { role: "clinician", route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("signoff");
    expect(screen.getByTestId("signoff")).toBeDisabled();
    expect(screen.getByTestId("signoff-why")).toHaveTextContent(/do not own this case.*Dr\. Layla Haddad/i);
  });
});
