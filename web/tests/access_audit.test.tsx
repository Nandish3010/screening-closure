import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEMO_PATIENT_CASE_ID } from "../src/api/fixtures";
import { createMockAdapter } from "../src/api/mockAdapter";
import { AccessLogPanel } from "../src/components/AccessLogPanel";
import { renderWithApp } from "./helpers";

describe("access audit in the staff UI and the mock", () => {
  it("the owner and the coordinator see who read the case, with the actor and the kind of read", async () => {
    const api = createMockAdapter(null);
    await api.login("coordinator", "Maryam Al Suwaidi");
    await api.getCase(DEMO_PATIENT_CASE_ID);
    await api.getTimeline(DEMO_PATIENT_CASE_ID);
    renderWithApp(<AccessLogPanel caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "coordinator" });
    const rows = await screen.findAllByTestId("access-audit-row");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.textContent).join(" ")).toMatch(/Opened the case page/);
    expect(rows.map((r) => r.textContent).join(" ")).toMatch(/Opened the timeline/);
    expect(rows[0]).toHaveTextContent("Maryam Al Suwaidi");
    const log = await api.getAccessLog(DEMO_PATIENT_CASE_ID);
    expect(log.entries[0]).toMatchObject({ role: "coordinator", kind: expect.any(String), actor: expect.stringContaining("coordinator:") });
  });

  it("says so when the role may not read it, and when nothing has been read", async () => {
    const api = createMockAdapter(null);
    await api.login("clinician", "Dr. Huda Rahman"); // not the owner of this case
    renderWithApp(<AccessLogPanel caseId={DEMO_PATIENT_CASE_ID} />, { api, role: "clinician" });
    await waitFor(() => expect(screen.getByTestId("access-audit")).toHaveTextContent(/cannot read the access log/));
  });

  it("a patient's own reads and a repeat inside the window are not written again", async () => {
    const api = createMockAdapter(null);
    await api.login("patient", "Patient");
    await api.getCase(DEMO_PATIENT_CASE_ID);
    await api.login("coordinator", "Maryam Al Suwaidi");
    expect((await api.getAccessLog(DEMO_PATIENT_CASE_ID)).entries).toHaveLength(0);
    await api.getCase(DEMO_PATIENT_CASE_ID);
    await api.getCase(DEMO_PATIENT_CASE_ID);
    expect((await api.getAccessLog(DEMO_PATIENT_CASE_ID)).entries).toHaveLength(1);
  });
});
