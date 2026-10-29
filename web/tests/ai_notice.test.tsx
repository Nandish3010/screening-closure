import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DEMO_PATIENT_CASE_ID } from "../src/api/fixtures";
import { STRINGS } from "../src/i18n/strings";
import { PatientView } from "../src/pages/PatientPage";
import { renderWithApp } from "./helpers";

const KEYS = ["aiNoticeSoftware", "aiNoticeStaff", "aiNoticePerson", "aiNoticeStop"] as const;
const BANNED = [/blood/i, /cancer/i, /positive/i, /negative/i, /abnormal/i, /diagnos/i, /found/i, /finding/i, /result/i, /disease/i, /worry/i, /\bnormal\b/i,
  /\bAI\b/, /decides?/i, /دم/, /سرطان/, /ايجابي|إيجابي/, /سلبي/, /مصاب/, /تشخيص/, /طبيعي/, /تقلق/, /نتيجة/, /مرض/];

describe("patient notice: how we prepare messages", () => {
  it("renders in English and Arabic (dir=rtl when toggled) from the same keys", async () => {
    const user = userEvent.setup();
    renderWithApp(<PatientView caseId={DEMO_PATIENT_CASE_ID} />, { role: "patient" });
    await screen.findByText(STRINGS.en.resultBody);
    const notice = screen.getByTestId("ai-notice");
    expect(notice).toHaveTextContent(STRINGS.en.aiNoticeHeading);
    for (const k of KEYS) expect(notice).toHaveTextContent(STRINGS.en[k]);
    await user.click(screen.getByRole("button", { name: /العربية/ }));
    expect(screen.getByTestId("patient-root")).toHaveAttribute("dir", "rtl");
    const ar = screen.getByTestId("ai-notice");
    expect(ar).toHaveTextContent(STRINGS.ar.aiNoticeHeading);
    for (const k of KEYS) {
      expect(ar).toHaveTextContent(STRINGS.ar[k]);
      expect(STRINGS.ar[k]).toMatch(/[؀-ۿ]/);
    }
  });

  it("uses no banned or disclosing words in either language, and does not say AI decides", () => {
    for (const lang of ["en", "ar"] as const) {
      const text = [STRINGS[lang].aiNoticeHeading, ...KEYS.map((k) => STRINGS[lang][k])].join(" ");
      for (const re of BANNED) expect(text, `${lang}: ${re}`).not.toMatch(re);
    }
  });

  it("English is easy to read: every sentence has at most 15 words", () => {
    for (const k of KEYS) {
      const sentences = STRINGS.en[k].split(/(?<=[.!?])\s+/).filter(Boolean);
      for (const s of sentences) expect(s.split(/\s+/).length, s).toBeLessThanOrEqual(15);
    }
  });

  it("points at the real buttons by their exact names", () => {
    for (const lang of ["en", "ar"] as const) {
      expect(STRINGS[lang].aiNoticePerson).toContain(STRINGS[lang].dontUnderstand);
      expect(STRINGS[lang].aiNoticeStop).toContain(STRINGS[lang].withdrawButton);
      expect(STRINGS[lang].aiNoticeStop).toContain(STRINGS[lang].consentHeading);
    }
  });
});
