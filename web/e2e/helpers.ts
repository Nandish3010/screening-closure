import type { Page } from "@playwright/test";

export const OVERDUE_CASE = "c0000001-0000-4000-8000-000000000001";
export const PATIENT_CASE = "c0000007-0000-4000-8000-000000000007";
export const BOUNCED_CASE = "c0000015-0000-4000-8000-000000000015";
export const PENDING_HANDOFF_CASE = "c0000016-0000-4000-8000-000000000016";
export const WATCHDOG_CASE = "c0000013-0000-4000-8000-000000000013";

/** Fresh mock data for every test (the mock persists to localStorage), without wiping on in-test reloads. */
export async function freshData(page: Page) {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("e2e-init")) {
      localStorage.clear();
      sessionStorage.setItem("e2e-init", "1");
    }
  });
}
