import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMockAdapter } from "../src/api/mockAdapter";
import { ApiError } from "../src/api/contract";
import { ALERT_POLL_MS, DashboardPage } from "../src/pages/DashboardPage";
import { renderWithApp } from "./helpers";

const cid = (n: number) => `c${String(n).padStart(7, "0")}-0000-4000-8000-${String(n).padStart(12, "0")}`;

afterEach(() => vi.useRealTimers());

describe("C9: cases nobody is looking at", () => {
  it("lists the watchdog's flagged cases with the red marker, the idle time and a link to the case", async () => {
    renderWithApp(<DashboardPage />);
    const rows = await screen.findAllByTestId("nobody-row");
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getByRole("link")).toHaveAttribute("href", `/coordinator/${cid(13)}`);
    expect(within(rows[0]).getByTestId("watchdog-marker")).toHaveTextContent("Nobody is looking at this");
    expect(rows[0]).toHaveTextContent(/11 days/);
    expect(rows[0]).toHaveTextContent(/Omar Khalid/);
  });

  it("asks the API for flagged=true only, and drops a case once a human acts on it", async () => {
    const api = createMockAdapter(null);
    const list = vi.spyOn(api, "listCases");
    await api.postEvent(cid(13), { type: "mark_lost_contact", actor: "coordinator:m", payload: {} });
    renderWithApp(<DashboardPage />, { api });
    expect(await screen.findByTestId("nobody-empty")).toHaveTextContent(/no flagged cases/i);
    expect(list).toHaveBeenCalledWith({ flagged: true });
  });

  it("says so when the list cannot be loaded, and still shows the rest of the dashboard", async () => {
    const api = { ...createMockAdapter(null), listCases: async () => { throw new ApiError(500, "boom"); } };
    renderWithApp(<DashboardPage />, { api });
    expect(await screen.findByText(/Could not load flagged cases: boom/)).toBeInTheDocument();
    expect(await screen.findByTestId("kpi-p50")).toBeInTheDocument();
  });
});

describe("C9: stale-scheduler banner", () => {
  it("is absent while GET /alerts is empty and appears for an active scheduler_down alert", async () => {
    const user = userEvent.setup();
    renderWithApp(<DashboardPage />);
    await screen.findByTestId("nobody-panel");
    expect(screen.queryByTestId("scheduler-banner")).toBeNull();
    await user.click(screen.getByTestId("toggle-scheduler"));
    const banner = await screen.findByTestId("scheduler-banner");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent(/scheduler is down/i);
    expect(banner).toHaveTextContent(/not reported since 2026-11-04/);
    expect(screen.getByTestId("toggle-scheduler")).toHaveTextContent(/back up/i);
  });

  it("shows a cleared alert as recovered, not as a banner", async () => {
    const api = createMockAdapter(null);
    await api.setSchedulerDown!(true);
    await api.setSchedulerDown!(false);
    renderWithApp(<DashboardPage />, { api });
    expect(await screen.findByTestId("scheduler-recovered")).toHaveTextContent(/running again/i);
    expect(screen.queryByTestId("scheduler-banner")).toBeNull();
  });

  it("re-reads the alerts on a timer, so the banner appears without a reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = createMockAdapter(null);
    renderWithApp(<DashboardPage />, { api });
    await screen.findByTestId("nobody-panel");
    expect(screen.queryByTestId("scheduler-banner")).toBeNull();
    await api.setSchedulerDown!(true); // the scheduler stops (from outside this page)
    expect(ALERT_POLL_MS).toBeLessThanOrEqual(15_000); // well inside the 60 s of T-C16
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ALERT_POLL_MS + 100);
    });
    await waitFor(() => expect(screen.getByTestId("scheduler-banner")).toBeInTheDocument());
  });

  it("an unreachable alert feed does not break the dashboard", async () => {
    const api = { ...createMockAdapter(null), listAlerts: async () => { throw new ApiError(500, "down"); } };
    renderWithApp(<DashboardPage />, { api });
    expect(await screen.findByTestId("kpi-p50")).toBeInTheDocument();
    expect(screen.queryByTestId("scheduler-banner")).toBeNull();
  });
});
