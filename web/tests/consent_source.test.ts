import { describe, expect, it } from "vitest";
import { consentSourceText } from "../src/components/Timeline";

describe("consent source on the timeline (ADR-059, F4)", () => {
  it("says how the patient asked, with the record reference for staff", () => {
    expect(consentSourceText({ channel: "phone_call", reference: "call-0412" })).toBe("asked by: phone call, ref call-0412");
    expect(consentSourceText({ channel: "patient_app" })).toBe("asked by: the patient, in the app");
    expect(consentSourceText({ channel: "reply_review", reference: "9f2c" })).toContain("a reply a person read");
  });
  it("is empty for an event written before the source existed", () => {
    expect(consentSourceText(undefined)).toBe("");
    expect(consentSourceText("phone")).toBe("");
  });
});
