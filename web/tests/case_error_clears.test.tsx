/**
 * A failed case fetch (for example a 404 answered to the previous login) must not leave its red banner behind once a later fetch
 * succeeds: the role change reloads the case, and the stale "Case not found." used to stay on screen.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiError, type ApiClient } from "../src/api/contract";
import { useApp } from "../src/app/AppContext";
import { createMockAdapter } from "../src/api/mockAdapter";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { renderWithApp } from "./helpers";

function SwitchToClinician() {
  const { setRole } = useApp();
  return <button onClick={() => setRole("clinician")}>switch</button>;
}

describe("Case page error banner", () => {
  it("is cleared by the next successful fetch", async () => {
    const user = userEvent.setup();
    const base = createMockAdapter(null, { automation: true });
    const id = (await base.listCases())[0].case_id;
    let calls = 0;
    const api: ApiClient = {
      ...base,
      getCase: async (cid: string) => {
        if (++calls === 1) throw new ApiError(404, "not found");
        return base.getCase(cid);
      },
    };
    renderWithApp(<><SwitchToClinician /><CaseDetailPage /></>, { api, role: "patient", route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
    expect(await screen.findByText("Case not found.")).toBeInTheDocument();
    await user.click(screen.getByText("switch"));
    await screen.findByTestId("reply-panel").catch(() => undefined);
    await waitFor(() => expect(screen.queryByText("Case not found.")).toBeNull());
  });
});
