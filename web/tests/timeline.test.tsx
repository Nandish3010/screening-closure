import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TimelineItem } from "../src/api/contract";
import { Timeline } from "../src/components/Timeline";

describe("Timeline derived items", () => {
  it("shows a backdated entry (T-F4, ADR-027) as a flag, in the order the API sent", () => {
    const items: TimelineItem[] = [
      { kind: "event", seq: 2, event_id: "e2", type: "acknowledge", at: "2026-10-19T08:00:00Z", actor: "lab:l1", payload: {} },
      { kind: "event", seq: 3, event_id: "e3", type: "accept_ownership", at: "2026-10-17T08:00:00Z", actor: "primary_care_owner:p1", payload: {} },
      { kind: "backdated", at: "2026-10-17T08:00:00Z", event_id: "e3", event_type: "accept_ownership", previous_at: "2026-10-19T08:00:00Z", behind_seconds: 172800 },
    ];
    render(<Timeline items={items} />);
    const flag = screen.getByTestId("tl-backdated");
    expect(flag).toHaveTextContent(/backdated entry/i);
    expect(flag).toHaveTextContent(/earlier than the entry before it/i);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});
