import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// The public preview (/preview): screenshots of synthetic data, reachable without signing in, no API calls of its own.
for (const [label, size] of [["desktop", { width: 1280, height: 800 }], ["phone", { width: 390, height: 844 }]] as const) {
  test(`preview page (${label}): five steps, images load, no sideways scroll, targets 44 px, no axe violations`, async ({ page }) => {
    await page.setViewportSize(size);
    const requests: string[] = [];
    page.on("request", (r) => requests.push(new URL(r.url()).pathname));
    await page.goto("/preview");
    await expect(page.getByRole("heading", { level: 1, name: "Preview: screenshots of synthetic data" })).toBeVisible();
    const items = page.getByTestId("preview-steps").locator("li");
    await expect(items).toHaveCount(5);
    for (let i = 0; i < 5; i++) {
      const img = items.nth(i).locator("img");
      await img.scrollIntoViewIfNeeded();
      await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
      await expect(img).toHaveAttribute("alt", /.{40,}/);
    }
    await expect(page.getByTestId("preview-numbers")).toContainText("40% target is not met");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    for (const l of await page.getByRole("link", { name: /Back to sign in/ }).all()) {
      expect((await l.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations.map((v) => v.id)).toEqual([]);
    expect(requests.filter((p) => /^\/(cases|me|metrics|agency|auth|api)/.test(p))).toEqual([]);
  });
}
