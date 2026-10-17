import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DEMO_OVERDUE_CASE_ID } from "../src/api/fixtures";
import { STRINGS } from "../src/i18n/strings";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

describe("Coordinator worklist", () => {
  it("shows the most overdue case first and filters by overdue", async () => {
    const user = userEvent.setup();
    renderWithApp(<CoordinatorPage />);
    const rows = await screen.findAllByTestId("worklist-row");
    expect(within(rows[0]).getByRole("link")).toHaveAttribute("href", `/coordinator/${DEMO_OVERDUE_CASE_ID}`);
    const total = rows.length;
    await user.click(screen.getByLabelText("Overdue only"));
    const overdueRows = screen.getAllByTestId("worklist-row");
    expect(overdueRows.length).toBeLessThan(total);
    overdueRows.forEach((r) => expect(r).toHaveClass("row-overdue"));
  });
});

describe("Case detail actions", () => {
  it("escalate, request draft and approve each post through the client and show provenance", async () => {
    const user = userEvent.setup();
    const { api } = renderWithApp(<CaseDetailPage />, { route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("escalate");
    await user.click(screen.getByTestId("escalate"));
    await waitFor(() => expect(screen.getByTestId("flash")).toHaveTextContent(/escalated/i));
    await user.click(screen.getByTestId("request-draft"));
    const draftItem = await screen.findByTestId(/^draft-d-mock/);
    expect(draftItem).toHaveTextContent(/AI draft/);
    expect(draftItem).toHaveTextContent(/template fallback/);
    expect(within(draftItem).getByText(/تنظير القولون/).closest("p")).toHaveAttribute("dir", "rtl");
    await user.click(within(draftItem).getByRole("button", { name: /approve draft/i }));
    await waitFor(() => expect(screen.getByTestId(/^approved-d-mock/)).toHaveTextContent(/Approved by Maryam/));
    const types = (await api.getCase(DEMO_OVERDUE_CASE_ID)).events.slice(-3).map((e) => e.type);
    expect(types).toEqual(["escalate", "draft_generated", "draft_approved"]);
  });

  it("shows hand-off rejection reasons", async () => {
    renderWithApp(<CaseDetailPage />, { route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("rejections")).toHaveTextContent(/No colonoscopy slots/);
  });

  it("explains why an action is disabled for the wrong role", async () => {
    renderWithApp(<CaseDetailPage />, { role: "specialist", route: `/coordinator/${DEMO_OVERDUE_CASE_ID}`, path: "/coordinator/:caseId" });
    const btn = await screen.findByTestId("escalate");
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("escalate-why")).toHaveTextContent(/specialist.*cannot/i);
  });
});

describe("i18n completeness", () => {
  it("Arabic mirrors every English key and step count", () => {
    expect(Object.keys(STRINGS.ar).sort()).toEqual(Object.keys(STRINGS.en).sort());
    expect(STRINGS.ar.nextSteps).toHaveLength(STRINGS.en.nextSteps.length);
    expect(STRINGS.ar.progress).toHaveLength(STRINGS.en.progress.length);
    expect(STRINGS.ar.resultBody).toMatch(/[؀-ۿ]/);
  });
});
