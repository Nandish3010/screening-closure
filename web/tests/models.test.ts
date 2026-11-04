import { describe, expect, it } from "vitest";
import { modelHeadline, modelKind, modelWords } from "../src/lib/models";

// api/app/triage: the calibration's model_id is "qwen2.5-1.5b-4bit+lora-<hash>" (the model on the host), the opt-out gate is
// "rule:opt-out-gate-v1", and a failed or missing model is "unavailable". Anything else is an id this UI cannot place.
describe("the model's label is derived from its id", () => {
  it("calls only the local triage model on-device", () => {
    const id = "qwen2.5-1.5b-4bit+lora-8b5a49fc";
    expect(modelKind(id)).toBe("local");
    expect(modelWords(id)).toBe("on-device model");
    expect(modelHeadline(id)).toMatch(/^On-device model/);
  });
  it("calls every other model id a neutral AI model", () => {
    for (const id of ["claude-haiku-4-5", "gpt-x", "llama-3", "some-remote-model"]) {
      expect(modelKind(id)).toBe("other");
      expect(modelWords(id)).toBe("AI model");
      expect(modelHeadline(id)).toBe("AI model");
    }
  });
  it("says so when there is no model or a rule decided", () => {
    for (const id of [null, undefined, "", "unavailable"]) {
      expect(modelWords(id)).toBe("no model available");
      expect(modelHeadline(id)).toMatch(/^No model available/);
    }
    expect(modelWords("rule:opt-out-gate-v1")).toBe("opt-out rule, no AI");
    expect(modelHeadline("rule:opt-out-gate-v1")).toMatch(/^Opt-out rule/);
  });
});
