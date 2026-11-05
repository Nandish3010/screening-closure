/**
 * E16: record the storyboard against the LIVE stack (http adapter), one webm per shot.
 *
 *   node tools/video/record.ts [--only 3,4,5] [--no-reset] [--no-build] [--cards-only]
 *
 * What it does, in order:
 *   1. waits for the stack (up to 3 tries, 2 minutes apart, in case another agent is rebuilding it);
 *   2. `make demo-reset`, then `make demo-scenario UNTIL=owner_accepted` (the scripted part of the demo, as the demo
 *      script's "before going on stage" step 5), and builds the web app with the http adapter into
 *      tools/video/out/web-build, served by `vite preview` on port 4175 (an origin the API's CORS allows);
 *   3. renders the title and end cards (out/cards/*.png);
 *   4. plays each shot of storyboard.yaml in its own browser context with `recordVideo` (1280x800), a caption bar and a
 *      "Synthetic data" strip injected into every page, and keeps the role (localStorage) from one shot to the next;
 *   5. writes out/shots.json (status, actual duration, cut windows) and ALWAYS, even on failure, starts the scheduler
 *      again, stops the preview server and runs `make demo-reset`.
 *
 * Optional shots (`optional: true`) are skipped, with the reason in shots.json, when their route is absent (the SPA
 * redirects unknown routes to "/", or the server answers 404), their guard element does not appear, or a step fails.
 * Node 22.18+ runs this file directly (type stripping); Playwright comes from web/node_modules, which Node would not find
 * from this directory, hence createRequire.
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { localChromium } from "../../web/pw-chromium.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");
const OUT = process.env.VIDEO_OUT ?? join(HERE, "out");
const RAW = join(OUT, "raw");
const PAGES = join(OUT, "pages");
const CARDS = join(OUT, "cards");
const BUILD = join(OUT, "web-build");
const API = process.env.LIVE_API_URL ?? "http://localhost:8000";
const FHIR = process.env.LIVE_FHIR_URL ?? "http://localhost:8080/fhir";
const PORT = Number(process.env.VIDEO_WEB_PORT ?? 4175);
const BASE = `http://localhost:${PORT}`;

const requireFromWeb = createRequire(join(REPO, "web/package.json"));
const { chromium } = requireFromWeb("playwright");

// ---------------------------------------------------------------------------------------------------------------------
type Action = Record<string, any>;
interface Shot {
  id: number; title: string; beat: string; route: string; caption: string; narration: string; duration: number;
  actions: Action[]; caption_pos?: string; optional?: boolean; submission?: boolean; requires?: { route: string; testid?: string };
}
interface Storyboard { meta: { width: number; height: number }; cards: Record<string, any>; shots: Shot[] }

const args = new Set(process.argv.slice(2));
const argValue = (name: string) => { const i = process.argv.indexOf(name); return i >= 0 ? process.argv[i + 1] : undefined; };
const only = argValue("--only")?.split(",").map(Number);

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const log = (...a: unknown[]) => console.log("[record]", ...a);

function loadStoryboard(): Storyboard {
  const py = existsSync(join(REPO, ".venv/bin/python")) ? join(REPO, ".venv/bin/python") : "python3";
  const r = spawnSync(py, [join(HERE, "storyboard.py"), "--json"], { encoding: "utf-8", maxBuffer: 1 << 24 });
  if (r.status !== 0) throw new Error(`storyboard.py --json failed: ${r.stderr}`);
  return JSON.parse(r.stdout);
}

function sh(cmd: string, timeoutS = 120): { code: number; out: string } {
  const r = spawnSync("sh", ["-c", cmd], { cwd: REPO, encoding: "utf-8", timeout: timeoutS * 1000, maxBuffer: 1 << 24 });
  return { code: r.status ?? 1, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

/** The agency run budget in seconds for the recording: a whole number from 20 (the API default) to 3600, 600 when unset. */
function agencyBudget(raw: string | undefined): number {
  if (raw === undefined || raw === "") return 600;
  if (!/^\d+$/.test(raw) || Number(raw) < 20 || Number(raw) > 3600) {
    throw new Error(`VIDEO_AGENCY_BUDGET must be a whole number of seconds from 20 to 3600, got ${JSON.stringify(raw)}`);
  }
  return Number(raw);
}

