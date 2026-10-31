/**
 * The case timeline reads in plain words by default: a sentence per event and who did it by name. The raw fields (event number,
 * `role:id`, ISO times, template and notification ids) sit behind a collapsed "Details" toggle.
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TimelineItem } from "../src/api/contract";
import { Timeline, plainSummary, plainWho } from "../src/components/Timeline";

const ev = (seq: number, type: string, actor: string, actor_kind: "human" | "automation" | "model" | "service", payload: Record<string, unknown> = {}): TimelineItem =>
  ({ kind: "event", seq, event_id: `e${seq}`, type, at: `2026-01-0${seq}T08:00:00Z`, actor, actor_kind, payload });

describe("Timeline in plain language", () => {
  it("shows a sentence and a name, and keeps the technical fields behind a closed Details toggle", () => {
    render(<Timeline items={[
      ev(9, "escalate", "system:scheduler", "service", { rung: "reminder", level: 1, target: "owner", due_at: "2026-01-12T08:00:00+00:00", notification_id: "c3c4ade9-c805" }),
    ]} />);
    const li = screen.getByTestId("timeline").querySelector("li")!;
    expect(within(li).getByTestId("tl-plain")).toHaveTextContent("Passed up to the owner (level 1).");
    expect(within(li).getByTestId("tl-who")).toHaveTextContent("By Scheduler");
    const details = within(li).getByTestId("tl-details");
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByTestId("tl-raw")).toHaveTextContent(/#9 · system:scheduler · .*due at: 2026-01-12T08:00:00\+00:00/);
    // nothing outside the Details toggle looks like a log line
    const visible = li.cloneNode(true) as HTMLElement;
    visible.querySelector("[data-testid=tl-details]")!.remove();
    expect(visible.textContent).not.toMatch(/system:scheduler|\+00:00|notification/);
  });

  it("names people and machinery in plain words", () => {
    expect(plainWho("system:automation", "automation")).toBe("Automation");
    expect(plainWho("system:triage", "service")).toBe("Triage");
    expect(plainWho("model:template", "model")).toBe("AI model");
    expect(plainWho("watchdog", "service")).toBe("Watchdog");
  });

  it("reads a classified reply as a sentence with no model id", () => {
    const s = plainSummary("reply_classified", { intent: "confirm", confidence: 0.98, model_id: "qwen2.5-1.5b-4bit+lora-8b5a49fc", needs_human: true });
    expect(s).toBe("Read as confirm (98% sure). A person must look at it.");
  });
});
