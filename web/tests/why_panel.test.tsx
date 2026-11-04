import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { TimelineItem } from "../src/api/contract";
import { createMockAdapter } from "../src/api/mockAdapter";
import { DraftsPanel } from "../src/components/DraftsPanel";
import { Timeline } from "../src/components/Timeline";
import { TRIAGE_THRESHOLD, WhyPanel, whyContent } from "../src/components/WhyPanel";
import type { DraftView } from "../src/lib/derive";
import { AgencyPage } from "../src/pages/AgencyPage";
import { CoordinatorPage } from "../src/pages/CoordinatorPage";
import { renderWithApp } from "./helpers";

const draft = (over: Partial<DraftView>): DraftView => ({
  draft_id: "d-1", text: "Hello", text_ar: null, text_en: "Hello", language: "en", provider: "claude", fallback_used: false,
  abstention_reason: null, prompt_hash: "abc", case_id: "c1", purpose: "reminder", channel: "sms", model_id: "claude-test",
  created_by: "model:claude", created_at: "2026-11-01T09:00:00.000Z", approved_at: null, approved_by: null, rejected_at: null,
  rejected_by: null, rejection_reason: null, status: "pending", ...over,
});
const noop = () => undefined;
const renderDrafts = (drafts: DraftView[]) =>
  render(<MemoryRouter><DraftsPanel drafts={drafts} canRequest canDecide onRequest={noop} onApprove={noop} onReject={noop} /></MemoryRouter>);

describe("Why? panel: the language-group rule", () => {
  it("names the Gulf-dialect rule in plain words when the API says it forced a person", () => {
    const c = whyContent({ kind: "triage", model_id: "qwen-lora", confidence: 0.99, needs_human: true, forced_reason: "ar_gulf" });
    expect(c.rule).toMatch(/^Gulf dialect: a person always checks these replies \(safety rule\)/);
    expect(c.rule).not.toMatch(/HZ-43|55\.3/); // the rule id and the threshold are under the technical details
    expect(c.technical).toMatch(/HZ-43.*55\.3%/);
    expect(c.rule).toMatch(/Handled without a person only if/);
  });
  it("says nothing about a group when there is no forced_reason", () => {
    expect(whyContent({ kind: "triage", model_id: "qwen-lora", confidence: 0.4, needs_human: true }).technical).not.toMatch(/HZ-43/);
  });
  it("calls only the local Qwen model on-premises; any other id is a neutral AI model, and none or a rule say so", () => {
    expect(whyContent({ kind: "triage", model_id: "qwen2.5-1.5b-4bit+lora-1", confidence: 1 }).model).toMatch(/^On-premises model/);
    expect(whyContent({ kind: "triage", model_id: "claude-haiku-x", confidence: 1 }).model).toBe("AI model");
    expect(whyContent({ kind: "triage", model_id: "claude-haiku-x", confidence: 1 }).modelId).toBe("claude-haiku-x");
    expect(whyContent({ kind: "triage", model_id: "unavailable" }).model).toMatch(/No model available/);
    expect(whyContent({ kind: "triage", model_id: "rule:optout" }).model).toMatch(/Opt-out rule/);
  });
  it("the model id and the rule id sit behind a button, not a nested details element", async () => {
    const user = userEvent.setup();
    render(<WhyPanel testId="w" subject="reply label" input={{ kind: "triage", model_id: "qwen-lora", confidence: 0.9, needs_human: true, forced_reason: "ar_gulf" }} />);
    const panel = screen.getByTestId("w");
    expect(panel.tagName).toBe("DETAILS");
    expect(panel.querySelectorAll("details")).toHaveLength(0); // no disclosure nested inside the panel
    await user.click(screen.getByRole("button", { name: /Model details/ }));
    expect(screen.getByTestId("w-model-id")).toHaveTextContent("qwen-lora");
    expect(screen.getByTestId("w-technical")).toHaveTextContent(/HZ-43/);
    expect(screen.getByRole("button", { name: /Hide model details/ })).toHaveAttribute("aria-expanded", "true");
  });
});

