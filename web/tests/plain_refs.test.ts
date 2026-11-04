import { describe, expect, it } from "vitest";
import { plainError, plainRef, plainRefs, shortRef } from "../src/lib/format";

describe("plainRefs", () => {
  it("puts a record reference in plain words and keeps a visible short id", () => {
    expect(shortRef("Procedure/colonoscopy-4becddf0-9201-5a1b-9071-08eba3bfc758")).toBe("\u2026bfc758");
    expect(plainRef("Procedure/colonoscopy-4becddf0-9201-5a1b-9071-08eba3bfc758")).toBe("the colonoscopy record (\u2026bfc758)");
    expect(plainRefs("evidence: DiagnosticReport/colonoscopy-report-4becddf0-9201-aa11bb")).toBe("evidence: the colonoscopy report (\u2026aa11bb)");
  });
  it("handles the second pathway's colposcopy references the same way", () => {
    expect(plainRef("Procedure/colposcopy-7f3a9c10-22bb")).toBe("the colposcopy record (\u202622bb)");
    expect(plainRefs("evidence: DiagnosticReport/colposcopy-report-7f3a9c10-aa11bb")).toBe("evidence: the colposcopy report (\u2026aa11bb)");
  });
  it("covers the cervical pathway's references as they are really made (a uuid after the procedure name)", () => {
    const id = "4becddf0-9201-5a1b-9071-08eba3bfc758";
    expect(plainRef(`Procedure/colposcopy-${id}`)).toBe("the colposcopy record (\u2026bfc758)");
    expect(plainRef(`DiagnosticReport/colposcopy-report-${id}`)).toBe("the colposcopy report (\u2026bfc758)");
    expect(plainRefs(`closed on Procedure/colposcopy-${id}, signed Practitioner/771a63a98e3d`))
      .toBe("closed on the colposcopy record (\u2026bfc758), signed the clinician (\u2026a98e3d)");
    // a colonoscopy and a colposcopy record of the same id are not mixed up
    expect(plainRef(`Procedure/colonoscopy-${id}`)).not.toBe(plainRef(`Procedure/colposcopy-${id}`));
  });
  it("never hides an unknown kind of reference: it still gets words and a visible short id", () => {
    expect(plainRef("Procedure/something-new-12ab34")).toBe("the procedure record (\u202612ab34)");
    expect(plainRef("Observation/weird")).toMatch(/^the record \(\u2026/);
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
    expect(plainError("evidence_ref 'Procedure/colposcopy-7f3a9c10-22bb' is not in the FHIR store"))
      .toBe("evidence reference 'Procedure/colposcopy-7f3a9c10-22bb' is not in the FHIR store");
  });
});
