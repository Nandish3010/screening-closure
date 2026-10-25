import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../src/app/AppContext";
import { createMockAdapter } from "../src/api/mockAdapter";
import App from "../src/App";
import { TOUR_STEPS } from "../src/judge/JudgeTour";
import { DOCS, REPO_BLOB, docHref } from "../src/judge/links";

describe("Judge mode (C13)", () => {
  it("the tour has the seven steps of the story, in order", () => {
    expect(TOUR_STEPS.map((s) => s.id)).toEqual(["problem", "timeline", "guard", "agency", "effort", "numbers", "summary"]);
  });

  it("document links point at the repository and name the repo path", () => {
    expect(docHref(DOCS.results)).toBe(`${REPO_BLOB}docs/results/results.md`);
    for (const d of Object.values(DOCS)) expect(d.path).toMatch(/^docs\/.+\.md$/);
  });

  it("the judge page states the positioning, the caveats and make check, and the header button opens the tour", async () => {
    render(
      <MemoryRouter initialEntries={["/judge"]}>
        <AppProvider api={createMockAdapter(null)} initialRole="coordinator"><App /></AppProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("judge-positioning")).toHaveTextContent("clinician-signed closure");
    expect(screen.getByTestId("judge-caveats")).toHaveTextContent("synthetic");
    expect(screen.getByTestId("judge-make-check")).toHaveTextContent("make check");
    fireEvent.click(screen.getByTestId("judge-tour-button"));
    expect(await screen.findByTestId("judge-tour")).toHaveTextContent("Step 1 of 7");
    fireEvent.click(screen.getByTestId("tour-next"));
    expect(screen.getByTestId("tour-progress")).toHaveTextContent("Step 2 of 7");
    fireEvent.click(screen.getByTestId("tour-back"));
    fireEvent.keyDown(screen.getByTestId("judge-tour"), { key: "Escape" });
    expect(screen.queryByTestId("judge-tour")).toBeNull();
  });
});