describe("Why? panel", () => {
  it("is collapsed by default and has its own accessible name", () => {
    renderDrafts([draft({})]);
    const panel = screen.getByTestId("why-draft-d-1") as HTMLDetailsElement;
    expect(panel.open).toBe(false);
    expect(within(panel).getByText("Why? (draft d-1)")).toBeInTheDocument();
  });

  it("drafts: a template fallback says 'Template (no AI)', the abstention reason, and that a person approves", async () => {
    const user = userEvent.setup();
    renderDrafts([draft({ provider: "template", fallback_used: true, abstention_reason: "validation_failed:forbidden_phrase:cancer" })]);
    await user.click(screen.getByText("Why? (draft d-1)"));
    expect(screen.getByTestId("why-draft-d-1-model")).toHaveTextContent("Template (no AI)");
    expect(screen.getByTestId("why-draft-d-1-confidence")).toHaveTextContent("not applicable");
    expect(screen.getByTestId("why-draft-d-1-rule")).toHaveTextContent(/forbidden-phrase validator/);
    expect(screen.getByTestId("why-draft-d-1-rule")).toHaveTextContent(/failed the safety checks/);
    expect(screen.getByTestId("why-draft-d-1-human")).toHaveTextContent("Approve or reject before anything is sent.");
    expect(within(screen.getByTestId("why-draft-d-1")).getByRole("link")).toHaveAttribute("href", expect.stringMatching(/model-cards\/template-fallback\.md$/));
  });

  it("drafts: a model draft names provider and model and links the drafting card", () => {
    renderDrafts([draft({})]);
    expect(screen.getByTestId("why-draft-d-1-model")).toHaveTextContent("claude / claude-test");
    expect(within(screen.getByTestId("why-draft-d-1")).getByRole("link")).toHaveAttribute("href", expect.stringMatching(/model-cards\/claude-drafting\.md$/));
  });

  it("triage: shows confidence, the threshold rule and the needs-human text on the timeline", () => {
    const items: TimelineItem[] = [{
      kind: "event", seq: 5, type: "reply_classified", at: "2026-11-02T09:00:00.000Z", actor: "system:triage",
      payload: { intent: "question", confidence: 0.42, model_id: "qwen-lora", needs_human: true },
    }];
    render(<Timeline items={items} />);
    expect(TRIAGE_THRESHOLD).toBe(0.553);
    expect(screen.getByText("Why? (reply label #5)")).toBeInTheDocument();
    expect(screen.getByTestId("why-triage-5-model")).toHaveTextContent("On-premises model");
    expect(screen.queryByTestId("why-triage-5-model-id")).toBeNull(); // the id only shows after "Model details" is opened
    expect(screen.getByTestId("why-triage-5-confidence")).toHaveTextContent("42%");
    expect(screen.getByTestId("why-triage-5-rule")).toHaveTextContent(/only if the model is confident enough and the intent is not question, other or opt-out/);
    expect(screen.getByTestId("why-triage-5-rule")).toHaveTextContent(/waiting for a person/);
    expect(screen.getByTestId("why-triage-5-human")).toHaveTextContent(/opt-out is always read by a person/);
  });

  it("triage: model 'unavailable' says no model, sent to a person", () => {
    const c = whyContent({ kind: "triage", model_id: "unavailable", confidence: 0, needs_human: true });
    expect(c.model).toBe("No model available. Sent to a person.");
    expect(c.card.path).toMatch(/reply-triage\.md$/);
  });

  it("risk: one panel on the worklist says it orders only and a person decides", async () => {
    renderWithApp(<CoordinatorPage />);
    await screen.findByTestId("worklist");
    expect(screen.getByTestId("why-risk-rule")).toHaveTextContent(/orders the worklist only; never acts\. language is not an input/i);
    expect(screen.getByTestId("why-risk-human")).toHaveTextContent(/does not change any due date or escalation/);
    expect(screen.getByTestId("why-risk-confidence")).toHaveTextContent(/percentage/);
    expect(within(screen.getByTestId("why-risk")).getByRole("link")).toHaveAttribute("href", expect.stringMatching(/risk-score\.md$/));
  });

  it("planner: shown with the run's provenance, tasks are accepted or dismissed by a person", async () => {
    const user = userEvent.setup();
    renderWithApp(<AgencyPage />, { api: createMockAdapter(null), role: "supervisor" });
    await user.click(await screen.findByTestId("run-agency"));
    await screen.findByTestId("run-result");
    await screen.findByTestId("why-planner");
    expect(screen.getByTestId("why-planner-rule")).toHaveTextContent(/cannot change case state/);
    expect(screen.getByTestId("why-planner-rule")).toHaveTextContent(/needs-human inbox/);
    expect(screen.getByTestId("why-planner-human")).toHaveTextContent("Accept or dismiss each task.");
    expect(screen.getByTestId("why-planner-confidence")).toHaveTextContent("not applicable");
  });
});
