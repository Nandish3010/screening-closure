import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createMockAdapter } from "../src/api/mockAdapter";
import { caseIdFor, pathwayOfCase, seedEvents } from "../src/api/fixtures";
import { PathwayBadge } from "../src/components/PathwayBadge";
import { DEFAULT_PATHWAY, MILESTONES, milestonesFor, pathwayMeta } from "../src/lib/pathway";
import { DEFAULT_FILTERS, filterWorklist, pathwaysOf, worklist } from "../src/lib/worklist";
import { CaseDetailPage } from "../src/pages/CaseDetailPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

/** The second pathway (cervical screening, `cervical_hpv`) on the same engine: the web shows which pathway a case runs on and works for both. */

const CERVICAL = [30, 31, 32, 33].map(caseIdFor);
const twoPathways = () => createMockAdapter(null, { automation: true, secondPathway: true });

describe("pathway metadata", () => {
  it("knows both shipped pathways, with the confirmatory procedure each one uses", () => {
    expect(pathwayMeta("colorectal_fit")).toMatchObject({ label: "Bowel screening", procedure: "colonoscopy", placeholderTimings: false });
    expect(pathwayMeta("cervical_hpv")).toMatchObject({ label: "Cervical screening", procedure: "colposcopy", placeholderTimings: true });
  });
  it("treats a missing pathway as the default one and shows an unknown name as-is, never hides it", () => {
    expect(pathwayMeta(undefined).name).toBe(DEFAULT_PATHWAY);
    expect(pathwayMeta("").name).toBe(DEFAULT_PATHWAY);
    expect(pathwayMeta("breast_mammogram").label).toBe("Breast mammogram");
  });
  it("words the milestone clocks for the pathway and marks the unsigned timing a placeholder", () => {
    expect(milestonesFor("colorectal_fit")).toBe(MILESTONES);
    expect(milestonesFor(undefined)).toBe(MILESTONES);
    const cervical = milestonesFor("cervical_hpv");
    expect(cervical.map((m) => m.name)).toEqual(MILESTONES.map((m) => m.name));
    const colposcopy = cervical.find((m) => m.name === "confirmatory_completed")!;
    expect(colposcopy.label).toBe("Colposcopy within 31 working days of referral (placeholder, H1)");
    expect(colposcopy.source).toMatch(/^Placeholder, needs clinician sign-off \(H1\)/);
    expect(colposcopy.working_days).toBe(31);
    expect(cervical.find((m) => m.name === "referral_requested")!.source).toMatch(/IFHAS.*to be confirmed by H1/);
    // the bowel definitions are untouched
    expect(MILESTONES.find((m) => m.name === "confirmatory_completed")!.label).toBe("Colonoscopy within 31 working days of referral");
  });
});

describe("PathwayBadge", () => {
  it("renders the label, carries the pathway name, and says the timings are placeholders where they are", () => {
    const { rerender } = renderWithApp(<PathwayBadge pathway="cervical_hpv" />);
    const badge = screen.getByTestId("pathway-badge");
    expect(badge).toHaveTextContent("Cervical screening");
    expect(badge).toHaveAttribute("data-pathway", "cervical_hpv");
    expect(badge.getAttribute("title")).toMatch(/placeholders pending clinician sign-off \(H1\)/);
    rerender(<PathwayBadge pathway="colorectal_fit" />);
    expect(screen.getByTestId("pathway-badge")).toHaveTextContent("Bowel screening");
    expect(screen.getByTestId("pathway-badge").getAttribute("title")).not.toMatch(/placeholder/);
  });
});

describe("the mock seed", () => {
  it("has no cervical case unless asked: every other seed, test and recording is unchanged", () => {
    for (const automation of [false, true]) {
      const ids = Object.keys(seedEvents(automation));
      expect(ids.some((id) => pathwayOfCase(id) === "cervical_hpv")).toBe(false);
    }
    expect(Object.keys(seedEvents(true, true)).filter((id) => pathwayOfCase(id) === "cervical_hpv")).toEqual(CERVICAL);
  });
  it("lists the cervical cases with their pathway, and scores none of them (the risk model is the bowel pathway's)", async () => {
    const api = twoPathways();
    const rows = await api.listCases();
    const cervical = rows.filter((r) => r.pathway === "cervical_hpv");
    expect(cervical.map((r) => r.case_id).sort()).toEqual([...CERVICAL].sort());
    expect(cervical.every((r) => r.risk_score === undefined)).toBe(true);
    expect(rows.filter((r) => r.pathway === "colorectal_fit").every((r) => typeof r.risk_score === "number")).toBe(true);
  });
});

