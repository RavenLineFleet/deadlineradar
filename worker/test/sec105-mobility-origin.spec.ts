/**
 * SEC-105 (LOW, 2026-11-19): the 4 mobility check POSTs had no originAllowed()
 * call. They were excluded from the 2026-08-21 CSRF census as read-only, but
 * each calls checkRateLimit() keyed on the session's firm, which writes a
 * rate_limit_hits row. A forged cross-site POST could spend the victim firm's
 * hourly quota. Each case: a cross-origin POST with a body that would succeed
 * is refused and writes no row; the same body from the site's own origin
 * returns 200 and writes one, so the zero means something.
 */
import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";
const EVIL = "https://evil.example";

async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  const ctx = { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
  return worker.fetch(request, env as never, ctx);
}

let seq = 0;
async function firm(): Promise<{ firmId: string; cookie: string }> {
  seq += 1;
  const f = await store.createFirm(env.DB, { name: "SEC-105 LLP", adminEmail: `sec105-${Date.now()}-${seq}@examplefirm.com` });
  await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_scale' WHERE id = ?1").bind(f.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, f.id, f.memberId);
  return { firmId: f.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

function post(path: string, cookie: string, body: unknown, origin: string | null): Promise<Response> {
  seq += 1;
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "cf-connecting-ip": `198.51.105.${(seq % 250) + 1}`,
    Cookie: cookie,
  };
  if (origin) headers.Origin = origin;
  return workerFetch(new Request(`${BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body) }));
}

async function rows(firmId: string): Promise<number> {
  const r = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1").bind(firmId).first<{ n: number }>();
  return r?.n ?? 0;
}

const HOME = { home_state_slug: "colorado", target_state_slug: "texas", service_type: "tax" };
const CASES: Array<[string, unknown]> = [
  ["/firm/mobility/check", HOME],
  ["/firm/mobility/check-batch", HOME],
  ["/firm/mobility/check-roster", { target_state_slug: "texas", service_type: "tax" }],
  ["/firm/mobility/firm-check", { firm_home_state_slug: "california", target_state_slug: "alabama", has_physical_office: false }],
];

describe("SEC-105: mobility check POSTs refuse a cross-site Origin before the victim-keyed rate limit", () => {
  for (const [path, body] of CASES) {
    it(`${path}: foreign Origin -> 400, no quota row; own Origin -> 200, one row`, async () => {
      const f = await firm();
      const refused = await post(path, f.cookie, body, EVIL);
      expect(refused.status).toBe(400);
      expect(((await refused.json()) as { error: string }).error).toContain("Please try again from the Deadline-Radar site");
      expect(await rows(f.firmId)).toBe(0);

      const ok = await post(path, f.cookie, body, BASE);
      expect(ok.status).toBe(200);
      expect(await rows(f.firmId)).toBeGreaterThan(0);
    });
  }
});
