import { test } from "@playwright/test";

// Opt-in: SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts  -> web/screenshots/*.png
test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to capture");

const PATIENT = "c0000007-0000-4000-8000-000000000007";
const OVERDUE = "c0000001-0000-4000-8000-000000000001";

for (const scheme of ["light", "dark"] as const) {
  test(`screenshots ${scheme}`, async ({ browser }) => {
    const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width: 1200, height: 900 } });
    const page = await ctx.newPage();
    const shot = (name: string) => page.screenshot({ path: `screenshots/${name}-${scheme}.png`, fullPage: true });
    await page.goto("/coordinator");
    await page.getByTestId("worklist-row").first().waitFor();
    await shot("coordinator-worklist");
    await page.goto(`/coordinator/${OVERDUE}`);
    await page.getByTestId("timeline").waitFor();
    await shot("case-detail");
    await page.goto("/dashboard");
    await page.getByTestId("kpi-p50").waitFor();
    await shot("dashboard");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/patient/${PATIENT}`);
    await page.getByTestId("patient-owner").waitFor();
    await shot("patient-en-mobile");
    await page.getByRole("button", { name: /العربية/ }).click();
    await shot("patient-ar-mobile");
    await ctx.close();
  });
}
