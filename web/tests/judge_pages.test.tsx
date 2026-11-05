import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../src/app/AppContext";
import { createMockAdapter } from "../src/api/mockAdapter";
import type { Role } from "../src/api/contract";
import App from "../src/App";
import safety from "../src/judge/safetyData.json";
import { PILOT } from "../src/lib/pilotValue";

const ROOT = resolve(__dirname, "../..");
const json = (rel: string) => JSON.parse(readFileSync(resolve(ROOT, rel), "utf8"));

function open(path: string, role: Role = "coordinator") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppProvider api={createMockAdapter(null)} initialRole={role}><App /></AppProvider>
    </MemoryRouter>,
  );
}
const pct = (x: number, d = 1) => `${(x * 100).toFixed(d)}%`;

describe("AI and safety page", () => {
  it("shows the three kinds of decision with an example each, and the four human guards", async () => {
    open("/safety");
    const kinds = await screen.findByTestId("safety-kinds");
    for (const id of ["rule", "calc", "ai"]) {
      const card = within(kinds).getByTestId(`kind-${id}`);
      expect(within(card).getAllByRole("listitem").length).toBeGreaterThanOrEqual(2);
    }
    const guards = screen.getByTestId("safety-guards");
    for (const g of ["closure guard", "Clinician signature", "Default-deny", "consent hold"]) expect(guards).toHaveTextContent(g);
  });

  it("every evaluation figure on the page is the one in the committed results files", async () => {
    const mlx = json("docs/results/triage/eval.json");
    const gg = json("docs/results/triage/eval-gguf-q8_0.json");
    const man = json("tools/triage/dataset/manifest.json");
    open("/safety");
    const head = await screen.findByTestId("safety-headline");
    expect(head).toHaveTextContent(pct(mlx.accuracy));
    expect(head).toHaveTextContent(pct(gg.accuracy));
    expect(head).toHaveTextContent(pct(mlx.auto_precision));
    expect(head).toHaveTextContent(`${pct(mlx.auto_precision_ci[0])} to ${pct(mlx.auto_precision_ci[1])}`);
    expect(head).toHaveTextContent(pct(mlx.coverage));
    expect(head).toHaveTextContent(`${gg.opt_out_leaked} of ${gg.opt_out_total}`);
    expect(screen.getByTestId("safety-models")).toHaveTextContent(gg.model_id);
    expect(screen.getByTestId("safety-models")).toHaveTextContent("Q8_0");

    const perClass = screen.getByTestId("safety-n-per-class");
    for (const [label, n] of Object.entries(man.counts.test.by_label)) {
      const row = within(perClass).getByRole("row", { name: new RegExp(`^${label.replace(/_/g, " ")}\\b`) });
      expect(row).toHaveTextContent(String(n));
    }
    expect(perClass).toHaveTextContent(String(man.counts.test.total));

    const byLang = screen.getByTestId("safety-by-language");
    const withRule = gg.groups.with_rule;
    for (const [name, key] of [["Arabizi", "arabizi"], ["English", "en"], ["Mixed script", "mixed"], ["Arabic", "ar"]] as const) {
      const row = within(byLang).getAllByRole("row").find((r) => within(r).queryByRole("rowheader", { name })!);
      expect(row, name).toBeTruthy();
      expect(row).toHaveTextContent(pct(withRule.by_language[key].accuracy));
      expect(row).toHaveTextContent(String(withRule.by_language[key].n));
    }
    const gulf = within(byLang).getAllByRole("row").find((r) => within(r).queryByRole("rowheader", { name: "Arabic, Gulf dialect" }))!;
    expect(gulf).toHaveTextContent(pct(withRule.by_dialect.ar_gulf.accuracy));
    expect(gulf).toHaveTextContent("always a person");
    expect(screen.getByTestId("safety-dialect-rule")).toHaveTextContent(gg.groups.arabizi_threshold_in_force.toFixed(3));
  });

  it("labels the evaluation synthetic, explains how labels were made, and flags the fairness gaps", async () => {
    open("/safety");
    expect(await screen.findByTestId("safety-synthetic")).toHaveTextContent(/synthetic/i);
    expect(screen.getByTestId("safety-labels")).toHaveTextContent(/label is the template's intent/);
    const fair = json("docs/results/fairness.json");
    for (const f of fair.flags.filter((x: { area: string }) => x.area === "triage")) {
      expect(screen.getByTestId("safety-fairness")).toHaveTextContent(pct(f.value));
    }
    expect(screen.getAllByText("Synthetic data").length).toBeGreaterThan(0);
  });

  it("links the model cards, the hazard log and the DPIA in the repository", async () => {
    open("/safety");
    const docs = await screen.findByTestId("safety-docs");
    const hrefs = within(docs).getAllByRole("link").map((a) => a.getAttribute("href"));
    for (const path of ["model-cards/reply-triage.md", "model-cards/claude-drafting.md", "hazard-log.md", "dpia.md", "triage/eval-gguf-q8_0.md"]) {
      expect(hrefs.some((h) => h?.endsWith(path) && h.startsWith("https://github.com/Nandish3010/screening-closure/blob/main/"))).toBe(true);
    }
    for (const h of hrefs) expect(readFileSync(resolve(ROOT, h!.replace("https://github.com/Nandish3010/screening-closure/blob/main/", "")))).toBeTruthy();
  });

  it("its data file is the one the generator would write (shape and a few values)", () => {
    expect(safety.synthetic).toBe(true);
    expect(safety.hosted.quantization).toBe("Q8_0");
    expect(safety.dataset.n.test).toBe(Object.values(safety.dataset.test_by_label).reduce((a, b) => a + b, 0));
  });
});

describe("Integrations page", () => {
  it("says plainly that nothing is connected, and lists what is built and what a pilot needs", async () => {
    open("/integrations");
    expect(await screen.findByTestId("integrations-honesty")).toHaveTextContent(/Not connected/);
    expect(screen.getByRole("heading", { name: /Malaffi: results in/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Sahatna and the patient/ })).toBeInTheDocument();
    expect(screen.getByText(/mutual TLS|client certificate/i, { selector: "td" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "FHIR evidence lookup" })).toBeInTheDocument();
    const pilot = screen.getByTestId("integrations-pilot-work");
    expect(pilot).toHaveTextContent(/There is no connection to Sahatna/);
    expect(pilot).toHaveTextContent(/shape we made up/);
    expect(pilot).toHaveTextContent(/No real message has ever been sent/);
  });

  it("shows the live log marked as a simulated feed with synthetic data, newest first, and refreshes", async () => {
    open("/integrations");
    const list = await screen.findByTestId("feed-list");
    expect(screen.getByTestId("feed-simulated")).toHaveTextContent("Simulated feed");
    expect(screen.getByTestId("integration-feed")).toHaveTextContent("Synthetic data");
    const items = within(list).getAllByRole("listitem");
    expect(items.length).toBeGreaterThan(0);
    expect(items.length).toBeLessThanOrEqual(10);
    expect(list.textContent).toMatch(/HL7/);
    expect(list.textContent).toMatch(/FHIR/);
    expect(list.textContent).toMatch(/Message/);
    fireEvent.change(screen.getByLabelText("Show the last"), { target: { value: "5" } });
    await waitFor(() => expect(within(screen.getByTestId("feed-list")).getAllByRole("listitem")).toHaveLength(5));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByTestId("feed-list")).toBeInTheDocument();
  });

  it("does not show the feed to a role that may not read it", async () => {
    open("/integrations", "owner");
    expect(await screen.findByTestId("feed-unavailable")).toHaveTextContent(/Coordinators and supervisors/);
    expect(screen.queryByTestId("feed-list")).toBeNull();
  });
});

