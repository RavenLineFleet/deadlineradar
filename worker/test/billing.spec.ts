/**
 * Paid-tiers + Stripe billing (2026-08-05; entitlement model rewritten
 * 2026-08-06 -- see entitlements.ts's own docstring). Two things this file
 * proves, per the paid-tiers plan's own verification section:
 *   1. POST /firm/billing/checkout rejects a tier smaller than the firm's
 *      live roster, and (with Stripe mocked) creates a real Checkout
 *      Session with the right metadata.
 *   2. POST /stripe/webhook verifies signatures, is idempotent on a
 *      redelivered event.id, and each of the three v1 event branches
 *      (checkout.session.completed / customer.subscription.deleted /
 *      invoice.payment_failed) does what it's supposed to.
 *
 * Stripe's own network calls are never made from tests -- `globalThis.fetch`
 * is mocked for the checkout-happy-path test, same `vi.spyOn` pattern
 * email-allowlist.spec.ts already uses for SendGrid. Webhook tests construct
 * their own valid signature independently (see signPayload() below, same
 * approach as stripe.spec.ts) rather than calling verifyWebhookSignature()
 * to sign its own test data.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { SIG_FAIL_ALERT_ATTEMPTS_PER_DAY } from "../src/subscription_sync";
import { fillBucket, freeOneSlot, slotsUsed } from "./rate-limit-slots";
import type { FirmRow } from "../src/store";

function testExecutionContext(): ExecutionContext {
  return {
    waitUntil() {},
    passThroughOnException() {},
    props: {},
  } as unknown as ExecutionContext;
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

async function setFirmTierAndAge(firmId: string, planTier: string, createdAt: string, status = "active"): Promise<void> {
  await env.DB.prepare("UPDATE firms SET plan_tier = ?1, created_at = ?2, status = ?3 WHERE id = ?4")
    .bind(planTier, createdAt, status, firmId)
    .run();
}

function daysAgoIso(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

async function getFirmLicenses(cookie: string): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/licenses", { headers: { Cookie: cookie } });
}

async function postFirmLicense(cookie: string, body: Record<string, string>): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/licenses", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.200", Cookie: cookie },
    body: JSON.stringify(body),
  });
}

async function getFirmCpe(cookie: string): Promise<Response> {
  return SELF.fetch("https://deadline-radar.com/firm/cpe", { headers: { Cookie: cookie } });
}

async function signPayload(secret: string, timestampSeconds: number, payload: string): Promise<string> {
  const signedPayload = `${timestampSeconds}.${payload}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedPayload));
  const hex = [...new Uint8Array(sigBuffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `t=${timestampSeconds},v1=${hex}`;
}

async function postWebhook(payload: string, sigHeader: string | null, envOverrides: Record<string, unknown>): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (sigHeader) headers["Stripe-Signature"] = sigHeader;
  return workerFetch(
    new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers, body: payload }),
    envOverrides
  );
}

describe("Roster/CPE Hours are a standing free tier -- no expiration, no entitlement gate (2026-08-06)", () => {
  it("GET /firm/licenses: 200 whether the firm is free or on an old (500-day) free-tier account -- no expiration exists to hit", async () => {
    const { firmId, cookie } = await createFirmWithSession("Gate Firm A", `gatea-${Date.now()}@example.com`);
    expect((await getFirmLicenses(cookie)).status).toBe(200);

    await setFirmTierAndAge(firmId, "free", daysAgoIso(500));
    expect((await getFirmLicenses(cookie)).status).toBe(200);
  });

  it("GET /firm/licenses: 200 on every paid firm tier too -- paid tiers grant Map/Practice Privilege Check on top, never take away the free features", async () => {
    for (const tier of ["firm_starter", "firm_growth", "firm_standard", "firm_scale", "firm", "firm_annual", "premium"]) {
      const { firmId, cookie } = await createFirmWithSession(`Gate Firm ${tier}`, `gate-${tier}-${Date.now()}@example.com`);
      await setFirmTierAndAge(firmId, tier, daysAgoIso(500));
      expect((await getFirmLicenses(cookie)).status, `tier "${tier}" should grant access`).toBe(200);
    }
  });

  it("POST /firm/licenses: 201 on a long-standing free-tier firm -- no 402 paywall on roster writes anymore", async () => {
    const { firmId, cookie } = await createFirmWithSession("Gate Firm B", `gateb-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "free", daysAgoIso(500));
    const resp = await postFirmLicense(cookie, { staff_label: "X", email: `x-${Date.now()}@example.com`, state_slug: "georgia", license_type_id: "ga-individual" });
    expect(resp.status).toBe(201);
  });

  it("GET /firm/cpe: 200 on a long-standing free-tier firm and on a paid tier alike", async () => {
    const { firmId, cookie } = await createFirmWithSession("Gate Firm C", `gatec-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "free", daysAgoIso(500));
    expect((await getFirmCpe(cookie)).status).toBe(200);

    await setFirmTierAndAge(firmId, "firm_growth", daysAgoIso(500));
    expect((await getFirmCpe(cookie)).status).toBe(200);
  });

  it("a suspended firm is still denied, free tier or paid -- requireFirmSession() catches this on its own, unrelated to plan_tier", async () => {
    const { firmId, cookie } = await createFirmWithSession("Gate Firm D", `gated-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_standard", daysAgoIso(10), "suspended");
    expect((await getFirmLicenses(cookie)).status).toBe(403);
  });

  it("dashboard-visible seat_cap on GET /firm/licenses is still tier-aware", async () => {
    const { firmId, cookie } = await createFirmWithSession("Gate Firm E", `gatee-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(1));
    const resp = await getFirmLicenses(cookie);
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { seat_cap: number };
    expect(body.seat_cap).toBe(5);
  });
});

describe("POST /firm/billing/checkout", () => {
  it("401s with no session", async () => {
    const resp = await SELF.fetch("https://deadline-radar.com/firm/billing/checkout", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tier: "firm_starter" }),
    });
    expect(resp.status).toBe(401);
  });

  it("503s when Stripe isn't configured (the real state of this test env by default)", async () => {
    const { cookie } = await createFirmWithSession("Checkout Firm A", `checkouta-${Date.now()}@example.com`);
    const resp = await SELF.fetch("https://deadline-radar.com/firm/billing/checkout", {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ tier: "firm_starter" }),
    });
    expect(resp.status).toBe(503);
  });

  it("400s on an unrecognised tier", async () => {
    const { cookie } = await createFirmWithSession("Checkout Firm B", `checkoutb-${Date.now()}@example.com`);
    const resp = await workerFetch(
      new Request("https://deadline-radar.com/firm/billing/checkout", {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ tier: "enterprise" }),
      }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_x" }
    );
    expect(resp.status).toBe(400);
  });

  it("a firm can never buy a tier smaller than its live roster requires", async () => {
    const { firmId, cookie } = await createFirmWithSession("Checkout Firm C", `checkoutc-${Date.now()}@example.com`);
    // Roadmap #151 (2026-08-10): a firm signing up today gets a 3-seat free
    // cap, which would block this test's own setup (6 staff) before it
    // ever reaches the checkout-validation property under test. Backdated
    // to the grandfathered 25-seat cap so setup behaves like it always
    // did -- this test is about checkout tier-vs-roster validation, not
    // about the free-tier seat cap itself.
    await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firmId).run();
    for (let i = 0; i < 6; i++) {
      const created = await postFirmLicense(cookie, {
        staff_label: `Staff ${i}`,
        email: `staff${i}-${Date.now()}@example.com`,
        state_slug: "georgia",
        license_type_id: "ga-individual",
      });
      expect(created.status).toBe(201);
    }
    // 6 staff exceeds Essentials' 5-seat cap -- checkout for Essentials must
    // be refused even though the caller can technically pay for it. Under
    // the 2026-08-09 4-band re-tier, 6 staff's minimum tier is Growth
    // (10-seat cap), not Professional (was Growth's own label pre-retier).
    const resp = await workerFetch(
      new Request("https://deadline-radar.com/firm/billing/checkout", {
        method: "POST",
        headers: { "content-type": "application/json", Cookie: cookie },
        body: JSON.stringify({ tier: "firm_starter" }),
      }),
      { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_x" }
    );
    expect(resp.status).toBe(400);
    const body = (await resp.json()) as { error: string };
    expect(body.error).toMatch(/growth/i);
  });

  it("happy path: creates a real Checkout Session (Stripe mocked) with firm_id/target_plan_tier metadata", async () => {
    const { firmId, cookie } = await createFirmWithSession("Checkout Firm D", `checkoutd-${Date.now()}@example.com`);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_test_123", url: "https://checkout.stripe.com/pay/cs_test_123" }), { status: 200 })
    );
    try {
      const resp = await workerFetch(
        new Request("https://deadline-radar.com/firm/billing/checkout", {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier: "firm_starter" }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_starter_x" }
      );
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { checkout_url: string };
      expect(body.checkout_url).toBe("https://checkout.stripe.com/pay/cs_test_123");

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [calledUrl, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(calledUrl).toBe("https://api.stripe.com/v1/checkout/sessions");
      const sentBody = (calledInit.body as string) ?? "";
      expect(sentBody).toContain(`metadata%5Bfirm_id%5D=${firmId}`);
      expect(sentBody).toContain("metadata%5Btarget_plan_tier%5D=firm_starter");
      expect(sentBody).toContain("price_starter_x");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  // AuditLab BILL-8 (LOW-MEDIUM, 2026-08-14): checkout never checked whether
  // the firm already had a subscription -- Stripe permits multiple active
  // subscriptions per customer, and the webhook's single-valued
  // stripe_subscription_id column would silently overwrite the first,
  // orphaning it while it kept billing. Not reachable through the dashboard
  // UI (it only renders checkout buttons for a firm with no known paid
  // tier), but nothing server-side stopped a stale second tab.
  it("BILL-8: refuses checkout for a firm that already has a subscription on record, no Stripe call made", async () => {
    const { firmId, cookie } = await createFirmWithSession("Checkout Firm E", `checkoute-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id = ?1 WHERE id = ?2").bind("sub_existing_123", firmId).run();

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_should_not_happen", url: "https://checkout.stripe.com/pay/cs_should_not_happen" }), { status: 200 })
    );
    try {
      const resp = await workerFetch(
        new Request("https://deadline-radar.com/firm/billing/checkout", {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier: "firm_starter" }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_x" }
      );
      expect(resp.status).toBe(400);
      const body = (await resp.json()) as { error: string; code?: string };
      expect(body.error).toMatch(/already have an active subscription/i);
      expect(body.code).toBe("already_subscribed");
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  // AuditLab RL-5 (2026-08-06, MEDIUM): this route had ZERO checkRateLimit
  // calls -- unlike its sibling cancel/resume below, an unbounded client
  // (compromised session, retry-looping bug) could hit Stripe under the one
  // shared secret key with no limit. Body is an unrecognised tier (400
  // before the fix, if the request ever got that far) so no Stripe call is
  // ever made -- the rate-limit check now sits before body parsing entirely.
  it("RL-5: rate-limited per firm (was completely unbounded)", async () => {
    const { firmId, cookie } = await createFirmWithSession("Checkout RL Firm", `checkout-rl-${Date.now()}@example.com`);
    const checkout = (tier: string) =>
      workerFetch(
        new Request("https://deadline-radar.com/firm/billing/checkout", {
          method: "POST",
          headers: { "content-type": "application/json", Cookie: cookie },
          body: JSON.stringify({ tier }),
        }),
        { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_PRICE_FIRM_STARTER: "price_x" }
      );
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "cs_test_rl", url: "https://checkout.stripe.com/pay/cs_test_rl" }), { status: 200 })
    );
    try {
      await fillBucket(firmId, "firm_billing_checkout", 10);
      // SEC-70/71: a tier refusal spends nothing, so it is still a 400 with the bucket full.
      expect((await checkout("enterprise")).status).toBe(400);
      expect((await checkout("firm_starter")).status).toBe(429);
      expect(fetchSpy).not.toHaveBeenCalled();
      await freeOneSlot(firmId, "firm_billing_checkout");
      expect((await checkout("firm_starter")).status).toBe(200);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(await slotsUsed(firmId, "firm_billing_checkout")).toBe(10);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /firm/billing/cancel and /firm/billing/resume (2026-08-05, self-serve cancellation)", () => {
  it("401s with no session", async () => {
    expect((await SELF.fetch("https://deadline-radar.com/firm/billing/cancel", { method: "POST" })).status).toBe(401);
    expect((await SELF.fetch("https://deadline-radar.com/firm/billing/resume", { method: "POST" })).status).toBe(401);
  });

  it("400s for a long-standing free-tier firm with nothing to cancel -- billing management has no entitlement gate anymore (2026-08-06)", async () => {
    // Billing self-management (cancel/resume) is one of the routes that
    // moved off requireFirmSessionAndEntitlement() entirely -- it's not a
    // paid FEATURE, it's account management, and a free-tier firm must be
    // able to reach it (if only to discover it has nothing to cancel).
    const { firmId, cookie } = await createFirmWithSession("Cancel Free Firm", `cancelfree-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "free", daysAgoIso(500));
    const resp = await workerFetch(
      new Request("https://deadline-radar.com/firm/billing/cancel", { method: "POST", headers: { Cookie: cookie } }),
      { STRIPE_SECRET_KEY: "sk_test_x" }
    );
    expect(resp.status).toBe(400);
  });

  it("400s when Origin doesn't match -- same CSRF defense-in-depth as every other mutating firm route", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cancel CSRF Firm", `cancelcsrf-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(10));
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id = ?1 WHERE id = ?2").bind("sub_csrf_test", firmId).run();
    const resp = await SELF.fetch("https://deadline-radar.com/firm/billing/cancel", {
      method: "POST",
      headers: { Cookie: cookie, Origin: "https://attacker.example" },
    });
    expect(resp.status).toBe(400);
  });

  it("503s when Stripe isn't configured", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cancel No Stripe Firm", `cancelnostripe-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(10));
    const resp = await SELF.fetch("https://deadline-radar.com/firm/billing/cancel", { method: "POST", headers: { Cookie: cookie } });
    expect(resp.status).toBe(503);
  });

  it("400s a paid firm with no stripe_subscription_id on record", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cancel No Sub Firm", `cancelnosub-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(10));
    const resp = await workerFetch(
      new Request("https://deadline-radar.com/firm/billing/cancel", { method: "POST", headers: { Cookie: cookie } }),
      { STRIPE_SECRET_KEY: "sk_test_x" }
    );
    expect(resp.status).toBe(400);
  });

  it("happy path: cancel sets cancel_at_period_end (Stripe mocked), plan_tier is UNCHANGED", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cancel Happy Firm", `cancelhappy-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(10));
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id = ?1 WHERE id = ?2").bind("sub_happy_test", firmId).run();
    const periodEndUnix = Math.floor(Date.now() / 1000) + 20 * 86400; // ~20 days out
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      // Real Stripe response shape (2026-08-05, confirmed against a live
      // test-mode call): current_period_end lives per subscription-item,
      // not at the top level.
      new Response(
        JSON.stringify({ id: "sub_happy_test", cancel_at_period_end: true, items: { data: [{ current_period_end: periodEndUnix }] } }),
        { status: 200 }
      )
    );
    try {
      const resp = await workerFetch(
        new Request("https://deadline-radar.com/firm/billing/cancel", { method: "POST", headers: { Cookie: cookie } }),
        { STRIPE_SECRET_KEY: "sk_test_x" }
      );
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { cancel_at_period_end: boolean; current_period_end: string };
      expect(body.cancel_at_period_end).toBe(true);
      expect(body.current_period_end).toBe(new Date(periodEndUnix * 1000).toISOString());

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [calledUrl, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(calledUrl).toBe("https://api.stripe.com/v1/subscriptions/sub_happy_test");
      expect((calledInit.body as string) ?? "").toContain("cancel_at_period_end=true");

      // plan_tier must NOT have moved -- access continues to period end,
      // only Stripe's own customer.subscription.deleted webhook (at the
      // real period end) is allowed to touch plan_tier.
      const firm = await store.getFirmById(env.DB, firmId);
      expect(firm?.plan_tier).toBe("firm_starter");
      expect(firm?.cancel_at_period_end).toBe(1);
      expect(firm?.current_period_end).toBe(new Date(periodEndUnix * 1000).toISOString());
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("resume: same toggle with cancel_at_period_end=false, clears the local flag", async () => {
    const { firmId, cookie } = await createFirmWithSession("Resume Happy Firm", `resumehappy-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_growth", daysAgoIso(10));
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id = ?1, cancel_at_period_end = 1, current_period_end = ?2 WHERE id = ?3")
      .bind("sub_resume_test", new Date().toISOString(), firmId)
      .run();
    const periodEndUnix = Math.floor(Date.now() / 1000) + 25 * 86400;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ id: "sub_resume_test", cancel_at_period_end: false, items: { data: [{ current_period_end: periodEndUnix }] } }),
        { status: 200 }
      )
    );
    try {
      const resp = await workerFetch(
        new Request("https://deadline-radar.com/firm/billing/resume", { method: "POST", headers: { Cookie: cookie } }),
        { STRIPE_SECRET_KEY: "sk_test_x" }
      );
      expect(resp.status).toBe(200);
      const body = (await resp.json()) as { cancel_at_period_end: boolean };
      expect(body.cancel_at_period_end).toBe(false);

      const [, calledInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect((calledInit.body as string) ?? "").toContain("cancel_at_period_end=false");

      const firm = await store.getFirmById(env.DB, firmId);
      expect(firm?.plan_tier).toBe("firm_growth"); // untouched throughout
      expect(firm?.cancel_at_period_end).toBe(0);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a Stripe API error surfaces as 502, not a raw 500", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cancel Stripe Error Firm", `cancelerr-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(10));
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id = ?1 WHERE id = ?2").bind("sub_err_test", firmId).run();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { message: "No such subscription" } }), { status: 404 })
    );
    try {
      const resp = await workerFetch(
        new Request("https://deadline-radar.com/firm/billing/cancel", { method: "POST", headers: { Cookie: cookie } }),
        { STRIPE_SECRET_KEY: "sk_test_x" }
      );
      expect(resp.status).toBe(502);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("is rate-limited per firm", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cancel Rate Limit Firm", `cancelrl-${Date.now()}@example.com`);
    await setFirmTierAndAge(firmId, "firm_starter", daysAgoIso(10));
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id = ?1 WHERE id = ?2").bind("sub_rl_test", firmId).run();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ id: "sub_rl_test", cancel_at_period_end: true, items: { data: [{ current_period_end: Math.floor(Date.now() / 1000) }] } }),
        { status: 200 }
      )
    );
    try {
      let sawA429 = false;
      for (let i = 0; i < 15; i++) {
        const resp = await workerFetch(
          new Request("https://deadline-radar.com/firm/billing/cancel", { method: "POST", headers: { Cookie: cookie } }),
          { STRIPE_SECRET_KEY: "sk_test_x" }
        );
        if (resp.status === 429) {
          sawA429 = true;
          break;
        }
      }
      expect(sawA429, "expected a 429 within RATE_LIMIT_FIRM_BILLING_CANCEL's ceiling (10/hour)").toBe(true);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe("POST /stripe/webhook", () => {
  const SECRET = "whsec_test_secret";

  it("400s on a missing/invalid signature -- body is never trusted", async () => {
    const payload = JSON.stringify({ id: "evt_bad", type: "checkout.session.completed", livemode: false, data: { object: {} } });
    const resp = await postWebhook(payload, null, { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET });
    expect(resp.status).toBe(400);
  });

  it("checkout.session.completed flips the firm onto the paid tier and stores Stripe ids", async () => {
    const { firmId } = await createFirmWithSession("Webhook Firm A", `webhooka-${Date.now()}@example.com`);
    const payload = JSON.stringify({
      id: `evt_checkout_${firmId}`,
      type: "checkout.session.completed", livemode: false,
      data: {
        object: {
          customer: "cus_test_1",
          subscription: "sub_test_1",
          metadata: { firm_id: firmId, target_plan_tier: "firm_growth" },
        },
      },
    });
    const t = Math.floor(Date.now() / 1000);
    const sig = await signPayload(SECRET, t, payload);
    const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET });
    expect(resp.status).toBe(200);

    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_growth");
    expect(firm?.stripe_customer_id).toBe("cus_test_1");
    expect(firm?.stripe_subscription_id).toBe("sub_test_1");
  });

  it("is idempotent: a redelivered event.id does not re-process (ledger row stays processed once)", async () => {
    const { firmId } = await createFirmWithSession("Webhook Firm B", `webhookb-${Date.now()}@example.com`);
    const eventId = `evt_dup_${firmId}`;
    const payload = JSON.stringify({
      id: eventId,
      type: "checkout.session.completed", livemode: false,
      data: {
        object: {
          customer: "cus_test_2",
          subscription: "sub_test_2",
          metadata: { firm_id: firmId, target_plan_tier: "firm_standard" },
        },
      },
    });
    const t = Math.floor(Date.now() / 1000);
    const sig = await signPayload(SECRET, t, payload);
    const envOverrides = { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET };

    const first = await postWebhook(payload, sig, envOverrides);
    expect(first.status).toBe(200);
    const second = await postWebhook(payload, sig, envOverrides);
    expect(second.status).toBe(200);

    const rows = await env.DB.prepare("SELECT COUNT(*) AS n FROM stripe_webhook_events WHERE id = ?1").bind(eventId).first<{ n: number }>();
    expect(rows?.n).toBe(1);

    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_standard");
  });

  // BILL-15 (AuditLab, 2026-08-29): recordWebhookEventIfNew() used to swallow
  // EVERY error as "already processed" -- a transient D1 failure was
  // indistinguishable from a real duplicate, the caller skipped processing,
  // and still returned 200 so Stripe never retried. These two tests prove
  // the fix distinguishes them: a genuine PK conflict still returns false
  // (against the REAL D1 error text, not a guessed one), and anything else
  // re-throws so the outer /stripe/webhook handler's catch (index.ts) turns
  // it into a non-2xx Stripe will retry.
  it("recordWebhookEventIfNew returns false on a REAL duplicate-id PK conflict (matches D1's actual error text)", async () => {
    const eventId = `evt_pk_conflict_${Date.now()}`;
    const first = await store.recordWebhookEventIfNew(env.DB, eventId, "checkout.session.completed", null);
    expect(first).toBe(true);
    const second = await store.recordWebhookEventIfNew(env.DB, eventId, "checkout.session.completed", null);
    expect(second).toBe(false);
  });

  it("recordWebhookEventIfNew re-throws a non-constraint D1 failure instead of treating it as a duplicate", async () => {
    const failingDb = {
      prepare: () => ({
        bind: () => ({
          run: () => {
            throw new Error("D1_ERROR: storage caused object to be reset because its code was updated.");
          },
        }),
      }),
    } as unknown as D1Database;

    await expect(
      store.recordWebhookEventIfNew(failingDb, `evt_transient_${Date.now()}`, "checkout.session.completed", null)
    ).rejects.toThrow(/storage caused object to be reset/);
  });

  it("a transient (non-constraint) D1 failure during webhook processing surfaces as a non-2xx, not a silently-dropped 200", async () => {
    const { firmId } = await createFirmWithSession("Webhook Firm E", `webhooke-${Date.now()}@example.com`);
    const eventId = `evt_transient_http_${firmId}`;
    const payload = JSON.stringify({
      id: eventId,
      type: "checkout.session.completed", livemode: false,
      data: {
        object: {
          customer: "cus_test_transient",
          subscription: "sub_test_transient",
          metadata: { firm_id: firmId, target_plan_tier: "firm_standard" },
        },
      },
    });
    const t = Math.floor(Date.now() / 1000);
    const sig = await signPayload(SECRET, t, payload);

    const spy = vi.spyOn(store, "recordWebhookEventIfNew").mockRejectedValueOnce(new Error("D1_ERROR: network error"));
    try {
      const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET });
      expect(resp.status).not.toBe(200);
      // Not just "not 200" -- the firm must genuinely be unmodified, not
      // silently marked processed while the plan change never applied.
      const firm = await store.getFirmById(env.DB, firmId);
      expect(firm?.plan_tier).not.toBe("firm_standard");
    } finally {
      spy.mockRestore();
    }
  });

  it("customer.subscription.deleted reverts the firm to the free tier and clears the subscription id", async () => {
    const { firmId } = await createFirmWithSession("Webhook Firm C", `webhookc-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter', stripe_customer_id = 'cus_test_3', stripe_subscription_id = 'sub_test_3' WHERE id = ?1")
      .bind(firmId)
      .run();

    const payload = JSON.stringify({
      id: `evt_cancel_${firmId}`,
      type: "customer.subscription.deleted", livemode: false,
      data: { object: { id: "sub_test_3" } },
    });
    const t = Math.floor(Date.now() / 1000);
    const sig = await signPayload(SECRET, t, payload);
    const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET });
    expect(resp.status).toBe(200);

    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("free");
    expect(firm?.stripe_subscription_id).toBeNull();
    // Customer id is retained -- a future checkout should reuse the same
    // Stripe Customer rather than creating a duplicate.
    expect(firm?.stripe_customer_id).toBe("cus_test_3");
  });

  it("invoice.payment_failed is acknowledged without touching plan_tier -- Stripe's own dunning cycle owns the grace period", async () => {
    const { firmId } = await createFirmWithSession("Webhook Firm D", `webhookd-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_standard' WHERE id = ?1").bind(firmId).run();

    const payload = JSON.stringify({
      id: `evt_failed_${firmId}`,
      type: "invoice.payment_failed", livemode: false,
      data: { object: { id: "in_test_1" } },
    });
    const t = Math.floor(Date.now() / 1000);
    const sig = await signPayload(SECRET, t, payload);
    const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET });
    expect(resp.status).toBe(200);

    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_standard");
  });

  it("503s when webhook secret isn't configured", async () => {
    const payload = JSON.stringify({ id: "evt_noconfig", type: "checkout.session.completed", livemode: false, data: { object: {} } });
    const resp = await postWebhook(payload, "t=1,v1=deadbeef", { STRIPE_SECRET_KEY: "sk_test_x" });
    expect(resp.status).toBe(503);
  });
});

describe("SEC-86 (AuditLab 2026-11-12, HIGH): an event's livemode must match the deployment's key mode", () => {
  const SECRET = "whsec_test_secret";

  async function checkoutEvent(firmId: string, eventId: string, livemode: unknown): Promise<string> {
    const event: Record<string, unknown> = {
      id: eventId,
      type: "checkout.session.completed",
      data: { object: { customer: "cus_sec86", subscription: "sub_sec86", metadata: { firm_id: firmId, target_plan_tier: "firm_growth" } } },
    };
    if (livemode !== "omit") event.livemode = livemode;
    return JSON.stringify(event);
  }

  async function ledgerRows(eventId: string): Promise<number> {
    const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM stripe_webhook_events WHERE id = ?1").bind(eventId).first<{ n: number }>();
    return row?.n ?? 0;
  }

  const rejected: Array<[string, string, unknown]> = [
    ["a validly-signed TEST event against a LIVE key", "sk_live_x", false],
    ["a validly-signed TEST event against a LIVE restricted key", "rk_live_x", false],
    ["a LIVE event against a TEST key", "sk_test_x", true],
    ["an event with no livemode field", "sk_live_x", "omit"],
    ["an event whose livemode is a string, not a boolean", "sk_live_x", "false"],
    ["any event when the key's mode can't be told from its prefix", "pk_live_x", true],
  ];
  for (const [label, key, livemode] of rejected) {
    it(`400s ${label}, writes no plan_tier and no ledger row`, async () => {
      const { firmId } = await createFirmWithSession(`SEC86 ${label}`, `sec86-${Math.random().toString(36).slice(2)}@example.com`);
      const eventId = `evt_sec86_${firmId}`;
      const payload = await checkoutEvent(firmId, eventId, livemode);
      const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: key, STRIPE_WEBHOOK_SECRET: SECRET });
      expect(resp.status).toBe(400);
      expect(((await resp.json()) as { error: string }).error).toBe("Event mode doesn't match this deployment.");
      const firm = await store.getFirmById(env.DB, firmId);
      expect(firm?.plan_tier).toBe("free");
      expect(firm?.stripe_subscription_id ?? null).toBeNull();
      expect(await ledgerRows(eventId)).toBe(0);
    });
  }

  const accepted: Array<[string, string, boolean]> = [
    ["a LIVE event against a LIVE key", "sk_live_x", true],
    ["a LIVE event against a LIVE restricted key", "rk_live_x", true],
    ["a TEST event against a TEST key", "sk_test_x", false],
  ];
  for (const [label, key, livemode] of accepted) {
    it(`accepts ${label} (positive control: same payload shape, mode matches)`, async () => {
      const { firmId } = await createFirmWithSession(`SEC86 ok ${label}`, `sec86ok-${Math.random().toString(36).slice(2)}@example.com`);
      const eventId = `evt_sec86ok_${firmId}`;
      const payload = await checkoutEvent(firmId, eventId, livemode);
      const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: key, STRIPE_WEBHOOK_SECRET: SECRET });
      expect(resp.status).toBe(200);
      const firm = await store.getFirmById(env.DB, firmId);
      expect(firm?.plan_tier).toBe("firm_growth");
      expect(await ledgerRows(eventId)).toBe(1);
    });
  }

  describe("BILL-69 (AuditLab 2026-11-12, MEDIUM): the mode-mismatch exit is no longer silent", () => {
    const modeRowId = () => `stripe_mode_reject:${new Date().toISOString().slice(0, 10)}`;
    async function modeRows(): Promise<number> {
      return ledgerRows(modeRowId());
    }
    async function clearModeRow(): Promise<void> {
      await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id = ?1").bind(modeRowId()).run();
    }

    it("3 test-mode events on a live key: each 400s and logs, exactly one daily marker row, no email, no event row", async () => {
      await clearModeRow();
      const warnSpy = vi.spyOn(console, "warn");
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      try {
        for (let i = 0; i < 3; i++) {
          const { firmId } = await createFirmWithSession(`BILL69 ${i}`, `bill69-${Math.random().toString(36).slice(2)}@example.com`);
          const eventId = `evt_bill69_${firmId}`;
          const payload = await checkoutEvent(firmId, eventId, false);
          const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
          const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: "sk_live_x", STRIPE_WEBHOOK_SECRET: SECRET, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "webhookSignatureFailureAlert" });
          expect(resp.status).toBe(400);
          expect(((await resp.json()) as { error: string }).error).toBe("Event mode doesn't match this deployment.");
          expect(await ledgerRows(eventId)).toBe(0);
        }
        expect(warnSpy.mock.calls.filter((c) => String(c[0]).includes("event mode MISMATCH")).length).toBe(3);
        expect(await modeRows()).toBe(1);
        expect(fetchSpy.mock.calls.filter((c) => String(c[0] instanceof Request ? c[0].url : c[0]).includes("resend")).length).toBe(0);
      } finally {
        warnSpy.mockRestore();
        fetchSpy.mockRestore();
      }
    });

    it("positive control: a mode-matched event leaves no mode-reject row", async () => {
      await clearModeRow();
      const { firmId } = await createFirmWithSession("BILL69 ok", `bill69ok-${Math.random().toString(36).slice(2)}@example.com`);
      const payload = await checkoutEvent(firmId, `evt_bill69ok_${firmId}`, true);
      const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const resp = await postWebhook(payload, sig, { STRIPE_SECRET_KEY: "sk_live_x", STRIPE_WEBHOOK_SECRET: SECRET });
      expect(resp.status).toBe(200);
      expect(await modeRows()).toBe(0);
    });

    it("the daily marker insert is a no-op on repeat, not a thrown constraint error (RL-11 LOW)", async () => {
      const id = `stripe_mode_reject:test-${Math.random().toString(36).slice(2)}`;
      expect(await store.recordDailyWebhookMarkerIfNew(env.DB, id, "stripe_mode_reject")).toBe(true);
      expect(await store.recordDailyWebhookMarkerIfNew(env.DB, id, "stripe_mode_reject")).toBe(false);
      expect(await store.recordDailyWebhookMarkerIfNew(env.DB, id, "stripe_mode_reject")).toBe(false);
      expect(await ledgerRows(id)).toBe(1);
    });
  });

  describe("BILL-69 widening + SEC-90 remedy 1 (2026-11-12): the other silent webhook exits now leave a trace", () => {
    const day = () => new Date().toISOString().slice(0, 10);
    async function clearRow(id: string): Promise<void> {
      await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id = ?1").bind(id).run();
    }
    const TEST_ENV = { STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET };

    it("a signed non-JSON body and a signed event with no type each 400 as before, warn, and leave one stripe_drop row", async () => {
      const rowId = `stripe_drop:${day()}`;
      await clearRow(rowId);
      const warnSpy = vi.spyOn(console, "warn");
      try {
        const t = Math.floor(Date.now() / 1000);
        const notJson = "not json {";
        let resp = await postWebhook(notJson, await signPayload(SECRET, t, notJson), TEST_ENV);
        expect(resp.status).toBe(400);
        expect(((await resp.json()) as { error: string }).error).toBe("Invalid payload.");
        const noType = JSON.stringify({ id: "evt_drop_notype", livemode: false, data: { object: {} } });
        resp = await postWebhook(noType, await signPayload(SECRET, t, noType), TEST_ENV);
        expect(resp.status).toBe(400);
        expect(((await resp.json()) as { error: string }).error).toBe("Invalid event.");
        expect(warnSpy.mock.calls.filter((c) => String(c[0]).includes("signed event DROPPED")).length).toBe(2);
        expect(await ledgerRows(rowId)).toBe(1);
        expect(await ledgerRows("evt_drop_notype")).toBe(0);
      } finally {
        warnSpy.mockRestore();
      }
    });

    it("SEC-90: updateFirmBilling throwing after the ledger insert returns 400, logs the error text, and leaves one stripe_handler_throw row", async () => {
      const rowId = `stripe_handler_throw:${day()}`;
      await clearRow(rowId);
      const { firmId } = await createFirmWithSession("SEC90 throw", `sec90-${Math.random().toString(36).slice(2)}@example.com`);
      const eventId = `evt_sec90_${firmId}`;
      const payload = await checkoutEvent(firmId, eventId, false);
      const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const errSpy = vi.spyOn(console, "error");
      const billSpy = vi.spyOn(store, "updateFirmBilling").mockRejectedValueOnce(new Error("D1_ERROR: sec90 transient"));
      try {
        const resp = await postWebhook(payload, sig, TEST_ENV);
        expect(resp.status).toBe(400);
        expect(((await resp.json()) as { error: string }).error).toBe("Webhook processing failed.");
        expect(billSpy).toHaveBeenCalledTimes(1);
        expect(errSpy.mock.calls.filter((c) => String(c[0]).includes("handler threw") && String(c[0]).includes("sec90 transient")).length).toBe(1);
        expect(await ledgerRows(rowId)).toBe(1);
        // Remedy 1 makes the loss visible; remedy 3 (below) repairs it.
        expect(await ledgerRows(eventId)).toBe(1);
        // SEC-92: the per-event record names the lost event.
        expect(await ledgerRows(`${eventId}:throw`)).toBe(1);
      } finally {
        billSpy.mockRestore();
        errSpy.mockRestore();
      }
    });

    describe("SEC-90 remedy 3: processed_at is the authority, so a redelivery repairs a run that threw", () => {
      async function processedAt(id: string): Promise<string | null | undefined> {
        const row = await env.DB.prepare("SELECT processed_at FROM stripe_webhook_events WHERE id = ?1").bind(id).first<{ processed_at: string | null }>();
        return row === null ? undefined : row.processed_at;
      }
      async function backdate(id: string, msAgo: number): Promise<void> {
        await env.DB.prepare("UPDATE stripe_webhook_events SET received_at = ?1 WHERE id = ?2")
          .bind(new Date(Date.now() - msAgo).toISOString(), id)
          .run();
      }

      it("throw -> in-lease redelivery 409s (Stripe keeps retrying, nothing re-run) -> after the lease the redelivery writes the tier, 200, processed, one reclaim row", async () => {
        const { firmId } = await createFirmWithSession("SEC90 r3", `sec90r3-${Math.random().toString(36).slice(2)}@example.com`);
        const eventId = `evt_sec90r3_${firmId}`;
        const payload = await checkoutEvent(firmId, eventId, false);
        const sig = () => signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
        const billSpy = vi.spyOn(store, "updateFirmBilling").mockRejectedValueOnce(new Error("D1_ERROR: sec90 r3"));
        try {
          expect((await postWebhook(payload, await sig(), TEST_ENV)).status).toBe(400);
          expect(await processedAt(eventId)).toBeNull();
          expect((await store.getFirmById(env.DB, firmId))?.plan_tier).not.toBe("firm_growth");

          const early = await postWebhook(payload, await sig(), TEST_ENV);
          expect(early.status).toBe(409);
          expect(billSpy).toHaveBeenCalledTimes(1); // in-lease: body not re-run
          expect(await processedAt(eventId)).toBeNull();

          await backdate(eventId, store.WEBHOOK_RECLAIM_AFTER_MS + 1000);
          const late = await postWebhook(payload, await sig(), TEST_ENV);
          expect(late.status).toBe(200);
          expect(billSpy).toHaveBeenCalledTimes(2);
          const firm = await store.getFirmById(env.DB, firmId);
          expect(firm?.plan_tier).toBe("firm_growth");
          expect(firm?.stripe_subscription_id).toBe("sub_sec86");
          expect(await processedAt(eventId)).not.toBeNull();
          expect(await ledgerRows(`${eventId}:reclaimed`)).toBe(1);

          // Once processed, a further redelivery is the old no-op 200.
          const after = await postWebhook(payload, await sig(), TEST_ENV);
          expect(after.status).toBe(200);
          expect(billSpy).toHaveBeenCalledTimes(2);
        } finally {
          billSpy.mockRestore();
        }
      });

      it("SEC-94: a throw at the final mark leaves the referral code minted; the reclaimed re-run keeps that code instead of rotating it", async () => {
        const { firmId } = await createFirmWithSession("SEC94", `sec94-${Math.random().toString(36).slice(2)}@example.com`);
        const eventId = `evt_sec94_${firmId}`;
        const payload = await checkoutEvent(firmId, eventId, false);
        const sig = () => signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
        const markSpy = vi.spyOn(store, "markWebhookEventProcessed").mockRejectedValueOnce(new Error("D1_ERROR: sec94 mark"));
        const mintSpy = vi.spyOn(store, "mintReferralCode");
        try {
          expect((await postWebhook(payload, await sig(), TEST_ENV)).status).toBe(400);
          expect(mintSpy).toHaveBeenCalledTimes(1); // run 1 got past the mint before the throw
          const code1 = (await store.getFirmById(env.DB, firmId))?.referral_code;
          expect(code1).toBeTruthy();
          expect(await processedAt(eventId)).toBeNull();

          await backdate(eventId, store.WEBHOOK_RECLAIM_AFTER_MS + 1000);
          expect((await postWebhook(payload, await sig(), TEST_ENV)).status).toBe(200);
          expect(await ledgerRows(`${eventId}:reclaimed`)).toBe(1);
          expect(mintSpy).toHaveBeenCalledTimes(1); // reclaim reused, did not re-mint
          expect((await store.getFirmById(env.DB, firmId))?.referral_code).toBe(code1);
          expect(await processedAt(eventId)).not.toBeNull();
        } finally {
          markSpy.mockRestore();
          mintSpy.mockRestore();
        }
      });

      it("BILL-15 race guard kept: an old row that IS processed is never reclaimed, and only one of two stale-row claims wins", async () => {
        const id = `evt_sec90r3_race_${Math.random().toString(36).slice(2)}`;
        expect(await store.recordWebhookEventIfNew(env.DB, id, "checkout.session.completed", null)).toBe(true);
        expect(await store.recordWebhookEventIfNew(env.DB, id, "checkout.session.completed", null)).toBe(false);
        expect(await store.reclaimWebhookEvent(env.DB, id)).toBe("in_flight");
        await backdate(id, store.WEBHOOK_RECLAIM_AFTER_MS + 1000);
        const [a, b] = await Promise.all([store.reclaimWebhookEvent(env.DB, id), store.reclaimWebhookEvent(env.DB, id)]);
        expect([a, b].sort()).toEqual(["in_flight", "reclaimed"]);
        await store.markWebhookEventProcessed(env.DB, id);
        await backdate(id, store.WEBHOOK_RECLAIM_AFTER_MS * 10);
        expect(await store.reclaimWebhookEvent(env.DB, id)).toBe("done");
      });
    });

    it("positive control: a clean checkout leaves no drop or handler-throw row", async () => {
      await clearRow(`stripe_drop:${day()}`);
      await clearRow(`stripe_handler_throw:${day()}`);
      const { firmId } = await createFirmWithSession("SEC90 ok", `sec90ok-${Math.random().toString(36).slice(2)}@example.com`);
      const payload = await checkoutEvent(firmId, `evt_sec90ok_${firmId}`, false);
      const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const resp = await postWebhook(payload, sig, TEST_ENV);
      expect(resp.status).toBe(200);
      expect(await ledgerRows(`stripe_drop:${day()}`)).toBe(0);
      expect(await ledgerRows(`stripe_handler_throw:${day()}`)).toBe(0);
      expect(await ledgerRows(`evt_sec90ok_${firmId}:throw`)).toBe(0);
    });

    it("SEC-90 remedy 3 on customer.subscription.deleted: a run that threw is re-run after the lease, so the cancelled firm drops to free", async () => {
      const { firmId } = await createFirmWithSession("SEC90 r3 del", `sec90r3del-${Math.random().toString(36).slice(2)}@example.com`);
      const subId = `sub_r3del_${firmId}`;
      await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_growth', stripe_customer_id = 'cus_r3del', stripe_subscription_id = ?2 WHERE id = ?1")
        .bind(firmId, subId)
        .run();
      const eventId = `evt_r3del_${firmId}`;
      const payload = JSON.stringify({ id: eventId, type: "customer.subscription.deleted", livemode: false, data: { object: { id: subId } } });
      const sig = async () => signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const billSpy = vi.spyOn(store, "updateFirmBilling").mockRejectedValueOnce(new Error("D1_ERROR: r3 del"));
      try {
        expect((await postWebhook(payload, await sig(), TEST_ENV)).status).toBe(400);
        expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
        expect(await ledgerRows(`${eventId}:throw`)).toBe(1);
        expect((await postWebhook(payload, await sig(), TEST_ENV)).status).toBe(409);
        await env.DB.prepare("UPDATE stripe_webhook_events SET received_at = ?1 WHERE id = ?2")
          .bind(new Date(Date.now() - store.WEBHOOK_RECLAIM_AFTER_MS - 1000).toISOString(), eventId)
          .run();
        expect((await postWebhook(payload, await sig(), TEST_ENV)).status).toBe(200);
        const firm = await store.getFirmById(env.DB, firmId);
        expect(firm?.plan_tier).toBe("free");
        expect(firm?.stripe_subscription_id).toBeNull();
        expect(await ledgerRows(`${eventId}:reclaimed`)).toBe(1);
        expect(billSpy).toHaveBeenCalledTimes(2);
      } finally {
        billSpy.mockRestore();
      }
    });

    it("SEC-92: a throw BEFORE the ledger insert still leaves the per-event :throw row (and no bare event row), so the lost event is named", async () => {
      const eventId = `evt_sec92_pre_${Math.random().toString(36).slice(2)}`;
      const payload = JSON.stringify({ id: eventId, type: "customer.subscription.deleted", livemode: false, data: { object: { id: `sub_${eventId}` } } });
      const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
      const recSpy = vi.spyOn(store, "recordWebhookEventIfNew").mockRejectedValueOnce(new Error("D1_ERROR: sec92 pre-ledger"));
      try {
        const resp = await postWebhook(payload, sig, TEST_ENV);
        expect(resp.status).toBe(400);
        expect(await ledgerRows(eventId)).toBe(0);
        expect(await ledgerRows(`${eventId}:throw`)).toBe(1);
        expect(await ledgerRows(`stripe_handler_throw:${day()}`)).toBe(1);
      } finally {
        recSpy.mockRestore();
      }
      // Not reclaimable: there is no claimed row, so Stripe's retry is a fresh first run.
      const retry = await postWebhook(payload, await signPayload(SECRET, Math.floor(Date.now() / 1000), payload), TEST_ENV);
      expect(retry.status).toBe(200);
      expect(await ledgerRows(eventId)).toBe(1);
      expect(await ledgerRows(`${eventId}:reclaimed`)).toBe(0);
    });
  });

  it("the signature check still runs first: a bad signature 400s as 'Invalid signature.' whatever livemode says", async () => {
    const payload = JSON.stringify({ id: "evt_sec86_badsig", type: "checkout.session.completed", livemode: true, data: { object: {} } });
    const resp = await postWebhook(payload, "t=1,v1=deadbeef", { STRIPE_SECRET_KEY: "sk_live_x", STRIPE_WEBHOOK_SECRET: SECRET });
    expect(resp.status).toBe(400);
    expect(((await resp.json()) as { error: string }).error).toBe("Invalid signature.");
  });
});

describe("BILL-68 (AuditLab 2026-11-12, CRITICAL): a failed webhook signature is no longer silent", () => {
  const SECRET = "whsec_test_secret";
  const LIVE_ENV = { STRIPE_SECRET_KEY: "sk_live_x", STRIPE_WEBHOOK_SECRET: SECRET };
  const dayRowId = () => `stripe_sig_fail:${new Date().toISOString().slice(0, 10)}`;
  async function dayRows(): Promise<number> {
    const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM stripe_webhook_events WHERE id = ?1").bind(dayRowId()).first<{ n: number }>();
    return row?.n ?? 0;
  }
  const alertRowId = () => `stripe_sig_fail_alert:${new Date().toISOString().slice(0, 10)}`;
  async function clearDayRow(): Promise<void> {
    await env.DB.prepare("DELETE FROM stripe_webhook_events WHERE id IN (?1, ?2) OR id LIKE ?2 || ':%'").bind(dayRowId(), alertRowId()).run();
  }
  const resendCalls = (spy: { mock: { calls: unknown[][] } }) =>
    spy.mock.calls.filter((c) => String(c[0] instanceof Request ? c[0].url : c[0]).includes("resend")).length;
  // The exact shape of the 08-05..11-12 defect: a real Stripe-signed event,
  // signed with the endpoint's secret, arriving at a Worker holding another one.
  async function liveEventSignedForOtherEndpoint(): Promise<{ payload: string; sig: string }> {
    const payload = JSON.stringify({ id: `evt_b68_${Math.random().toString(36).slice(2)}`, type: "checkout.session.completed", livemode: true, data: { object: {} } });
    const sig = await signPayload("whsec_the_live_endpoints_secret", Math.floor(Date.now() / 1000), payload);
    return { payload, sig };
  }

  it("a mis-signed event still 400s, logs, and leaves exactly one durable row per UTC day; no email while the pass is unapproved", async () => {
    await clearDayRow();
    const warnSpy = vi.spyOn(console, "warn");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    try {
      for (let i = 0; i < 3; i++) {
        const { payload, sig } = await liveEventSignedForOtherEndpoint();
        const resp = await postWebhook(payload, sig, { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "" });
        expect(resp.status).toBe(400);
        expect(((await resp.json()) as { error: string }).error).toBe("Invalid signature.");
      }
      expect(warnSpy.mock.calls.filter((c) => String(c[0]).includes("signature verification FAILED")).length).toBe(3);
      expect(await dayRows()).toBe(1);
      expect(fetchSpy.mock.calls.filter((c) => String(c[0] instanceof Request ? c[0].url : c[0]).includes("resend")).length).toBe(0);
    } finally {
      warnSpy.mockRestore();
      fetchSpy.mockRestore();
    }
  });

  it("with webhookSignatureFailureAlert approved, the first failure of the day sends ONE internal alert and the rest send none", async () => {
    await clearDayRow();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ id: "em_1" }), { status: 200 }));
    try {
      for (let i = 0; i < 3; i++) {
        const { payload, sig } = await liveEventSignedForOtherEndpoint();
        const resp = await postWebhook(payload, sig, { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "webhookSignatureFailureAlert" });
        expect(resp.status).toBe(400);
      }
      const sends = fetchSpy.mock.calls.filter((c) => String(c[0] instanceof Request ? c[0].url : c[0]).includes("resend"));
      expect(sends.length).toBe(1);
      const body = JSON.parse(String((sends[0]![1] as RequestInit).body)) as { to: unknown; subject: string };
      expect(JSON.stringify(body.to)).toContain("support@deadline-radar.com");
      expect(body.subject).toContain("failed signature verification");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("SEC-91 limb 2: a failure while the pass is closed records evidence but does NOT spend the day's email; approving mid-day then sends", async () => {
    await clearDayRow();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ id: "em_1" }), { status: 200 }));
    try {
      let { payload, sig } = await liveEventSignedForOtherEndpoint();
      expect((await postWebhook(payload, sig, { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "" })).status).toBe(400);
      expect(await dayRows()).toBe(1);
      expect(resendCalls(fetchSpy)).toBe(0);
      ({ payload, sig } = await liveEventSignedForOtherEndpoint());
      expect((await postWebhook(payload, sig, { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "webhookSignatureFailureAlert" })).status).toBe(400);
      expect(resendCalls(fetchSpy)).toBe(1);
      expect(await dayRows()).toBe(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("SEC-91 limb 3: a failed send releases the email claim, so the next failure that day retries it; the evidence row stays", async () => {
    await clearDayRow();
    let status = 500;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ id: "em_1" }), { status }));
    const ENV = { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "webhookSignatureFailureAlert" };
    const alertRows = async () =>
      (await env.DB.prepare("SELECT COUNT(*) AS n FROM stripe_webhook_events WHERE id = ?1").bind(alertRowId()).first<{ n: number }>())?.n ?? 0;
    try {
      let { payload, sig } = await liveEventSignedForOtherEndpoint();
      expect((await postWebhook(payload, sig, ENV)).status).toBe(400);
      expect(resendCalls(fetchSpy)).toBe(1);
      expect(await alertRows()).toBe(0); // released
      expect(await dayRows()).toBe(1);
      status = 200;
      ({ payload, sig } = await liveEventSignedForOtherEndpoint());
      expect((await postWebhook(payload, sig, ENV)).status).toBe(400);
      expect(resendCalls(fetchSpy)).toBe(2);
      expect(await alertRows()).toBe(1);
      ({ payload, sig } = await liveEventSignedForOtherEndpoint());
      expect((await postWebhook(payload, sig, ENV)).status).toBe(400);
      expect(resendCalls(fetchSpy)).toBe(2); // sent once, so the day's slot is spent
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("SEC-95: under a persistent send failure the day's alert is attempted at most 3 times, then the day is spent", async () => {
    await clearDayRow();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ id: "em_1" }), { status: 500 }));
    const ENV = { ...LIVE_ENV, RESEND_API_KEY: "re_test", SEND_APPROVED_PASSES: "webhookSignatureFailureAlert" };
    try {
      for (let i = 0; i < 6; i++) {
        const { payload, sig } = await liveEventSignedForOtherEndpoint();
        expect((await postWebhook(payload, sig, ENV)).status).toBe(400);
        expect(resendCalls(fetchSpy)).toBe(Math.min(i + 1, 3));
      }
      expect(resendCalls(fetchSpy)).toBe(SIG_FAIL_ALERT_ATTEMPTS_PER_DAY);
      expect(await dayRows()).toBe(1);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a request with no Stripe-Signature header (a scanner, not Stripe) is logged but leaves no row", async () => {
    await clearDayRow();
    const resp = await postWebhook(JSON.stringify({ id: "evt_b68_nohdr", type: "x", data: { object: {} } }), null, LIVE_ENV);
    expect(resp.status).toBe(400);
    expect(await dayRows()).toBe(0);
  });

  it("positive control: a correctly-signed event leaves no signature-failure row", async () => {
    await clearDayRow();
    const payload = JSON.stringify({ id: `evt_b68_ok_${Date.now()}`, type: "invoice.created", livemode: true, data: { object: {} } });
    const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
    const resp = await postWebhook(payload, sig, LIVE_ENV);
    expect(resp.status).toBe(200);
    expect(await dayRows()).toBe(0);
  });
});

describe("BILL-66 (AuditLab 2026-11-12): GET /firm/licenses tells the dashboard a revoked firm still holds a subscription", () => {
  it("has_subscription is true for a free-tier firm that kept its stripe_subscription_id (SEC-83 revoke), false without one", async () => {
    const revoked = await createFirmWithSession("BILL66 revoked", `bill66r-${Date.now()}@example.com`);
    await env.DB.prepare("UPDATE firms SET plan_tier = 'free', stripe_customer_id = 'cus_b66', stripe_subscription_id = 'sub_b66' WHERE id = ?1")
      .bind(revoked.firmId).run();
    const r1 = (await (await getFirmLicenses(revoked.cookie)).json()) as { plan_tier: string; has_subscription?: boolean };
    expect(r1.plan_tier).toBe("free");
    expect(r1.has_subscription).toBe(true);

    const plain = await createFirmWithSession("BILL66 plain", `bill66p-${Date.now()}@example.com`);
    const r2 = (await (await getFirmLicenses(plain.cookie)).json()) as { has_subscription?: boolean };
    expect(r2.has_subscription).toBe(false);
  });
});
