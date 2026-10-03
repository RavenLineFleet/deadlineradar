/**
 * PR6 (migration 0085, 2026-10-02, Devin via orchestrator): monthly billing
 * + a 14-day full-feature trial + the per-seat add-on above firm_scale's
 * 35-seat cap. Three things this file proves, mirroring billing.spec.ts's
 * own split:
 *   1. A brand-new signup gets a real, unmetered 14-day trial -- every
 *      paid feature AND an uncapped seat count -- which lapses back to
 *      plain free-tier rules once trial_ends_at passes, with NO separate
 *      expiry code (hasActiveTrial()/trialLiftsSeatCap() are live, derived
 *      checks). A firm that's already on a real paid tier gets ITS tier's
 *      own cap regardless of trial status (entitlements.ts's own
 *      trialLiftsSeatCap() docstring).
 *   2. POST /firm/billing/checkout accepts an `interval` ("annual"/
 *      "monthly"), resolves the matching Stripe Price id, and -- above the
 *      top tier's seat cap -- adds the per-seat add-on as a second line
 *      item, priced off the LIVE roster count, never a client-supplied one.
 *   3. POST /stripe/webhook persists billing_interval from checkout
 *      metadata, defaulting to "annual" for a missing/invalid value.
 *
 * Stripe's own network calls are mocked, same vi.spyOn pattern
 * billing.spec.ts already uses; webhook tests sign their own payload
 * independently, same as that file's signPayload().
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";

function testExecutionContext(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}

async function workerFetch(request: Request, envOverrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...envOverrides } as never, testExecutionContext());
}

async function createFirmWithSession(name: string, adminEmail: string): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function addSecondMember(firmId: string): Promise<void> {
  await store.createFirmMember(env.DB, {
    firmId,
    email: `pr6-second-${Date.now()}-${Math.floor(Math.random() * 1e6)}@examplefirm.com`,
    role: "staff",
    alreadyJoined: true,
  });
}

async function expireTrial(firmId: string): Promise<void> {
  await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2")
    .bind(new Date(Date.now() - 86_400_000).toISOString(), firmId)
    .run();
}

async function getFirmLicenses(cookie: string): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/licenses`, { headers: { Cookie: cookie } });
}

async function postFirmLicense(cookie: string, body: Record<string, string>, ip = "203.0.113.210"): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/licenses`, {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": ip, Cookie: cookie },
    body: JSON.stringify(body),
  });
}

async function getFirmMobilityFirmCoverage(cookie: string): Promise<Response> {
  return SELF.fetch(`${BASE}/firm/mobility/firm-coverage`, { headers: { Cookie: cookie, "cf-connecting-ip": "203.0.113.211" } });
}

describe("PR6 -- 14-day trial", () => {
  it("a brand-new multi-person firm gets Map/Practice Privilege Check during the trial, with no second member needed to unlock it", async () => {
    const { firmId, cookie } = await createFirmWithSession("Trial Firm A", `trial-a-${Date.now()}@example.com`);
    await addSecondMember(firmId); // rules out the separate solo-free exception
    expect((await getFirmMobilityFirmCoverage(cookie)).status).toBe(200);
  });

  it("the same multi-person firm is BLOCKED once its trial has lapsed -- no separate expiry code, just the live date check going false", async () => {
    const { firmId, cookie } = await createFirmWithSession("Trial Firm B", `trial-b-${Date.now()}@example.com`);
    await addSecondMember(firmId);
    expect((await getFirmMobilityFirmCoverage(cookie)).status).toBe(200);
    await expireTrial(firmId);
    expect((await getFirmMobilityFirmCoverage(cookie)).status).toBe(403);
  });

  it("the trial lifts the seat cap entirely -- a brand-new firm can add well past the new-signup 3-seat cap", async () => {
    const { cookie } = await createFirmWithSession("Trial Firm C", `trial-c-${Date.now()}@example.com`);
    for (let i = 0; i < 6; i++) {
      const resp = await postFirmLicense(cookie, {
        staff_label: `Staff ${i}`,
        email: `trial-c-staff-${i}-${Date.now()}@example.com`,
        state_slug: "georgia",
        license_type_id: "ga-individual",
      });
      expect(resp.status, `staff ${i} should be allowed during an active trial`).toBe(201);
    }
    const body = (await (await getFirmLicenses(cookie)).json()) as { seat_cap: number };
    expect(body.seat_cap).toBe(Number.MAX_SAFE_INTEGER);
  });

  it("once the trial lapses, the seat cap reverts to the new-signup default (3) -- existing roster untouched, no further adds", async () => {
    const { firmId, cookie } = await createFirmWithSession("Trial Firm D", `trial-d-${Date.now()}@example.com`);
    for (let i = 0; i < 5; i++) {
      expect((await postFirmLicense(cookie, {
        staff_label: `Staff ${i}`,
        email: `trial-d-staff-${i}-${Date.now()}@example.com`,
        state_slug: "georgia",
        license_type_id: "ga-individual",
      })).status).toBe(201);
    }
    await expireTrial(firmId);
    const capBody = (await (await getFirmLicenses(cookie)).json()) as { seat_cap: number; licenses: unknown[] };
    expect(capBody.seat_cap).toBe(3);
    expect(capBody.licenses.length).toBe(5); // frozen, not force-removed
    const blocked = await postFirmLicense(cookie, {
      staff_label: "One Too Many",
      email: `trial-d-blocked-${Date.now()}@example.com`,
      state_slug: "georgia",
      license_type_id: "ga-individual",
    });
    expect(blocked.status).toBe(402);
  });

  it("a firm already on a real paid tier gets ITS tier's own cap during the trial window, not an unbounded one", async () => {
    const { firmId, cookie } = await createFirmWithSession("Trial Firm E", `trial-e-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter' WHERE id = ?1").bind(firmId).run();
    const body = (await (await getFirmLicenses(cookie)).json()) as { seat_cap: number };
    expect(body.seat_cap).toBe(5); // firm_starter's own cap, trialLiftsSeatCap() excludes any paid tier
  });

  it("GET /firm/licenses discloses trial_ends_at while active, null once expired", async () => {
    const { firmId, cookie } = await createFirmWithSession("Trial Firm F", `trial-f-${Date.now()}@example.com`);
    const active = (await (await getFirmLicenses(cookie)).json()) as { trial_ends_at: string | null };
    expect(active.trial_ends_at).not.toBeNull();
    expect(Date.parse(active.trial_ends_at as string)).toBeGreaterThan(Date.now());

    await expireTrial(firmId);
    const expired = (await (await getFirmLicenses(cookie)).json()) as { trial_ends_at: string | null };
    expect(Date.parse(expired.trial_ends_at as string)).toBeLessThan(Date.now());
  });
});

describe("PR6 -- POST /firm/billing/checkout: interval + per-seat add-on", () => {
  it("defaults to annual when interval is omitted (back-compat with an old/cached client)", async () => {
    const { cookie } = await createFirmWithSession("Interval Firm A", `interval-a-${Date.now()}@example.com`);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_test_1", url: "https://checkout.stripe.com/pay/cs_test_1" }), { status: 200 })
    );
    try {
      const resp = await workerFetch(
        new Request(`${BASE}/firm/billing/checkout`, {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier: "firm_starter" }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_annual_x", STRIPE_PRICE_FIRM_STARTER_MONTHLY: "price_monthly_x" }
      );
      expect(resp.status).toBe(200);
      const [, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const sentBody = (calledInit.body as string) ?? "";
      expect(sentBody).toContain("price_annual_x");
      expect(sentBody).not.toContain("price_monthly_x");
      expect(sentBody).toContain("metadata%5Bbilling_interval%5D=annual");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("interval: 'monthly' resolves the MONTHLY price id, not the annual one", async () => {
    const { cookie } = await createFirmWithSession("Interval Firm B", `interval-b-${Date.now()}@example.com`);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_test_2", url: "https://checkout.stripe.com/pay/cs_test_2" }), { status: 200 })
    );
    try {
      const resp = await workerFetch(
        new Request(`${BASE}/firm/billing/checkout`, {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier: "firm_starter", interval: "monthly" }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_annual_x", STRIPE_PRICE_FIRM_STARTER_MONTHLY: "price_monthly_x" }
      );
      expect(resp.status).toBe(200);
      const [, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const sentBody = (calledInit.body as string) ?? "";
      expect(sentBody).toContain("price_monthly_x");
      expect(sentBody).not.toContain("price_annual_x");
      expect(sentBody).toContain("metadata%5Bbilling_interval%5D=monthly");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("400s on an unrecognised interval", async () => {
    const { cookie } = await createFirmWithSession("Interval Firm C", `interval-c-${Date.now()}@example.com`);
    const resp = await workerFetch(
      new Request(`${BASE}/firm/billing/checkout`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ tier: "firm_starter", interval: "biweekly" }),
      }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_x" }
    );
    expect(resp.status).toBe(400);
  });

  it("monthly checkout 503s cleanly when the monthly price id isn't configured yet (annual still works)", async () => {
    const { cookie } = await createFirmWithSession("Interval Firm D", `interval-d-${Date.now()}@example.com`);
    const resp = await workerFetch(
      new Request(`${BASE}/firm/billing/checkout`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ tier: "firm_starter", interval: "monthly" }),
      }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_annual_x" } // no _MONTHLY var set
    );
    expect(resp.status).toBe(503);
  });

  it("a roster above firm_scale's 35-seat cap can check out on firm_scale WITH the per-seat add-on as a second line item", async () => {
    const { firmId, cookie } = await createFirmWithSession("PerSeat Firm A", `perseat-a-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firmId).run(); // grandfathered cap, so setup itself isn't blocked
    for (let i = 0; i < 40; i++) {
      await store.addPending(env.DB, {
        email: `perseat-a-${i}-${Date.now()}@example.com`,
        stateSlug: "georgia",
        deadlineFields: { license_type_id: "ga-individual" },
        firstName: null,
        deadlineSource: store.DEADLINE_SOURCE_COMPUTED,
        userDeadline: null,
        firmId,
        staffLabel: null,
        skipConfirmation: true,
      });
    }
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_test_3", url: "https://checkout.stripe.com/pay/cs_test_3" }), { status: 200 })
    );
    try {
      const resp = await workerFetch(
        new Request(`${BASE}/firm/billing/checkout`, {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier: "firm_scale" }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_SCALE: "price_scale_x", STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL: "price_addon_x" }
      );
      expect(resp.status, await resp.clone().text()).toBe(200);
      const [, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const sentBody = (calledInit.body as string) ?? "";
      expect(sentBody).toContain("line_items%5B0%5D%5Bprice%5D=price_scale_x");
      expect(sentBody).toContain("line_items%5B1%5D%5Bprice%5D=price_addon_x");
      expect(sentBody).toContain("line_items%5B1%5D%5Bquantity%5D=5"); // 40 - 35
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a roster above 35 seats requesting a SMALLER named tier (not firm_scale) is still refused -- the add-on only ever rides on the top tier", async () => {
    const { firmId, cookie } = await createFirmWithSession("PerSeat Firm B", `perseat-b-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firmId).run();
    for (let i = 0; i < 40; i++) {
      await store.addPending(env.DB, {
        email: `perseat-b-${i}-${Date.now()}@example.com`,
        stateSlug: "georgia",
        deadlineFields: { license_type_id: "ga-individual" },
        firstName: null,
        deadlineSource: store.DEADLINE_SOURCE_COMPUTED,
        userDeadline: null,
        firmId,
        staffLabel: null,
        skipConfirmation: true,
      });
    }
    const resp = await workerFetch(
      new Request(`${BASE}/firm/billing/checkout`, {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ tier: "firm_starter" }),
      }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_x" }
    );
    expect(resp.status).toBe(400);
  });

  it("a roster at or under 35 seats never adds the per-seat line item, even with the add-on price configured", async () => {
    const { cookie } = await createFirmWithSession("PerSeat Firm C", `perseat-c-${Date.now()}@example.com`);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_test_4", url: "https://checkout.stripe.com/pay/cs_test_4" }), { status: 200 })
    );
    try {
      const resp = await workerFetch(
        new Request(`${BASE}/firm/billing/checkout`, {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier: "firm_scale" }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_SCALE: "price_scale_x", STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL: "price_addon_x" }
      );
      expect(resp.status).toBe(200);
      const [, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const sentBody = (calledInit.body as string) ?? "";
      expect(sentBody).not.toContain("line_items%5B1%5D");
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("PR6 -- POST /stripe/webhook: billing_interval persistence", () => {
  const SECRET = "whsec_pr6_test_secret";

  async function signPayload(secret: string, timestampSeconds: number, payload: string): Promise<string> {
    const signedPayload = `${timestampSeconds}.${payload}`;
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sigBuffer = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedPayload));
    const hex = [...new Uint8Array(sigBuffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
    return `t=${timestampSeconds},v1=${hex}`;
  }

  async function postWebhook(payload: string): Promise<Response> {
    const t = Math.floor(Date.now() / 1000);
    const sig = await signPayload(SECRET, t, payload);
    return workerFetch(
      new Request(`${BASE}/stripe/webhook`, { method: "POST", headers: { "content-type": "application/json", "Stripe-Signature": sig }, body: payload }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET }
    );
  }

  it("checkout.session.completed with billing_interval: 'monthly' persists it", async () => {
    const { firmId } = await createFirmWithSession("Webhook Interval A", `whinterval-a-${Date.now()}@example.com`);
    const payload = JSON.stringify({
      id: `evt_pr6_monthly_${firmId}`,
      type: "checkout.session.completed",
      data: { object: { customer: "cus_pr6_1", subscription: "sub_pr6_1", metadata: { firm_id: firmId, target_plan_tier: "firm_growth", billing_interval: "monthly" } } },
    });
    expect((await postWebhook(payload)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.billing_interval).toBe("monthly");
  });

  it("checkout.session.completed with NO billing_interval metadata defaults to 'annual'", async () => {
    const { firmId } = await createFirmWithSession("Webhook Interval B", `whinterval-b-${Date.now()}@example.com`);
    const payload = JSON.stringify({
      id: `evt_pr6_noival_${firmId}`,
      type: "checkout.session.completed",
      data: { object: { customer: "cus_pr6_2", subscription: "sub_pr6_2", metadata: { firm_id: firmId, target_plan_tier: "firm_growth" } } },
    });
    expect((await postWebhook(payload)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.billing_interval).toBe("annual");
  });

  it("checkout.session.completed with a GARBAGE billing_interval value also defaults to 'annual' -- never writes an arbitrary string to the column", async () => {
    const { firmId } = await createFirmWithSession("Webhook Interval C", `whinterval-c-${Date.now()}@example.com`);
    const payload = JSON.stringify({
      id: `evt_pr6_garbage_${firmId}`,
      type: "checkout.session.completed",
      data: { object: { customer: "cus_pr6_3", subscription: "sub_pr6_3", metadata: { firm_id: firmId, target_plan_tier: "firm_growth", billing_interval: "weekly'); DROP TABLE firms;--" } } },
    });
    expect((await postWebhook(payload)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.billing_interval).toBe("annual");
  });

  it("customer.subscription.deleted leaves billing_interval untouched (reverting to free makes it moot, not wrong)", async () => {
    const { firmId } = await createFirmWithSession("Webhook Interval D", `whinterval-d-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_growth', stripe_subscription_id = 'sub_pr6_delete', billing_interval = 'monthly' WHERE id = ?1").bind(firmId).run();
    const payload = JSON.stringify({ id: `evt_pr6_deleted_${firmId}`, type: "customer.subscription.deleted", data: { object: { id: "sub_pr6_delete" } } });
    expect((await postWebhook(payload)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("free");
    expect(firm?.billing_interval).toBe("monthly"); // stale but harmless, nothing reads it once free
  });
});
