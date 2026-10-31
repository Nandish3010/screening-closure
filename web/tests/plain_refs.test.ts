import { describe, expect, it } from "vitest";
import { plainError, plainRef, plainRefs, shortRef } from "../src/lib/format";

describe("plainRefs", () => {
  it("puts a record reference in plain words and keeps a visible short id", () => {
    expect(shortRef("Procedure/colonoscopy-4becddf0-9201-5a1b-9071-08eba3bfc758")).toBe("\u2026bfc758");
    expect(plainRef("Procedure/colonoscopy-4becddf0-9201-5a1b-9071-08eba3bfc758")).toBe("the colonoscopy record (\u2026bfc758)");
    expect(plainRefs("evidence: DiagnosticReport/colonoscopy-report-4becddf0-9201-aa11bb")).toBe("evidence: the colonoscopy report (\u2026aa11bb)");
  });
  it("tells two different records apart", () => {
    expect(plainRef("Procedure/colonoscopy-0001")).not.toBe(plainRef("Procedure/colonoscopy-0017"));
  });
  it("leaves nothing to say about text without a reference", () => {
    expect(plainRefs("nothing to change")).toBe("nothing to change");
  });
});

describe("plainError", () => {
  it("shows the exact submitted reference in a refusal, only the field name is plain", () => {
    expect(plainError("evidence_ref 'Procedure/colonoscopy-0001' does not resolve to a resource of this case"))
      .toBe("evidence reference 'Procedure/colonoscopy-0001' does not resolve to a resource of this case");
  });
});
