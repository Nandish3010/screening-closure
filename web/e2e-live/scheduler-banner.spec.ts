/**
 * T-C16 on the real stack: stop the scheduler container, the dashboard's red banner appears within 90 s,
 * start it again, the banner clears. Needs docker on the host and the compose stack running. It writes no cases.
 */
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { stackProblem } from "./helpers";

const REPO = fileURLToPath(new URL("../..", import.meta.url));
const compose = (...args: string[]) => execFileSync("docker", ["compose", ...args], { cwd: REPO, stdio: "pipe", timeout: 120_000 });

let problem: string | null = null;
test.beforeAll(async () => {
  problem = await stackProblem();
  if (!problem) {
    try {
      compose("ps", "--status", "running", "scheduler");
      const running = compose("ps", "--status", "running", "--services").toString();
      if (!running.split("\n").includes("scheduler")) problem = "the scheduler container is not running (make up)";
    } catch (e) {
      problem = `docker compose is not usable from here: ${(e as Error).message.split("\n")[0]}`;
    }
  }
});

test("T-C16: a stopped scheduler raises the dashboard banner within 90 s and a restart clears it", async ({ page }) => {
  test.setTimeout(6 * 60_000);
  test.skip(problem !== null, `live e2e skipped: ${problem}`);
  // The alert is only comparable from a healthy start: the scheduler is running and no alert is open.
  compose("start", "scheduler");
  await page.goto("/dashboard");
  await expect(page.getByTestId("scheduler-banner")).toHaveCount(0, { timeout: 90_000 });

  const stoppedAt = Date.now();
  compose("stop", "scheduler");
  try {
    await expect(page.getByTestId("scheduler-banner")).toBeVisible({ timeout: 90_000 });
    const seconds = Math.round((Date.now() - stoppedAt) / 1000);
    console.log(`T-C16: banner visible ${seconds} s after the scheduler stopped`);
    expect(seconds).toBeLessThanOrEqual(90);
    await expect(page.getByTestId("scheduler-banner")).toContainText("scheduler is down");
  } finally {
    compose("start", "scheduler"); // whatever happened, leave the scheduler running
  }
  const restartedAt = Date.now();
  await expect(page.getByTestId("scheduler-banner")).toHaveCount(0, { timeout: 90_000 });
  await expect(page.getByTestId("scheduler-recovered")).toBeVisible();
  console.log(`T-C16: banner cleared ${Math.round((Date.now() - restartedAt) / 1000)} s after the restart`);
});
