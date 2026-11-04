/** Hosted judge demo (deploy/gcp): `GET /health` -> `demo.simulated_clock` puts "Demo: simulated time" in the header; nothing otherwise. */
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ApiClient } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { Header } from "../src/components/Header";
import { renderWithApp } from "./helpers";

const withHealth = (api: ApiClient, demo: { simulated_clock: boolean } | undefined): ApiClient =>
  ({ ...api, health: async () => ({ status: "ok", sim_time: "2026-01-05T08:00:00Z", ...(demo ? { demo } : {}) }) }) as ApiClient;

describe("the simulated-time banner", () => {
  it("is shown, with the simulated date, while the server reports the demo clock", async () => {
    renderWithApp(<Header />, { api: withHealth(createMockAdapter(null), { simulated_clock: true }), role: "supervisor" });
    const banner = await screen.findByTestId("demo-clock-banner");
    expect(banner.textContent).toContain("Demo: simulated time");
    expect(banner.title).toMatch(/synthetic data/);
  });

  it("is absent when the server does not report it", async () => {
    renderWithApp(<Header />, { api: withHealth(createMockAdapter(null), undefined), role: "supervisor" });
    await screen.findByText(/Role|Sign out/);
    await waitFor(() => expect(screen.queryByTestId("demo-clock-banner")).toBeNull());
  });
});
