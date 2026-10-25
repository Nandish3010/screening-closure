/**
 * T-C1: the hand-written contract (src/api/contract.ts) cannot silently diverge from the API's OpenAPI schema.
 *
 * `src/api/generated.ts` and `openapi.json` come from the API (`make api-types`; tests/test_api_types_sync.py fails when they are
 * stale). Here (1) type-level assertions, compiled by `tsc --noEmit` (`npm run typecheck`, part of `npm run build`), tie the request
 * shapes and enums of the contract to the generated ones: when either side changes, tsc fails; (2) a runtime test checks that every
 * path the http adapter calls exists in the schema.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { ContactChannel, DraftChannel, DraftPurpose, DraftRejectBody, DraftRequestBody, Lang, PostEventBody, RejectReason, ReplyBody } from "../src/api/contract";
import type { CreateDraft, DevLogin, PostEvent, PostReply, RejectDraft } from "../src/api/generated";
import { ROUTES } from "../src/api/generated";

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
/** Compile-time only: the argument must be `true`. */
function assertType<_T extends true>(): void {}

// Enums: identical sets on both sides.
// The UI offers only three of the API's six purposes (the others are written by the system), so a subset is the contract.
assertType<[DraftPurpose] extends [CreateDraft["purpose"]] ? true : false>();
assertType<Equal<DraftChannel, CreateDraft["channel"]>>();
assertType<Equal<Lang, CreateDraft["language"]>>();
assertType<Equal<RejectReason, RejectDraft["reason"]>>();
assertType<Equal<ContactChannel, PostReply["channel"]>>();
assertType<Equal<Lang, NonNullable<PostReply["language_hint"]>>>();

// Request bodies: what the UI sends is accepted by the server shape, and the required keys are the same.
const draft: CreateDraft = {} as DraftRequestBody;
const reject: RejectDraft = {} as DraftRejectBody;
const reply: PostReply = {} as ReplyBody;
const event: PostEvent = {} as Omit<PostEventBody, "actor">; // the actor is mock-only: the API rejects a body that has one
assertType<Equal<keyof DraftRequestBody, keyof CreateDraft>>();
assertType<Equal<keyof DraftRejectBody, keyof RejectDraft>>();
assertType<Equal<Exclude<keyof PostEventBody, "actor">, "type" | "payload" | "event_id">>();
assertType<Equal<Exclude<keyof PostEventBody, "actor">, Exclude<keyof PostEvent, "at">>>();
void [draft, reject, reply, event];

function adapterPaths(): string[] {
  const src = readFileSync(resolve(__dirname, "../src/api/httpAdapter.ts"), "utf8");
  const found = new Set<string>();
  for (const m of src.matchAll(/["`](\/[a-z][^"`\s]*)["`]/g)) {
    const raw = m[1]!
      .replace(/\$\{qs\([\s\S]*$/, "") // query string helper
      .replace(/\$\{[^}]*\}/g, "{x}"); // path parameter
    found.add(raw.replace(/\?.*$/, ""));
  }
  return [...found].sort();
}

describe("contract vs OpenAPI (T-C1)", () => {
  it("every path the http adapter calls is in the generated route table", () => {
    const patterns = ROUTES.map((r) => r.split(" ")[1]!).map((p) => new RegExp("^" + p.replace(/\{[^}]+\}/g, "[^/]+") + "$"));
    const paths = adapterPaths();
    expect(paths.length).toBeGreaterThan(10);
    const missing = paths.filter((p) => !patterns.some((re) => re.test(p.replace(/\{x\}/g, "x"))));
    expect(missing).toEqual([]);
  });

  it("openapi.json and generated.ts describe the same routes", () => {
    const spec = JSON.parse(readFileSync(resolve(__dirname, "../src/api/openapi.json"), "utf8")) as { paths: Record<string, Record<string, unknown>> };
    const keys = Object.entries(spec.paths).flatMap(([p, ops]) => Object.keys(ops).map((m) => `${m.toUpperCase()} ${p}`));
    expect([...ROUTES].sort()).toEqual(keys.sort());
  });

  it("DevLogin accepts the body the adapter sends", () => {
    const body: DevLogin = { role: "primary_care_owner", name: "x", provider: "p" };
    expect(Object.keys(body).sort()).toEqual(["name", "provider", "role"]);
  });
});
