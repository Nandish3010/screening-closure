import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createMockAdapter } from "../src/api/mockAdapter";
import { Header } from "../src/components/Header";
import { renderWithApp } from "./helpers";

describe("role switch logs in", () => {
  it("calls api.login(role, name) at start-up and on every role switch in the header", async () => {
    const user = userEvent.setup();
    const login = vi.fn(async () => undefined);
    const api = { ...createMockAdapter(null), login };
    renderWithApp(<Header />, { api, role: "coordinator" });
    await waitFor(() => expect(login).toHaveBeenCalledWith("coordinator", "Maryam Al Suwaidi", undefined));
    await user.selectOptions(screen.getByLabelText("Switch role"), "clinician");
    // Clinician logins send the provider they act for (A5), so they can answer handoffs addressed to it.
    await waitFor(() => expect(login).toHaveBeenLastCalledWith("clinician", "Dr. Huda Rahman", "Marina Digestive Health"));
    expect(login).toHaveBeenCalledTimes(2);
  });

  it("a failing login does not break the app", async () => {
    const api = { ...createMockAdapter(null), login: vi.fn(async () => { throw new Error("down"); }) };
    renderWithApp(<Header />, { api });
    expect(await screen.findByLabelText("Switch role")).toBeInTheDocument();
  });
});