/** Recreate the API container with the given environment added (no shell: the values travel in the child's environment). */
function composeApi(env: Record<string, string>): { code: number; out: string } {
  const { AGENCY_RUN_BUDGET_SECONDS: _inherited, ...base } = process.env; // an inherited value never leaks into the restore
  const r = spawnSync("docker", ["compose", "up", "-d", "--no-build", "api"], { cwd: REPO, encoding: "utf-8", timeout: 180_000, env: { ...base, ...env } });
  return { code: r.status ?? 1, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

function shAsync(cmd: string, timeoutS = 120): Promise<{ code: number; out: string }> {
  return new Promise((res) => {
    const c = spawn("sh", ["-c", cmd], { cwd: REPO });
    let out = "";
    c.stdout.on("data", (d) => (out += d));
    c.stderr.on("data", (d) => (out += d));
    const timer = setTimeout(() => c.kill(), timeoutS * 1000);
    c.on("close", (code) => { clearTimeout(timer); res({ code: code ?? 1, out }); });
  });
}

function make(target: string, extra: string[] = []) {
  const r = spawnSync("make", [target, ...extra], { cwd: REPO, stdio: "inherit" });
  if (r.status !== 0) throw new Error(`make ${target} failed (exit ${r.status})`);
}

const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function stackProblem(): Promise<string | null> {
  try {
    const h = await fetch(`${API}/health`, { signal: AbortSignal.timeout(4000) });
    if (!h.ok) return `API ${API}/health answered HTTP ${h.status}`;
    const f = await fetch(`${FHIR}/metadata`, { signal: AbortSignal.timeout(8000) });
    if (!f.ok) return `HAPI ${FHIR} answered HTTP ${f.status}`;
  } catch (e) {
    return `the stack is not ready: ${(e as Error).message}`;
  }
  return null;
}

async function waitForStack() {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const p = await stackProblem();
    if (!p) return;
    log(`stack not ready (${p}); attempt ${attempt} of 3${attempt < 3 ? ", waiting 2 minutes" : ""}`);
    if (attempt < 3) await sleep(120_000);
  }
  throw new Error("the live stack is not up after 3 tries (make up, make demo-reset)");
}

/** fetch with 4 tries: right after `make demo-reset` the API can still drop a connection. */
async function retryFetch(url: string, init?: RequestInit): Promise<Response> {
  let last: unknown;
  for (let i = 0; i < 4; i++) {
    try { return await fetch(url, init); } catch (e) { last = e; await sleep(3000); }
  }
  throw new Error(`fetch ${url} failed: ${(last as Error).message} (${String((last as any)?.cause)})`);
}

async function apiLogin(role: string, name: string) {
  const r = await retryFetch(`${API}/auth/dev-login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role, name }) });
  if (!r.ok) throw new Error(`dev-login ${role}: HTTP ${r.status}`);
  return (await r.json()) as { token: string };
}

/** The named patient's case (owner accepted, by the pre-run) and the prepared second case (confirmatory completed). */
async function findCases(): Promise<{ case: string; case2: string; ltc: string }> {
  const { token } = await apiLogin("coordinator", "Maryam Al Suwaidi");
  const rows = (await (await retryFetch(`${API}/cases`, { headers: { Authorization: `Bearer ${token}` } })).json()) as any[];
  const first = rows.find((r) => r.state === "owner_accepted");
  const second = rows.find((r) => r.state === "confirmatory_completed");
  const urgent = rows.find((r) => r.pathway === "chronic_conditions" && r.priority?.tier === "urgent" && r.state !== "closed");
  if (!urgent) throw new Error("no urgent long-term-conditions case: `make demo-seed-chronic` did not run");
  if (!first) throw new Error("no case at owner_accepted: `make demo-scenario UNTIL=owner_accepted` did not run, or the demo was not reset");
  if (!second) throw new Error("no case at confirmatory_completed: run `make demo-stage` and `make demo-snapshot` (the second case is part of the snapshot)");
  return { case: first.case_id, case2: second.case_id, ltc: urgent.case_id };
}

// ---- page rendering: cards, terminal ---------------------------------------------------------------------------------
const BASE_CSS = `
  *{box-sizing:border-box} html,body{margin:0;height:100%}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:#0e2a35;color:#f4f8fa;display:flex;align-items:center;justify-content:center}
  .wrap{width:1100px;padding-bottom:70px} h1{font-size:54px;margin:0 0 18px;letter-spacing:-.01em} p.line{font-size:28px;line-height:1.35;margin:0 0 20px;color:#d4e6ee}
  .mark{display:inline-grid;place-items:center;width:46px;height:46px;border-radius:50%;background:#2a9d8f;color:#fff;font-size:28px;margin-right:14px;vertical-align:8px}
  .note{font-size:20px;color:#ffb703;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
  .brandline{font-size:26px;font-weight:700;letter-spacing:.06em;color:#9fc0cd;text-transform:uppercase;margin-bottom:10px}
  .cta{font-size:44px;font-weight:800;color:#ffb703;margin:6px 0 22px}
  .big{font-size:190px;font-weight:800;color:#ffb703;line-height:1;margin:0 0 12px} .src{font-size:20px;color:#9fc0cd;margin-top:16px}
  ul{font-size:34px;line-height:1.5;padding-left:34px;margin:10px 0} li{margin-bottom:10px}
  table{border-collapse:collapse;width:100%;font-size:26px} th,td{padding:14px 16px;text-align:left;border-bottom:1px solid #2c4b58;vertical-align:top}
  th{font-size:20px;color:#9fc0cd;text-transform:uppercase;letter-spacing:.04em} td.v{font-weight:700;white-space:nowrap} td small{display:block;color:#9fc0cd;font-weight:400;font-size:17px}
  tr.hl td{background:#16414f} tr.hl td:first-child{border-left:6px solid #ffb703}
  .term{width:1180px;background:#0b0f12;border:1px solid #2b343b;border-radius:10px;padding:18px 22px;font:18px/1.35 ui-monospace,Menlo,Consolas,monospace;color:#d7e2e8;white-space:pre-wrap;word-break:break-word;margin-bottom:60px}
  .term .ps{color:#7bd88f}.term .ok{color:#7bd88f;font-weight:700}
`;

function pageHtml(body: string, css = ""): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>shot</title><style>${BASE_CSS}${css}</style></head><body>${body}</body></html>`;
}

function parseHeadline(sourceRel: string): { header: string[]; rows: string[][] } {
  const md = readFileSync(join(REPO, sourceRel), "utf-8").split("\n");
  const start = md.findIndex((l) => l.startsWith("## Headline"));
  const tableStart = md.findIndex((l, i) => i > start && l.startsWith("|"));
  const lines: string[] = [];
  for (let i = tableStart; i < md.length && md[i].startsWith("|"); i++) lines.push(md[i]);
  const cells = (l: string) => l.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  return { header: cells(lines[0]), rows: lines.slice(2).map(cells) };
}

function cardHtml(c: Action): string {
  if (c.kind === "stat") {
    return pageHtml(`<div class="wrap">${c.brand ? `<div class="brandline">${esc(c.brand)}</div>` : ""}<div class="big">${esc(c.value)}</div><p class="line">${esc(c.text)}</p><div class="src">${esc(c.source)}${c.footer ? `. ${esc(c.footer)}` : ""}</div></div>`);
  }
  if (c.kind === "figure") {
    return pageHtml(`<div class="wrap"><h1 style="font-size:40px;color:#9fc0cd;margin-bottom:6px">${esc(c.heading)}</h1><div class="big" style="font-size:150px">${esc(c.value)}</div><p class="line" style="font-size:32px;max-width:1000px">${esc(c.label)}</p><ul>${((c.bullets ?? []) as string[]).map((b) => `<li>${esc(b)}</li>`).join("")}</ul><div class="note">${esc(c.note)}</div></div>`);
  }
  if (c.kind === "bignum") {
    return pageHtml(`<div class="wrap"><div class="big" style="font-size:170px">${esc(c.value)}</div>${((c.lines ?? []) as string[]).slice(0, 2).map((l) => `<p class="line" style="font-size:36px;margin:0 0 14px">${esc(l)}</p>`).join("")}</div>`);
  }
  if (c.kind === "slide") {
    return pageHtml(`<div class="wrap"><h1>${esc(c.heading)}</h1><ul>${(c.bullets as string[]).map((b) => `<li>${esc(b)}</li>`).join("")}</ul><div class="note">Synthetic data. All figures from the simulation results.</div></div>`, "body{align-items:flex-start;padding-top:36px}");
  }
  if (c.kind === "text") {
    return pageHtml(`<div class="wrap"><h1>${esc(c.heading)}</h1><p class="line">${esc(c.text)}</p></div>`);
  }
  if (c.kind === "results") {
    const { header, rows } = parseHeadline(c.source);
    const valueCols = (c.columns as string[]).map((name) => {
      const i = header.indexOf(name);
      if (i < 1) throw new Error(`results card: no column ${JSON.stringify(name)} in ${c.source}`);
      return i;
    });
    const body = (c.rows as string[]).map((name) => {
      const row = rows.find((r) => r[0].startsWith(name));
      if (!row) throw new Error(`results card: no row starting with ${JSON.stringify(name)} in ${c.source}`);
      return `<tr class="hl"><td>${esc(row[0])}</td>${valueCols.map((i) => { const m = /^(\S+%?)\s*(\(.*\))?$/.exec(row[i]); return `<td class="v">${esc(m?.[1] ?? row[i])}${m?.[2] ? `<small>95% CI ${esc(m[2].replace(/[()]/g, ""))}</small>` : ""}</td>`; }).join("")}</tr>`;
    }).join("");
    return pageHtml(`<div class="wrap" style="width:1160px"><h1 style="font-size:40px">Simulation, synthetic data: baseline vs orchestrated</h1><table><tr><th>Metric</th>${valueCols.map((i) => `<th>${esc(header[i])}</th>`).join("")}</tr>${body}</table><div class="src">Simulation results, headline table (literature-anchored scenario). Before capacity limits.</div></div>`);
  }
  throw new Error(`unknown card kind ${c.kind}`);
}

function terminalHtml(cmd: string, output: string, ok: boolean | null): string {
  return pageHtml(`<div class="term"><span class="ps">$</span> ${esc(cmd)}\n${esc(output)}${ok === null ? "" : ok ? "" : "\n(command exited with an error)"}</div>`, "body{align-items:flex-start;padding-top:40px}");
}

function overlayScript(caption: string, pos = "bottom"): string {
  return `(() => {
  const FIRST = ${JSON.stringify(caption)};
  const POS = ${JSON.stringify(pos)};
  const PREFIX = "__vcap:";
  const css = "#__vcap_bar{position:fixed;left:50%;transform:translateX(-50%);" + (POS === "top" ? "top:12px;" : "bottom:34px;") + "width:min(1180px,calc(100vw - 40px));z-index:2147483647;pointer-events:none;background:rgba(8,18,26,.9);color:#fff;font:600 22px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:12px 18px;border-radius:10px;text-align:center;direction:ltr;unicode-bidi:isolate;box-shadow:0 4px 18px rgba(0,0,0,.35)}"
    + "#__vcap_bar.m{font-size:18px}#__vcap_bar.s{font-size:15px;line-height:1.3}"
    + "#__vcap_strip{position:fixed;left:0;right:0;bottom:0;height:26px;z-index:2147483647;pointer-events:none;background:#ffb703;color:#1b1300;font:800 13px/26px -apple-system,Segoe UI,sans-serif;letter-spacing:.08em;text-align:center;text-transform:uppercase}"
    + '[data-testid="consent-withdraw-why"]{display:none !important}' // a supervisor's disabled-consent note is not what the shot is about (the page still says it to a real user)
    + "body{padding-bottom:170px !important}"
    // The sign-off panel's read-only signer field holds a practitioner id: hide it behind a plain label (the page still has the value).
    + 'input[data-testid="signer"]{color:transparent !important;background:#fff url("data:image/svg+xml;utf8,<svg xmlns=%27http://www.w3.org/2000/svg%27 width=%27300%27 height=%2740%27><text x=%2712%27 y=%2726%27 font-family=%27sans-serif%27 font-size=%2716%27 fill=%27%23444%27>You, signed in</text></svg>") no-repeat left center !important}'
    // The patient app's language switch wraps its two names letter by letter at phone width; keep each word whole.
    + ".pa-lang span{overflow-wrap:normal !important;white-space:nowrap !important;padding:0 10px !important}";
  function ensure() {
    if (!document.documentElement) return;
    if (!document.getElementById("__vcap_style")) { const s = document.createElement("style"); s.id = "__vcap_style"; s.textContent = css; (document.head || document.documentElement).appendChild(s); }
    if (!document.getElementById("__vcap_bar") && document.body) {
      const b = document.createElement("div"); b.id = "__vcap_bar"; b.setAttribute("role", "note"); b.setAttribute("dir", "ltr"); document.body.appendChild(b);
      const t = document.createElement("div"); t.id = "__vcap_strip"; t.setAttribute("dir", "ltr"); t.textContent = "Synthetic data  \\u00b7  simulated time  \\u00b7  nothing is diagnosed"; document.body.appendChild(t);
    }
  }
  function current() { return window.name.startsWith(PREFIX) ? window.name.slice(PREFIX.length) : FIRST; }
  function render() {
    ensure(); const b = document.getElementById("__vcap_bar"); if (!b) return;
    const t = current(); b.textContent = t; b.className = t.length > 200 ? "s" : t.length > 110 ? "m" : "";
  }
  // What the viewer must not see: raw case ids (a short case number instead, the same for the same id on every page) and the
  // internal ticket tag "H1" in the demo banner and the priority note. Only text nodes are touched; the page's own data is not.
  const ID = /\\b(?=[0-9a-f]*[0-9])[0-9a-f]{8}\\b(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?/g;
  function alias(m) { return "#" + (1000 + (parseInt(m.slice(0, 8), 16) % 9000)); }
  function clean(root) {
    if (!root || !document.body) return;
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = n.nodeValue; if (!t) continue;
      const u = t.replace(/,\\s*H1\\)/g, ")").replace(/\\s*\\(H1\\)/g, "").replace(/\\s*\\(\u2026[0-9a-f]{4,}\\)/g, "").replace(ID, alias);
      if (u !== t) n.nodeValue = u;
    }
  }
  // A field whose value holds a raw record id (the evidence reference after "Record colonoscopy result") is covered by a plain label.
  const FIELD_BG = '#fff url("data:image/svg+xml;utf8,<svg xmlns=%27http://www.w3.org/2000/svg%27 width=%27360%27 height=%2740%27><text x=%2712%27 y=%2726%27 font-family=%27sans-serif%27 font-size=%2716%27 fill=%27%23444%27>Colonoscopy record on file</text></svg>") no-repeat left center';
  setInterval(() => {
    document.querySelectorAll("input:not([data-testid=signer]), textarea").forEach((el) => {
      const hit = new RegExp(ID.source).test(el.value || "");
      if (hit && !el.dataset.vcapMask) { el.dataset.vcapMask = "1"; el.style.setProperty("color", "transparent", "important"); el.style.setProperty("background", FIELD_BG, "important"); }
      else if (!hit && el.dataset.vcapMask) { delete el.dataset.vcapMask; el.style.removeProperty("color"); el.style.removeProperty("background"); }
    });
  }, 250);
  let pending = false;
  new MutationObserver(() => { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; clean(document.body); }); }).observe(document, { childList: true, subtree: true, characterData: true });
  window.__setCaption = (t) => { window.name = PREFIX + t; render(); };
  if (!window.name.startsWith(PREFIX)) window.name = PREFIX + FIRST;
  document.addEventListener("DOMContentLoaded", render);
  new MutationObserver(() => { if (document.body && !document.getElementById("__vcap_bar")) render(); }).observe(document, { childList: true, subtree: true });
  render();
})();`;
}

// ---- running a shot --------------------------------------------------------------------------------------------------
const ROLE_NAMES: Record<string, string> = {
  coordinator: "Maryam Al Suwaidi", owner: "Dr. Layla Haddad", specialist: "Dr. Karim Nasser",
  clinician: "Dr. Huda Rahman", supervisor: "Dr. Noura Al Hammadi", patient: "Patient",
};

const terminalCache = new Map<string, { output: string; ok: boolean }>();
let pageSeq = 0;

function fill(s: string, cases: Record<string, string>): string {
  return s.replace(/\{(case2?|ltc)\}/g, (_, k) => cases[k]);
}

function locator(page: any, t: Action) {
  if (t.testid) return page.getByTestId(t.testid);
  if (t.text) return page.getByText(t.text).first();
  if (t.heading) return page.getByRole("heading", { name: t.heading }).first();
  throw new Error(`no locator in ${JSON.stringify(t)}`);
}

async function runAction(page: any, act: Action, ctx: { cases: Record<string, string>; marks: { cuts: [number, number][]; open: number | null }; t0: number; vidOffset: () => number; timeout: number }) {
  const [verb] = Object.keys(act);
  const v = act[verb];
  const { cases } = ctx;
  const T = ctx.timeout;
  switch (verb) {
    case "goto":
      await page.goto(BASE + fill(v, cases), { waitUntil: "domcontentloaded" });
      await page.getByTestId("sim-badge").waitFor({ timeout: T });
      return;
    case "role": {
      await page.getByLabel("Switch role").selectOption(v);
      await page.waitForFunction((n: string) => document.querySelector(".who")?.textContent === n, ROLE_NAMES[v], { timeout: T });
      await sleep(600);
      return;
    }
    case "click": {
      let loc;
      if (v.testid) loc = page.getByTestId(v.testid).first();
      else if (v.name) loc = page.getByRole("button", { name: v.name }).first();
      else if (v.link) loc = page.getByRole("link", { name: v.link }).first();
      else if (v.row) loc = page.getByTestId("worklist-row").filter({ hasText: v.row }).first().getByRole("link").first();
      else throw new Error(`click: no target in ${JSON.stringify(v)}`);
      await loc.scrollIntoViewIfNeeded({ timeout: T });
      await loc.click({ timeout: T });
      await sleep(400);
      return;
    }
    case "select": {
      const loc = v.testid ? page.getByTestId(v.testid) : page.getByLabel(v.label).first();
      await loc.scrollIntoViewIfNeeded({ timeout: T });
      await loc.selectOption(v.value, { timeout: T });
      await sleep(300);
      return;
    }
    case "fill": {
      const loc = page.getByLabel(v.label).first();
      await loc.scrollIntoViewIfNeeded({ timeout: T });
      await loc.fill(v.value, { timeout: T });
      return;
    }
    case "scroll": {
      const loc = locator(page, v);
      await loc.waitFor({ timeout: T });
      if (v.inner) {
        // only the element's own scroll container moves (the phone frame's screen); the page itself stays where it is
        await loc.evaluate((el: Element) => {
          let a: Element | null = el.parentElement;
          while (a && !(a.scrollHeight > a.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(a).overflowY))) a = a.parentElement;
          if (a) a.scrollTo({ top: a.scrollTop + el.getBoundingClientRect().top - a.getBoundingClientRect().top - 16, behavior: "smooth" });
        });
      } else if (v.align === "top") {
        // the element goes just under the header, so the caption bar at the bottom of the frame cannot cover it
        await loc.evaluate((el: Element) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 150, behavior: "smooth" }));
      } else {
        await loc.evaluate((el: Element) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
      }
      await sleep(900);
      return;
    }
    case "scroll_page":
      await page.evaluate((y: number) => window.scrollTo({ top: y, behavior: "smooth" }), v);
      await sleep(900);
      return;
    case "wait_for": {
      let loc = locator(page, v);
      if (v.contains) loc = loc.filter({ hasText: v.contains });
      const total = v.timeout ?? T;
      if (v.absent) { await loc.first().waitFor({ state: "detached", timeout: total }); return; }
      const deadline = Date.now() + total;
      for (;;) {
        const left = deadline - Date.now();
        const slice = v.reload_every ? Math.min(v.reload_every, Math.max(left, 1)) : left;
        try { await loc.first().waitFor({ state: "visible", timeout: Math.max(slice, 500) }); return; }
        catch (e) {
          if (!v.reload_every || Date.now() >= deadline) throw e;
          await page.reload({ waitUntil: "domcontentloaded" });
          await page.getByTestId("sim-badge").waitFor({ timeout: T });
        }
      }
    }
    case "hold": await sleep(v * 1000); return;
    case "caption": await page.evaluate((t: string) => (window as any).__setCaption?.(t), v); return;
    case "cut": {
      const now = (Date.now() - ctx.t0) / 1000;
      if (v === "start") ctx.marks.open = now;
      else if (ctx.marks.open !== null) { ctx.marks.cuts.push([ctx.marks.open, now]); ctx.marks.open = null; }
      return;
    }
    case "shell": {
      const r = await shAsync(v, 600); // async: a blocked event loop would stall the screencast and shorten the video
      if (r.code !== 0) throw new Error(`shell ${JSON.stringify(v)} exited ${r.code}: ${r.out.slice(-400)}`);
      return;
    }
    case "terminal": {
      const key = v.reuse ?? v.command;
      let res = v.reuse ? terminalCache.get(key) : undefined;
      const lines = v.lines ?? 14;
      const show = async (output: string, ok: boolean | null) => {
        const file = join(PAGES, `term-${++pageSeq}.html`);
        writeFileSync(file, terminalHtml(v.command, output, ok));
        await page.goto(pathToFileURL(file).href);
      };
      if (!res || v.run) {
        // The run itself is dead time on screen and is cut from the video; no empty terminal is shown before it (a viewer
        // would see only the command line). The page that was up stays, then the terminal appears with the real output.
        const started = (Date.now() - ctx.t0) / 1000;
        const r = await shAsync(v.command, v.timeout ?? 120);
        const ended = (Date.now() - ctx.t0) / 1000;
        if (ended - started > 0.5) ctx.marks.cuts.push([started + 0.3, ended]);
        const out = stripAnsi(r.out).trimEnd().split("\n");
        res = { output: out.slice(-lines).join("\n"), ok: r.code === 0 };
        terminalCache.set(key, res);
        if (!res.ok) log(`terminal command failed (exit ${r.code}): ${v.command}`);
      }
      await show(res.output, res.ok);
      await sleep(500);
      return;
    }
    case "card": {
      const file = join(PAGES, `card-${++pageSeq}.html`);
      writeFileSync(file, cardHtml(v));
      await page.goto(pathToFileURL(file).href);
      await sleep(500);
      return;
    }
    case "advance_clock": {
      const before = await page.getByTestId("sim-badge").innerText();
      await page.getByLabel("Days to advance the simulation clock").fill(String(v));
      await page.getByTestId("advance-clock").click({ timeout: T });
      await page.waitForFunction((b: string) => document.querySelector('[data-testid="sim-badge"]')?.textContent !== b, before, { timeout: 90_000 });
      await sleep(500);
      return;
    }
    default: throw new Error(`unknown action ${verb}`);
  }
}

function ffprobeDuration(file: string): number {
  const r = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file], { encoding: "utf-8" });
  return Number.parseFloat(r.stdout.trim());
}

async function probeOptional(browser: any, shot: Shot, cases: Record<string, string>, state: any): Promise<string | null> {
  if (!shot.requires) return null;
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, storageState: state });
  try {
    const page = await ctx.newPage();
    const route = fill(shot.requires.route, cases);
    const res = await page.goto(BASE + route, { waitUntil: "domcontentloaded" });
    if (res && res.status() === 404) return `${route} answered 404`;
    await sleep(1500);
    const path = new URL(page.url()).pathname;
    if (path !== route.split("?")[0]) return `${route} redirects to ${path} (no such route in this build)`;
    if (shot.requires.testid) {
      try { await page.getByTestId(shot.requires.testid).first().waitFor({ state: "visible", timeout: 8000 }); }
      catch { return `${route} has no element ${shot.requires.testid}`; }
    }
    return null;
  } finally {
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------------------------------------------------
async function renderCards(browser: any, sb: Storyboard) {
  mkdirSync(CARDS, { recursive: true });
  const { title, end } = sb.cards;
  const t = pageHtml(`<div class="wrap"><h1><span class="mark">&#10003;</span>${esc(title.heading)}</h1><p class="line">${esc(title.line)}</p><div class="note">${esc(title.note)}</div></div>`);
  const e = pageHtml(`<div class="wrap"><h1><span class="mark">&#10003;</span>${esc(end.heading)}</h1>${end.cta ? `<div class="cta">${esc(end.cta)}</div>` : ""}<p class="line">${esc(end.line)}</p><p class="line">Team: ${esc(end.team)}</p><div class="note">${esc(end.note)}</div></div>`);
  for (const [name, html] of [["title", t], ["end", e]] as const) {
    const file = join(PAGES, `${name}-card.html`);
    writeFileSync(file, html);
    const ctx = await browser.newContext({ viewport: { width: sb.meta.width, height: sb.meta.height } });
    const page = await ctx.newPage();
    await page.goto(pathToFileURL(file).href);
    await page.screenshot({ path: join(CARDS, `${name}.png`) });
    await ctx.close();
  }
  log("cards rendered");
}

async function startPreview(): Promise<ChildProcess> {
  if (!args.has("--no-build")) {
    log("building the web app with the http adapter");
    const r = spawnSync("npx", ["vite", "build", "--outDir", BUILD, "--emptyOutDir"], {
      cwd: join(REPO, "web"), encoding: "utf-8",
      env: { ...process.env, VITE_API_ADAPTER: "http", VITE_API_URL: API },
    });
    if (r.status !== 0) throw new Error(`vite build failed:\n${r.stdout}\n${r.stderr}`);
  }
  try { await fetch(BASE, { signal: AbortSignal.timeout(1500) }); throw new Error(`port ${PORT} is already in use (set VIDEO_WEB_PORT, or stop the other preview)`); }
  catch (e) { if ((e as Error).message.includes("already in use")) throw e; }
  const child = spawn("npx", ["vite", "preview", "--outDir", BUILD, "--port", String(PORT), "--strictPort"], { cwd: join(REPO, "web"), stdio: "ignore" });
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(BASE, { signal: AbortSignal.timeout(1000) }); if (r.ok) return child; } catch { /* not yet */ }
    await sleep(500);
  }
  child.kill();
  throw new Error("vite preview did not start");
}

async function main() {
  const sb = loadStoryboard();
  mkdirSync(PAGES, { recursive: true });
  mkdirSync(RAW, { recursive: true });
  const browser = await chromium.launch({ executablePath: localChromium() });
  if (args.has("--cards-only")) { await renderCards(browser, sb); await browser.close(); return; }

  let preview: ChildProcess | null = null;
  const results: any[] = [];
  let failure: Error | null = null;
  let cases: Record<string, string> = {};
  let simTime = "";
  try {
    await waitForStack();
    // The agency recording run needs a 600 s budget (the API default is 20 s): recreate the API container with the override,
    // and put the default back in `finally`. Production defaults are untouched (docker-compose.yml keeps 20).
    const budget = agencyBudget(process.env.VIDEO_AGENCY_BUDGET);
    const rb = composeApi({ AGENCY_RUN_BUDGET_SECONDS: String(budget) });
    if (rb.code !== 0) throw new Error(`could not set the agency run budget to ${budget} s: ${rb.out.slice(-300)}`);
    log(`agency run budget: ${budget} s (API recreated)`);
    for (let i = 0; i < 40 && (await stackProblem()); i++) await sleep(3000); // the recreated API takes a while to answer
    await waitForStack();
    if (!args.has("--no-reset")) {
      make("demo-reset");
      make("demo-scenario", ["UNTIL=owner_accepted"]);
      make("demo-seed-chronic"); // the urgent long-term-conditions case of shots 2 and 3 (opt-in seed; the final demo-reset removes it)
    }
    cases = await findCases();
    simTime = (await (await retryFetch(`${API}/health`)).json()).sim_time;
    log(`named patient's case ${cases.case}, second case ${cases.case2}, sim time ${simTime}`);
    rmSync(RAW, { recursive: true, force: true });
    mkdirSync(RAW, { recursive: true });
    preview = await startPreview();
    await renderCards(browser, sb);

    let state: any = undefined;
    for (const shot of sb.shots) {
      if (only && !only.includes(shot.id)) continue;
      const entry: any = { id: shot.id, title: shot.title, optional: !!shot.optional, submission: !!shot.submission, target: shot.duration, status: "recorded" };
      if (shot.optional) {
        const reason = await probeOptional(browser, shot, cases, state);
        if (reason) { entry.status = "skipped"; entry.reason = reason; results.push(entry); log(`shot ${shot.id} skipped: ${reason}`); continue; }
      }
      log(`shot ${shot.id}: ${shot.title}`);
      const dir = join(RAW, `shot-${shot.id}`);
      const ctx = await browser.newContext({
        viewport: { width: sb.meta.width, height: sb.meta.height },
        recordVideo: { dir, size: { width: sb.meta.width, height: sb.meta.height } },
        storageState: state,
      });
      await ctx.addInitScript({ content: overlayScript(shot.caption, shot.caption_pos ?? "bottom") });
      const tPage = Date.now();
      const page = await ctx.newPage();
      const marks = { cuts: [] as [number, number][], open: null as number | null };
      const t0 = Date.now();
      let err: Error | null = null;
      try {
        for (const act of shot.actions) {
          await runAction(page, act, { cases, marks, t0, vidOffset: () => 0, timeout: shot.optional ? 8000 : 30_000 });
        }
      } catch (e) {
        err = e as Error;
        try { await page.screenshot({ path: join(RAW, `shot-${shot.id}-failure.png`) }); } catch { /* ignore */ }
      }
      const wall = (Date.now() - t0) / 1000;
      try { state = await ctx.storageState(); } catch { /* keep the previous state */ }
      const video = page.video();
      await ctx.close();
      const src = video ? await video.path() : null;
      if (err) {
        if (shot.optional) { entry.status = "skipped"; entry.reason = `step failed: ${err.message.split("\n")[0]}`; results.push(entry); log(`shot ${shot.id} skipped: ${entry.reason}`); continue; }
        results.push({ ...entry, status: "failed", reason: err.message.split("\n")[0] });
        throw new Error(`shot ${shot.id} (${shot.title}) failed: ${err.message}`);
      }
      const file = join(RAW, `shot-${shot.id}.webm`);
      renameSync(src!, file);
      const dur = ffprobeDuration(file);
      const offset = (t0 - tPage) / 1000; // the video starts when the page opens, slightly before t0
      entry.video = file;
      entry.duration = Number(dur.toFixed(2));
      entry.cuts = marks.cuts.map(([a, b]) => [Number((a + offset).toFixed(2)), Number((b + offset).toFixed(2))]);
      entry.kept = Number((dur - entry.cuts.reduce((s: number, [a, b]: number[]) => s + (b - a), 0)).toFixed(2));
      results.push(entry);
      log(`  recorded ${entry.duration} s (${entry.kept} s after ${entry.cuts.length} cut(s))`);
    }
  } catch (e) {
    failure = e as Error;
  } finally {
    // Whatever happened: the scheduler runs, the preview is gone, the demo is reset.
    const up = sh("docker compose start scheduler", 120);
    log(`scheduler start: ${up.code === 0 ? "ok" : "FAILED " + up.out.slice(-200)}`);
    preview?.kill();
    const rb2 = composeApi({});
    log(`agency run budget back to the default: ${rb2.code === 0 ? "ok" : "FAILED " + rb2.out.slice(-200)}`);
    if (rb2.code !== 0 && !failure) failure = new Error(`could not restore the API's default agency run budget (the API may still run with the video budget): ${rb2.out.slice(-300)}`);
    await browser.close().catch(() => undefined);
    writeFileSync(join(OUT, "shots.json"), JSON.stringify({ generated_at: new Date().toISOString(), sim_time_at_start: simTime, cases, shots: results }, null, 2));
    if (!args.has("--no-reset") && !args.has("--keep-state")) {
      try { make("demo-reset"); } catch (e) { log(`final demo-reset failed: ${(e as Error).message}`); if (!failure) failure = e as Error; }
    }
  }
  if (failure) { console.error(`record: FAILED: ${failure.message}`); process.exit(1); }
  const rec = results.filter((r) => r.status === "recorded").length;
  const skipped = results.filter((r) => r.status === "skipped");
  log(`done: ${rec} shots recorded, ${skipped.length} skipped${skipped.map((s) => ` (shot ${s.id}: ${s.reason})`).join("")}`);
}

await main();
