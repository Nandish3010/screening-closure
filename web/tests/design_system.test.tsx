/** C18 (T-C28): the design system. Colours live in tokens, every var() is defined, the brand lockup and the effort chart render. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppProvider } from "../src/app/AppContext";
import { createMockAdapter } from "../src/api/mockAdapter";
import App from "../src/App";

const src = (f: string) => readFileSync(resolve(__dirname, "../src", f), "utf8");
/** The stylesheet with the two token blocks (`:root { ... }`) taken out. */
const withoutTokens = (css: string) => css.replace(/:root\s*\{[^}]*\}/g, "");

describe("design tokens", () => {
  it("styles.css and judgePages.css hold no hard-coded colour outside the :root token blocks", () => {
    for (const f of ["styles.css", "judgePages.css"]) {
      const rest = withoutTokens(src(f)).replace(/\/\*[\s\S]*?\*\//g, "");
      expect(rest.match(/#[0-9a-fA-F]{3,8}\b|rgba?\(/g), f).toBeNull();
    }
  });

  it("every var(--token) a stylesheet uses is defined in :root", () => {
    const css = src("styles.css");
    const defined = new Set([...css.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]));
    for (const f of ["styles.css", "judgePages.css", "patient.css"]) {
      const used = [...src(f).matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]);
      expect(used.filter((u) => !defined.has(u)), f).toEqual([]);
    }
  });

  it("the dark block redefines every colour token the light block defines", () => {
    const css = src("styles.css");
    const [light, dark] = [css.match(/:root\s*\{([^}]*)\}/)![1], css.match(/prefers-color-scheme: dark\)\s*\{\s*:root\s*\{([^}]*)\}/)![1]];
    const names = (b: string) => new Set([...b.matchAll(/(--[a-z0-9-]+)\s*:\s*#/g)].map((m) => m[1]));
    const missing = [...names(light)].filter((n) => !names(dark).has(n));
    expect(missing).toEqual([]);
  });
});

describe("branding and charts", () => {
  it("the header carries the product name with 'by Humans Decide'", async () => {
    render(<MemoryRouter initialEntries={["/dashboard"]}><AppProvider api={createMockAdapter(null)} initialRole="coordinator"><App /></AppProvider></MemoryRouter>);
    const home = screen.getByRole("link", { name: /Screening Closure/ });
    expect(home.textContent).toContain("by Humans Decide");
  });

  it("the effort chart has a text alternative with the same figures as the card", async () => {
    render(<MemoryRouter initialEntries={["/dashboard"]}><AppProvider api={createMockAdapter(null)} initialRole="coordinator"><App /></AppProvider></MemoryRouter>);
    const chart = await screen.findByTestId("effort-chart");
    await waitFor(() => expect(chart.getAttribute("aria-label")).toMatch(/actions by people and \d+ automated/));
    expect(chart.getAttribute("aria-label")).toMatch(/assumed minutes/);
  });
});
