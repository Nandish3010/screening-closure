/**
 * Phone header (<= 480px): the prototype / demo notices collapse to one line with a "More" toggle and the role switch is one
 * compact select. jsdom does not apply the media query, so these tests pin the markup the CSS relies on: the full wording is
 * always in the page (screen readers), the compact line is aria-hidden, and nothing is removed.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ApiClient } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { Header } from "../src/components/Header";
import { PrototypeStrip } from "../src/components/DemoBanner";
import { OutreachDemoBanner } from "../src/components/OutreachDemoBanner";
import { renderWithApp } from "./helpers";

const withHealth = (api: ApiClient, demo: boolean): ApiClient =>
  ({ ...api, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", ...(demo ? { demo: { simulated_clock: true } } : {}) }) }) as ApiClient;

describe("phone header", () => {
  it("one strip carries the prototype notice and, on the demo clock, the synthetic-data / simulated-time notice", async () => {
    renderWithApp(<PrototypeStrip />, { api: withHealth(createMockAdapter(null), true) });
    const demo = await screen.findByTestId("proto-strip-demo");
    expect(demo.textContent).toMatch(/synthetic data, simulated time/);
    const full = screen.getByTestId("proto-strip-full");
    expect(full.textContent).toMatch(/Not an official government service/);
    expect(full.textContent).toMatch(/Synthetic data only/);
    const compact = screen.getByTestId("proto-strip-compact");
    expect(compact.getAttribute("aria-hidden")).toBe("true");
    expect(compact.textContent).toMatch(/synthetic data, simulated time/i);
  });

  it("the compact line says synthetic data without the demo clock, and More toggles with aria-expanded", async () => {
    const user = userEvent.setup();
    renderWithApp(<PrototypeStrip />, { api: withHealth(createMockAdapter(null), false) });
    expect(screen.getByTestId("proto-strip-compact").textContent).toMatch(/Synthetic data/);
    expect(screen.queryByTestId("proto-strip-demo")).toBeNull();
    const more = screen.getByTestId("proto-strip-more");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    await user.click(more);
    expect(more.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("proto-strip").className).toContain("is-open");
  });

  it("the header's own demo banner is kept in the page (merged visually on a phone, never removed)", async () => {
    renderWithApp(<Header />, { api: withHealth(createMockAdapter(null), true), role: "supervisor" });
    const banner = await screen.findByTestId("demo-clock-banner");
    expect(banner.className).toContain("demo-strip-merged");
    expect(banner.textContent).toContain("Demo, synthetic data, simulated time");
  });

  it("the role switch is a single labelled select with a short and a full label", async () => {
    renderWithApp(<Header />, { api: withHealth(createMockAdapter(null), false), role: "coordinator" });
    const select = await screen.findByRole("combobox", { name: "Switch role" });
    expect(select.tagName).toBe("SELECT");
    const label = select.closest("label")!;
    expect(label.querySelector(".rs-long")!.textContent).toMatch(/Role, view as \(demo only\)/);
    expect(label.querySelector(".rs-short")!.textContent).toMatch(/View as/);
  });

  it("the demo-placeholder banner keeps the API's text and gets a compact line with a toggle", async () => {
    const user = userEvent.setup();
    renderWithApp(<OutreachDemoBanner />, { api: createMockAdapter(null, { automation: true }) });
    const banner = await screen.findByTestId("outreach-demo-banner");
    expect(screen.getByTestId("outreach-demo-text").textContent).toBe("DEMO PLACEHOLDER (clinical sign-off pending)");
    expect(banner.className).toContain("banner-warn");
    const more = screen.getByTestId("outreach-banner-more");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    await user.click(more);
    await waitFor(() => expect(more.getAttribute("aria-expanded")).toBe("true"));
  });
});
