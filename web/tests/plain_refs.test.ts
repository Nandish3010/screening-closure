import { describe, expect, it } from "vitest";
import { plainRefs } from "../src/lib/format";

describe("plainRefs", () => {
  it("turns record references and field names into plain words", () => {
    expect(plainRefs("the colonoscopy evidence: Procedure/colonoscopy-4becddf0-9201-5a1b")).toBe("the colonoscopy evidence: the colonoscopy record");
    expect(plainRefs("evidence: DiagnosticReport/colonoscopy-report-4becddf0-9201")).toBe("evidence: the colonoscopy report");
    expect(plainRefs("evidence_ref 'Procedure/x-1' does not resolve")).toBe("evidence reference 'the record' does not resolve");
    expect(plainRefs("nothing to change")).toBe("nothing to change");
  });
});