describe("Pilot value page", () => {
  it("opens on the repository's documented scenario with the numbers the cost model gives", async () => {
    open("/pilot-value");
    expect(await screen.findByTestId("pilot-illustrative")).toHaveTextContent(/Illustrative, synthetic/);
    expect(screen.getByTestId("pilot-shadow-total")).toHaveTextContent("AED 105,828");
    expect(screen.getByLabelText("Annual positive results")).toHaveValue(Math.round(PILOT.defaults.annual_positives));
    expect(screen.getByLabelText("Planned pilot cases")).toHaveValue(Math.round(PILOT.defaults.pilot_cases));
    expect(screen.getByLabelText("Staff cost per hour (AED)")).toHaveValue(PILOT.defaults.staff_cost_per_hour);
    expect(screen.getByLabelText("Current follow-up completion (%)")).toHaveValue(Number((PILOT.defaults.current_completion * 100).toFixed(1)));
    const table = screen.getByTestId("pilot-shadow-table");
    for (const item of ["Hosting", "Third-party licences", "Support", "Integration", "Governance review", "Total"]) expect(table).toHaveTextContent(item);
  });

  it("never claims the 40% target is met, and shows clinician minutes rising", async () => {
    open("/pilot-value");
    const target = await screen.findByTestId("pilot-target");
    expect(target).toHaveTextContent(/target is not met/);
    expect(target).not.toHaveTextContent(/target (is|was) met/i);
    const minutes = screen.getByTestId("pilot-minutes");
    expect(within(minutes).getByRole("row", { name: /Owner clinician/ })).toHaveTextContent(/more/);
    expect(within(minutes).getByRole("row", { name: /Coordinator/ })).toHaveTextContent(/fewer/);
  });

  it("recomputes when the inputs change, rejects bad input and resets", async () => {
    open("/pilot-value");
    const extra = await screen.findByTestId("pilot-extra");
    const before = extra.textContent;
    fireEvent.change(screen.getByLabelText("Annual positive results"), { target: { value: "1000" } });
    expect(screen.getByTestId("pilot-extra").textContent).not.toBe(before);
    const ratio = PILOT.completion.ratio_360d;
    const expected = Math.round(1000 * (Math.min(1, PILOT.defaults.current_completion * ratio) - PILOT.defaults.current_completion));
    expect(screen.getByTestId("pilot-extra")).toHaveTextContent(String(expected));
    fireEvent.change(screen.getByLabelText("Planned pilot cases"), { target: { value: "60" } });
    expect(screen.getByTestId("pilot-shadow-total")).not.toHaveTextContent("105,828");
    fireEvent.change(screen.getByLabelText("Staff cost per hour (AED)"), { target: { value: "-4" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/Staff cost per hour must be a number/);
    expect(screen.getByLabelText("Staff cost per hour (AED)")).toHaveAttribute("aria-invalid", "true");
    fireEvent.click(screen.getByRole("button", { name: /Reset to the documented scenario/ }));
    expect(screen.getByTestId("pilot-shadow-total")).toHaveTextContent("AED 105,828");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the assumptions beside the numbers and the comparison without naming products", async () => {
    open("/pilot-value");
    const aside = await screen.findByTestId("pilot-assumptions");
    expect(aside).toHaveTextContent(/assumed/);
    expect(aside).toHaveTextContent("shadow integration one off aed per provider");
    const cmp = screen.getByTestId("pilot-compare");
    for (const row of ["Manual tracking", "Worklists in an electronic health record", "Generic reminders"]) expect(cmp).toHaveTextContent(row);
    expect(cmp).toHaveTextContent(/no products are named/);
  });
});

describe("navigation and the judge page", () => {
  it("the header links to all three pages, and the judge page links to them", async () => {
    open("/judge");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const [name, href] of [["AI and safety", "/safety"], ["Integrations", "/integrations"], ["Pilot value", "/pilot-value"]]) {
      expect(within(nav).getByRole("link", { name })).toHaveAttribute("href", href);
    }
    const more = screen.getByTestId("judge-more-pages");
    expect(within(more).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/safety", "/integrations", "/pilot-value"]);
  });

  it("each page has one h1, labelled sections and phone-safe tables (scrollable wrappers with captions)", async () => {
    for (const path of ["/safety", "/integrations", "/pilot-value"]) {
      const { container, unmount } = open(path);
      await screen.findByRole("heading", { level: 1 });
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      for (const t of container.querySelectorAll("table")) {
        expect(t.querySelector("caption"), `${path} table caption`).not.toBeNull();
        expect(t.parentElement?.classList.contains("jp-tablewrap")).toBe(true);
      }
      for (const s of container.querySelectorAll("section")) expect(s.getAttribute("aria-labelledby"), path).toBeTruthy();
      unmount();
    }
  });

  it("the page sources hold no hard-coded result figures (no decimals, no percentages with a decimal point)", () => {
    for (const f of ["SafetyPage", "IntegrationsPage", "PilotValuePage"]) {
      const src = readFileSync(resolve(__dirname, `../src/pages/${f}.tsx`), "utf8").replace(/^import .*$/gm, "");
      expect(src.match(/(?<![\w.])\d+\.\d+/g), f).toBeNull();
    }
  });
});
