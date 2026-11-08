// Fails when the files the browser must fetch before the first screen (the entry script, its modulepreloads, the stylesheet; fonts and
// lazy route chunks are not counted) exceed the budget, gzip sizes. Usage: node scripts/bundle-budget.mjs [dist] [budgetKB]
// Why: on a slow, high-latency link the first screen is bytes-bound (see docs of the cold-load PR); a stray static import of a page or of
// the mock adapter shows up here at once.
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join, resolve } from "node:path";

const dist = resolve(process.argv[2] ?? "dist");
const budgetKB = Number(process.argv[3] ?? process.env.INITIAL_LOAD_BUDGET_KB ?? 120);
const html = readFileSync(join(dist, "index.html"), "utf8");
const urls = [...html.matchAll(/<(?:script|link)\b[^>]*?(?:src|href)="(\/assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]);
if (!urls.length) { console.error("bundle-budget: no /assets/ files found in index.html"); process.exit(2); }
let total = 0;
for (const u of [...new Set(urls)]) {
  const gz = gzipSync(readFileSync(join(dist, u))).length;
  total += gz;
  console.log(`${(gz / 1024).toFixed(1).padStart(7)} KB  ${u}`);
}
const kb = total / 1024;
console.log(`initial load: ${kb.toFixed(1)} KB gzip in ${new Set(urls).size} files (budget ${budgetKB} KB)`);
if (kb > budgetKB) { console.error(`bundle-budget: over budget by ${(kb - budgetKB).toFixed(1)} KB`); process.exit(1); }
