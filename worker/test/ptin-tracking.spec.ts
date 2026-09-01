/**
 * Federal PTIN reminder tracking (2026-09-01, orchestrator directive,
 * Devin-approved). PTIN (IRS Preparer Tax Identification Number) is a
 * single, universal, per-person federal deadline -- always December 31
 * annually, no state variation -- deliberately NOT folded into
 * cpa_deadlines.json's 55-jurisdiction state records (see migration
 * 0074's own docstring and data/ptin_federal.json's _meta for why).
 * Same cross-row-write-by-email convention as notification_mode
 * (digest-mode.spec.ts, which this file mirrors the structure of).
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";
import { nextAnnualMonthEnd } from "../src/deadline";

const BASE = "https://deadline-radar.com";

async function seedUserDate(email: string, stateSlug: string, userDeadline: string, firmId: string | null = null) {
  return store.addPending(env.DB, {
    email,
    stateSlug,
    deadlineFields: {},
    deadlineSource: store.DEADLINE_SOURCE_USER,
    userDeadline,
    firstName: null,
    firmId,
    skipConfirmation: true,
  });
}

async function subscriberCookie(email: string): Promise<string> {
  const { rawSessionToken } = await store.createSubscriberSession(env.DB, store.normalizeEmail(email));
  return `dr_sub_session=${rawSessionToken}`;
}

async function patchPtin(cookie: string | null, enabled: unknown): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (cookie) headers["Cookie"] = cookie;
  return SELF.fetch(`${BASE}/subscriber/ptin`, {
    method: "PATCH",
    headers,
    body: JSON.stringify({ enabled }),
  });
}

describe("PATCH /subscriber/ptin", () => {
  it("401s with no session", async () => {
    expect((await patchPtin(null, true)).status).toBe(401);
  });

  it("rejects a cross-origin request (CSRF)", async () => {
    const email = `ptincsrf-${Date.now()}@example.com`;
    await seedUserDate(email, "ohio", "2027-01-01");
    const cookie = await subscriberCookie(email);
    const resp = await SELF.fetch(`${BASE}/subscriber/ptin`, {
      method: "PATCH",
      headers: { "content-type": "application/json", Cookie: cookie, Origin: "https://attacker.example" },
      body: JSON.stringify({ enabled: true }),
    });
    expect(resp.status).toBe(400);
    for (const row of await store.listSubscriberLicenses(env.DB, email)) {
      expect(row.ptin_tracking_enabled).toBe(0);
    }
  });

  it("rejects a non-boolean enabled value", async () => {
    const email = `ptinbadtype-${Date.now()}@example.com`;
    await seedUserDate(email, "ohio", "2027-01-01");
    const cookie = await subscriberCookie(email);
    const resp = await patchPtin(cookie, "yes");
    expect(resp.status).toBe(400);
  });

  it("404s for a session email with zero subscriber rows", async () => {
    const email = `ptinnorows-${Date.now()}@example.com`;
    // A real session can exist without any subscriber row ever having been
    // created for this email -- createSubscriberSession() only needs an
    // email, not a pre-existing row.
    const cookie = await subscriberCookie(email);
    const resp = await patchPtin(cookie, true);
    expect(resp.status).toBe(404);
  });

  it("sets ptin_tracking_enabled across every row sharing this email, and back off", async () => {
    const email = `ptinpatch-${Date.now()}@example.com`;
    await seedUserDate(email, "ohio", "2027-01-01");
    await seedUserDate(email, "texas", "2027-02-01");
    const cookie = await subscriberCookie(email);

    const setResp = await patchPtin(cookie, true);
    expect(setResp.status).toBe(200);
    const setBody = (await setResp.json()) as { ptin_tracking_enabled: boolean; ptin_next_deadline: string };
    expect(setBody.ptin_tracking_enabled).toBe(true);
    expect(setBody.ptin_next_deadline).toMatch(/^\d{4}-12-31$/);
    for (const row of await store.listSubscriberLicenses(env.DB, email)) {
      expect(row.ptin_tracking_enabled).toBe(1);
    }

    const clearResp = await patchPtin(cookie, false);
    expect(clearResp.status).toBe(200);
    for (const row of await store.listSubscriberLicenses(env.DB, email)) {
      expect(row.ptin_tracking_enabled).toBe(0);
    }
  });

  it("a firm-managed row can be toggled the same way as a free-tier row -- PTIN is not gated by firm_id", async () => {
    const { id: firmId } = await store.createFirm(env.DB, { name: "PTIN Firm Test LLC", adminEmail: `ptinfirmowner-${Date.now()}@example.com` });
    const email = `ptinfirmstaff-${Date.now()}@example.com`;
    await seedUserDate(email, "georgia", "2027-03-01", firmId);
    const cookie = await subscriberCookie(email);

    const resp = await patchPtin(cookie, true);
    expect(resp.status).toBe(200);
    const rows = await store.listSubscriberLicenses(env.DB, email);
    expect(rows.length).toBe(1);
    expect(rows[0]?.ptin_tracking_enabled).toBe(1);
    expect(rows[0]?.firm_id).toBe(firmId);
  });
});

describe("GET /subscriber/licenses -- PTIN fields", () => {
  it("defaults to disabled with a computed next-Dec-31 deadline, before any opt-in", async () => {
    const email = `ptinlist-default-${Date.now()}@example.com`;
    await seedUserDate(email, "ohio", "2027-01-01");
    const cookie = await subscriberCookie(email);

    const resp = await SELF.fetch(`${BASE}/subscriber/licenses`, { headers: { Cookie: cookie } });
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { ptin_tracking_enabled: boolean; ptin_next_deadline: string };
    expect(body.ptin_tracking_enabled).toBe(false);
    expect(body.ptin_next_deadline).toBe(nextAnnualMonthEnd(new Date(), 12).toISOString().slice(0, 10));
  });

  it("reflects an opt-in after PATCH /subscriber/ptin", async () => {
    const email = `ptinlist-after-${Date.now()}@example.com`;
    await seedUserDate(email, "ohio", "2027-01-01");
    const cookie = await subscriberCookie(email);
    await patchPtin(cookie, true);

    const resp = await SELF.fetch(`${BASE}/subscriber/licenses`, { headers: { Cookie: cookie } });
    const body = (await resp.json()) as { ptin_tracking_enabled: boolean };
    expect(body.ptin_tracking_enabled).toBe(true);
  });

  it("does not leak one subscriber's PTIN opt-in to a different email's session", async () => {
    const emailA = `ptiniso-a-${Date.now()}@example.com`;
    const emailB = `ptiniso-b-${Date.now()}@example.com`;
    await seedUserDate(emailA, "ohio", "2027-01-01");
    await seedUserDate(emailB, "texas", "2027-01-01");
    const cookieA = await subscriberCookie(emailA);
    const cookieB = await subscriberCookie(emailB);
    await patchPtin(cookieA, true);

    const respB = await SELF.fetch(`${BASE}/subscriber/licenses`, { headers: { Cookie: cookieB } });
    const bodyB = (await respB.json()) as { ptin_tracking_enabled: boolean };
    expect(bodyB.ptin_tracking_enabled).toBe(false);
  });
});