describe("worklist filter by pathway", () => {
  it("offers the pathways present, default first, and filters on the pathway name", async () => {
    const rows = await twoPathways().listCases();
    expect(pathwaysOf(rows)).toEqual(["colorectal_fit", "cervical_hpv"]);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, pathway: "cervical_hpv" }).every((r) => r.pathway === "cervical_hpv")).toBe(true);
    expect(filterWorklist(rows, { ...DEFAULT_FILTERS, pathway: "colorectal_fit" }).some((r) => r.pathway === "cervical_hpv")).toBe(false);
    expect(filterWorklist(rows, DEFAULT_FILTERS).length).toBe(filterWorklist(rows, { ...DEFAULT_FILTERS, pathway: "" }).length);
    // a row with no pathway field counts as the default pathway
    const bare = { ...rows[0], pathway: "" };
    expect(filterWorklist([bare], { ...DEFAULT_FILTERS, pathway: "colorectal_fit" })).toHaveLength(1);
    // sorting is not by pathway: the most overdue case of either pathway comes first
    const sorted = worklist(rows, DEFAULT_FILTERS, "2026-11-04T09:00:00.000Z");
    expect(sorted[0].overdue).toBe(true);
  });
});

describe("worklist and case page with both pathways", () => {
  it("shows a pathway badge on every worklist row, for both pathways", async () => {
    renderWithApp(<CoordinatorPage />, { api: twoPathways() });
    const rows = await screen.findAllByTestId("worklist-row");
    const badges = rows.map((r) => within(r).getByTestId("worklist-pathway"));
    expect(badges.length).toBe(rows.length);
    expect(new Set(badges.map((b) => b.getAttribute("data-pathway")))).toEqual(new Set(["colorectal_fit", "cervical_hpv"]));
    expect(screen.getByRole("columnheader", { name: "Pathway" })).toBeInTheDocument();
  });
  it("filters the worklist to one pathway and back", async () => {
    const user = userEvent.setup();
    renderWithApp(<CoordinatorPage />, { api: twoPathways() });
    await screen.findAllByTestId("worklist-row");
    await user.selectOptions(screen.getByLabelText("Pathway"), "cervical_hpv");
    const rows = screen.getAllByTestId("worklist-row");
    expect(rows).toHaveLength(CERVICAL.length);
    rows.forEach((r) => expect(within(r).getByTestId("worklist-pathway")).toHaveAttribute("data-pathway", "cervical_hpv"));
    await user.selectOptions(screen.getByLabelText("Pathway"), "");
    expect(screen.getAllByTestId("worklist-row").length).toBeGreaterThan(CERVICAL.length);
  });
  it("offers no pathway filter while only one pathway is on the list", async () => {
    renderWithApp(<CoordinatorPage />);
    await screen.findAllByTestId("worklist-row");
    expect(screen.queryByLabelText("Pathway")).toBeNull();
  });
  it("shows the pathway and the colposcopy clocks on a cervical case page, with the timing marked a placeholder", async () => {
    const id = caseIdFor(33);
    renderWithApp(<CaseDetailPage />, { api: twoPathways(), route: `/coordinator/${id}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("pathway-badge")).toHaveAttribute("data-pathway", "cervical_hpv");
    expect(screen.getByText("Referral within 15 working days of result")).toBeInTheDocument();
    expect(screen.getByText("Colposcopy within 31 working days of referral (placeholder, H1)")).toBeInTheDocument();
    expect(screen.queryByText("Colonoscopy within 31 working days of referral")).toBeNull();
    expect(screen.getByTestId("milestone-confirmatory_completed")).toBeInTheDocument();
  });
  it("shows the bowel pathway's badge and colonoscopy clock on a bowel case page, as before", async () => {
    renderWithApp(<CaseDetailPage />, { api: twoPathways(), route: `/coordinator/${caseIdFor(9)}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("pathway-badge")).toHaveAttribute("data-pathway", "colorectal_fit");
    expect(screen.getByText("Colonoscopy within 31 working days of referral")).toBeInTheDocument();
  });
  it("words the booked-case action for the pathway's confirmatory procedure", async () => {
    // case 33 is booked and owned by Dr. Huda Rahman: the owner sees "Record colposcopy result"
    renderWithApp(<CaseDetailPage />, { api: twoPathways(), role: "clinician", route: `/coordinator/${caseIdFor(33)}`, path: "/coordinator/:caseId" });
    expect(await screen.findByTestId("pathway-badge")).toHaveAttribute("data-pathway", "cervical_hpv");
    await waitFor(() => expect(screen.getByTestId("record-colonoscopy")).toHaveTextContent("Record colposcopy result"));
    expect(screen.getByPlaceholderText("Procedure/colposcopy-0001")).toBeInTheDocument();
  });
  it("closure still needs evidence and a signature on a cervical case: the sign-off panel is the same guard", async () => {
    // case 33 is not reviewed yet; the engine's closure rule is not pathway-specific, so the panel is not offered before the
    // confirmatory step is complete, exactly as for a bowel case
    renderWithApp(<CaseDetailPage />, { api: twoPathways(), route: `/coordinator/${caseIdFor(33)}`, path: "/coordinator/:caseId" });
    await screen.findByTestId("pathway-badge");
    expect(screen.queryByTestId("signoff-panel")).toBeNull();
  });
});
