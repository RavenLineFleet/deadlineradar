/**
 * Self-serve plan change (2026-10-07): the referral-reward re-apply after a
 * portal switch. MEASURED against Stripe's real test-mode API
 * (scripts/check_stripe_portal_integration.py): a plan/interval switch drops a
 * pending duration=once discount without applying it to any invoice, so a
 * referrer who switches plans before their next renewal would silently lose a
 * reward the app promised. The switch event carries the evidence
 * (previous_attributes.discounts non-empty + items changed, refetched
 * subscription has none); subscription_sync.ts re-applies the tier coupon.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const SECRET = "whsec_test_coupon";
const PRICES: Record<string, string> = {
  STRIPE_PRICE_FIRM_STARTER: "price_st_a",
  STRIPE_PRICE_FIRM_GROWTH: "price_gr_a",
  STRIPE_PRICE_FIRM_STANDARD: "price_sd_a",
  STRIPE_PRICE_FIRM_SCALE: "price_sc_a",
  STRIPE_PRICE_FIRM_STARTER_MONTHLY: "price_st_m",
  STRIPE_PRICE_FIRM_GROWTH_MONTHLY: "price_gr_m",
  STRIPE_PRICE_FIRM_STANDARD_MONTHLY: "price_sd_m",
  STRIPE_PRICE_FIRM_SCALE_MONTHLY: "price_sc_m",
};
const BASE = { SELF_SERVE_PLAN_CHANGE: "on", STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET, ...PRICES };
const PREV_SWITCH = { discounts: ["di_old"], items: { data: [] }, plan: {} };

function ctx(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}
async function sign(payload: string): Promise<string> {
  const ts = Math.floor(Date.now() / 1000);
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  return `t=${ts},v1=${[...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}
async function postUpdated(subId: string, eventId: string, overrides: Record<string, unknown>, previous?: Record<string, unknown>): Promise<Response> {
  const payload = JSON.stringify({
    id: eventId,
    type: "customer.subscription.updated",
    data: { object: { id: subId }, ...(previous ? { previous_attributes: previous } : {}) },
  });
  const worker = (await import("../src/index")).default;
  return worker.fetch(
    new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "Stripe-Signature": await sign(payload) }, body: payload }),
    { ...env, ...BASE, ...overrides } as never,
    ctx()
  );
}

interface Stub {
  price: string;
  discounts: number;
  couponFails?: boolean;
}
function installFetch(sub: string, stub: Stub): Array<{ sub: string; body: URLSearchParams }> {
  const couponPosts: Array<{ sub: string; body: URLSearchParams }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    if (!url.includes(`/v1/subscriptions/${sub}`)) throw new Error(`unexpected fetch ${url}`);
    if (init?.method === "POST") {
      couponPosts.push({ sub, body: new URLSearchParams(String(init.body)) });
      if (stub.couponFails) return new Response(JSON.stringify({ error: { message: "coupon boom" } }), { status: 400 });
      return new Response(JSON.stringify({ id: sub }), { status: 200 });
    }
    return new Response(
      JSON.stringify({
        id: sub,
        status: "active",
        customer: "cus_c1",
        cancel_at_period_end: false,
        discounts: Array.from({ length: stub.discounts }, (_, i) => ({ id: `di_${i}` })),
        items: { data: [{ price: { id: stub.price }, quantity: 1, current_period_end: 1_900_000_000 }] },
      }),
      { status: 200 }
    );
  });
  return couponPosts;
}
afterEach(() => vi.restoreAllMocks());

async function makeFirm(): Promise<{ firmId: string; sub: string }> {
  const firm = await store.createFirm(env.DB, { name: "Coupon Firm", adminEmail: `cp-${crypto.randomUUID()}@example.com` });
  const sub = `sub_${crypto.randomUUID().slice(0, 8)}`;
  await env.DB.prepare("UPDATE firms SET plan_tier='firm_growth', billing_interval='annual', stripe_subscription_id=?1, stripe_customer_id='cus_c1' WHERE id=?2").bind(sub, firm.id).run();
  return { firmId: firm.id, sub };
}
async function referrerWithRewards(firmId: string, n: number): Promise<void> {
  for (let i = 0; i < n; i++) {
    const ref = await store.createFirm(env.DB, { name: `Referee ${i}`, adminEmail: `ref${i}-${crypto.randomUUID()}@example.com` });
    await env.DB.prepare("UPDATE firms SET referred_by_firm_id = ?1, referrer_rewarded_at = ?2 WHERE id = ?3").bind(firmId, new Date().toISOString(), ref.id).run();
  }
}
const O = { STRIPE_COUPON_REFERRAL: "cpn_ref_t" };

describe("referral reward survives a portal plan switch", () => {
  it("switch that dropped a discount: tier coupon (rewarded-count) re-applied once; tier still mirrored", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 3);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_1`, O, PREV_SWITCH);
    expect(posts).toHaveLength(1);
    expect(posts[0]!.body.get("discounts[0][coupon]")).toBe("cpn_ref_t3");
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("coupon tier is capped at 10", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 12);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_2`, O, PREV_SWITCH);
    expect(posts[0]!.body.get("discounts[0][coupon]")).toBe("cpn_ref_t10");
  });
  it("a renewal consuming the coupon (no item change, tier unchanged) is NOT re-applied", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 2);
    const posts = installFetch(sub, { price: "price_gr_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_3`, O, { discounts: ["di_old"] });
    expect(posts).toHaveLength(0);
  });
  it("a renewal event (previous discounts, NO item change) is not mistaken for a switch even when the stored tier is stale", async () => {
    const { firmId, sub } = await makeFirm(); // stored tier firm_growth, Stripe says standard
    await referrerWithRewards(firmId, 2);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_3b`, O, { discounts: ["di_old"] });
    expect(posts).toHaveLength(0);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("a switch where the discount is still attached is NOT re-applied", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 2);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 1 });
    await postUpdated(sub, `evt_${sub}_4`, O, PREV_SWITCH);
    expect(posts).toHaveLength(0);
  });
  it("a switch with no previous discount never touches coupons", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 2);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_5`, O, { items: { data: [] }, plan: {} });
    expect(posts).toHaveLength(0);
  });
  it("no recorded reward => nothing to re-apply", async () => {
    const { sub } = await makeFirm();
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_6`, O, PREV_SWITCH);
    expect(posts).toHaveLength(0);
  });
  it("STRIPE_COUPON_REFERRAL unset => no coupon POST", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 1);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_7`, {}, PREV_SWITCH);
    expect(posts).toHaveLength(0);
  });
  it("MEDIUM-1: a failing re-apply THROWS (non-2xx -> Stripe retries) and leaves the tier row stale so the retry re-applies", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 1);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0, couponFails: true });
    const r = await postUpdated(sub, `evt_${sub}_8`, O, PREV_SWITCH);
    expect(r.status).toBe(400); // the dispatcher maps any throw to a deliberate non-2xx, which Stripe retries
    expect(posts).toHaveLength(1);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
    // Stripe retries the same event; the transient error is gone this time.
    vi.restoreAllMocks();
    const retryPosts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    expect((await postUpdated(sub, `evt_${sub}_8`, O, PREV_SWITCH)).status).toBe(200);
    expect(retryPosts).toHaveLength(1);
    expect(retryPosts[0]!.body.get("discounts[0][coupon]")).toBe("cpn_ref_t1");
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("LOW-1: a dropped NON-referral coupon (object-form discount) is not replaced by a referral coupon", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 2);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_9`, O, { discounts: [{ id: "di_x", coupon: { id: "courtesy_20" } }], items: { data: [] } });
    expect(posts).toHaveLength(0);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("LOW-1: an object-form referral-prefix discount IS re-applied", async () => {
    const { firmId, sub } = await makeFirm();
    await referrerWithRewards(firmId, 2);
    const posts = installFetch(sub, { price: "price_sd_a", discounts: 0 });
    await postUpdated(sub, `evt_${sub}_10`, O, { discounts: [{ id: "di_x", coupon: { id: "cpn_ref_t2" } }], items: { data: [] } });
    expect(posts).toHaveLength(1);
  });
});
