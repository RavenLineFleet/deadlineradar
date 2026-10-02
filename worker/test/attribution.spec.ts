/**
 * Privacy-safe source attribution (Orchestrator directive, 2026-10-02,
 * Devin: "measure what drives traffic and signups"). See index.ts's
 * handleAttrBeacon/handleAttrSummary docstring and migrations
 * 0081/0082 for the full design reasoning -- this file verifies the
 * allow-list gate (both routes reject anything off ATTRIBUTION_SRC_
 * ALLOWLIST rather than storing it), the daily-counter UPSERT shape (one
 * row per date+src, incremented -- never one row per beacon), and that a
 * real /subscribe signup records the stored first-touch src on the row.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";

function form(fields: Record<string, string>): string {
  return new URLSearchParams(fields).toString();
}

async function postAttr(src: string | undefined, ip: string): Promise<Response> {
  const body: Record<string, string> = {};
  if (src !== undefined) body.src = src;
  return SELF.fetch(`${BASE}/attr`, {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": ip, Origin: BASE },
    body: JSON.stringify(body),
  });
}

async function getAttrSummary(ip: string): Promise<Response> {
  return SELF.fetch(`${BASE}/attr/summary`, { headers: { "cf-connecting-ip": ip } });
}

async function attrRow(date: string, src: string): Promise<{ hit_count: number } | null> {
  return env.DB.prepare("SELECT hit_count FROM attribution_daily WHERE date = ?1 AND src = ?2")
    .bind(date, src)
    .first<{ hit_count: number }>();
}

describe("POST /attr", () => {
  it("rejects an off-allow-list src and stores nothing", async () => {
    const resp = await postAttr("totally-bogus", "203.0.113.40");
    expect(resp.status).toBe(400);
    const row = await attrRow(new Date().toISOString().slice(0, 10), "totally-bogus");
    expect(row).toBeNull();
  });

  it("rejects a missing src", async () => {
    const resp = await postAttr(undefined, "203.0.113.41");
    expect(resp.status).toBe(400);
  });

  it("accepts a valid src, returns 204, and increments a (date, src) counter rather than inserting a new row per beacon", async () => {
    const ip = "203.0.113.42";
    const today = new Date().toISOString().slice(0, 10);
    const before = await attrRow(today, "em-w1");
    const startCount = before?.hit_count ?? 0;

    const resp1 = await postAttr("em-w1", ip);
    expect(resp1.status).toBe(204);
    const after1 = await attrRow(today, "em-w1");
    expect(after1?.hit_count).toBe(startCount + 1);

    const resp2 = await postAttr("em-w1", ip);
    expect(resp2.status).toBe(204);
    const after2 = await attrRow(today, "em-w1");
    expect(after2?.hit_count).toBe(startCount + 2);
  });

  it("keeps separate counters per src on the same day", async () => {
    const ip = "203.0.113.43";
    const today = new Date().toISOString().slice(0, 10);
    await postAttr("li", ip);
    await postAttr("bs", ip);
    const li = await attrRow(today, "li");
    const bs = await attrRow(today, "bs");
    expect(li?.hit_count).toBeGreaterThanOrEqual(1);
    expect(bs?.hit_count).toBeGreaterThanOrEqual(1);
  });

  it("rejects a cross-origin request (CSRF-style origin check, same posture as /roadmap/vote)", async () => {
    const resp = await SELF.fetch(`${BASE}/attr`, {
      method: "POST",
      headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.44", Origin: "https://evil.example.com" },
      body: JSON.stringify({ src: "em-w1" }),
    });
    expect(resp.status).toBe(400);
  });
});

describe("GET /attr/summary", () => {
  it("reflects what POST /attr recorded", async () => {
    const ip = "203.0.113.45";
    await postAttr("ma", ip);
    const resp = await getAttrSummary(ip);
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { rows: { date: string; src: string; hit_count: number }[] };
    const today = new Date().toISOString().slice(0, 10);
    const row = body.rows.find((r) => r.date === today && r.src === "ma");
    expect(row).toBeTruthy();
    expect(row!.hit_count).toBeGreaterThanOrEqual(1);
  });

  it("excludes rows older than the 90-day window", async () => {
    const oldDate = "2020-01-01";
    await env.DB.prepare(
      "INSERT INTO attribution_daily (date, src, hit_count) VALUES (?1, ?2, 1) ON CONFLICT(date, src) DO UPDATE SET hit_count = hit_count + 1"
    )
      .bind(oldDate, "rd")
      .run();
    const resp = await getAttrSummary("203.0.113.46");
    const body = (await resp.json()) as { rows: { date: string; src: string }[] };
    expect(body.rows.some((r) => r.date === oldDate)).toBe(false);
  });
});

describe("store.addPending -- first_touch_src", () => {
  it("persists a valid firstTouchSrc on the subscriber row", async () => {
    const email = `attr-store-test-${Date.now()}@example.com`;
    const record = await store.addPending(env.DB, {
      email,
      stateSlug: "georgia",
      deadlineFields: { license_type_id: "ga-individual" },
      firstName: null,
      firstTouchSrc: "x",
    });
    expect(record.first_touch_src).toBe("x");
    const row = await env.DB.prepare("SELECT first_touch_src FROM subscribers WHERE email = ?1")
      .bind(email)
      .first<{ first_touch_src: string | null }>();
    expect(row?.first_touch_src).toBe("x");
  });

  it("defaults to null when omitted -- every pre-existing call site keeps working unchanged", async () => {
    const email = `attr-store-test-omitted-${Date.now()}@example.com`;
    const record = await store.addPending(env.DB, {
      email,
      stateSlug: "georgia",
      deadlineFields: { license_type_id: "ga-individual" },
      firstName: null,
    });
    expect(record.first_touch_src).toBeNull();
  });
});

describe("POST /subscribe -- first_touch_src end to end", () => {
  it("a real signup with a valid src field records it on the row", async () => {
    const email = `attr-subscribe-valid-${Date.now()}@example.com`;
    const resp = await SELF.fetch(`${BASE}/subscribe`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": "203.0.113.50" },
      body: form({ email, state: "georgia", license_type_id: "ga-individual", src: "blog", hp_website: "" }),
    });
    expect(resp.status).toBe(200);
    const row = await env.DB.prepare("SELECT first_touch_src FROM subscribers WHERE email = ?1")
      .bind(email)
      .first<{ first_touch_src: string | null }>();
    expect(row?.first_touch_src).toBe("blog");
  });

  it("an off-allow-list src is dropped to null, not stored as-is -- never trust a client-supplied tag past the allow-list", async () => {
    const email = `attr-subscribe-invalid-${Date.now()}@example.com`;
    const resp = await SELF.fetch(`${BASE}/subscribe`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": "203.0.113.51" },
      body: form({ email, state: "georgia", license_type_id: "ga-individual", src: "<script>alert(1)</script>", hp_website: "" }),
    });
    expect(resp.status).toBe(200);
    const row = await env.DB.prepare("SELECT first_touch_src FROM subscribers WHERE email = ?1")
      .bind(email)
      .first<{ first_touch_src: string | null }>();
    expect(row?.first_touch_src).toBeNull();
  });

  it("no src field at all -- row stores null, same as every signup before this build", async () => {
    const email = `attr-subscribe-none-${Date.now()}@example.com`;
    const resp = await SELF.fetch(`${BASE}/subscribe`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded", "cf-connecting-ip": "203.0.113.52" },
      body: form({ email, state: "georgia", license_type_id: "ga-individual", hp_website: "" }),
    });
    expect(resp.status).toBe(200);
    const row = await env.DB.prepare("SELECT first_touch_src FROM subscribers WHERE email = ?1")
      .bind(email)
      .first<{ first_touch_src: string | null }>();
    expect(row?.first_touch_src).toBeNull();
  });
});
