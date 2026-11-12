/**
 * Mid-trial checkout (Devin, 2026-11-12): a firm that picks a plan during its
 * 14-day trial is charged when the trial ends, not at checkout. Stripe is
 * mocked here; Stripe's own 2-day minimum for Checkout trial_end was measured
 * in test mode the same day (47h -> 400, 48h+60s -> 200).
 *
 * Every positive case has a control that differs in one input, so a passing
 * "trial_end present" can't be an artifact of always sending it.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { DEFERRED_CHARGE_MIN_LEAD_MS, DEFERRED_CHECKOUT_SESSION_TTL_S, deferredFirstChargeAt } from "../src/entitlements";

const SECRET = "whsec_test_midtrial";
const STRIPE_ENV = {
  STRIPE_SECRET_KEY: "sk_test_x",
  STRIPE_WEBHOOK_SECRET: SECRET,
  STRIPE_PRICE_FIRM_STARTER: "price_st_a",
  STRIPE_PRICE_FIRM_GROWTH: "price_gr_a",
  STRIPE_PRICE_FIRM_GROWTH_MONTHLY: "price_gr_m",
  STRIPE_PRICE_FIRM_STANDARD: "price_sd_a",
  STRIPE_COUPON_REFERRAL: "ref_coupon",
};

function ctx(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}
async function workerFetch(request: Request): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...STRIPE_ENV } as never, ctx());
}
async function signPayload(ts: number, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  return `t=${ts},v1=${[...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}
async function makeFirm(trialEndsAt: string | null | "default" = "default"): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name: "Mid-trial Firm", adminEmail: `mt-${crypto.randomUUID()}@example.com` });
  if (trialEndsAt !== "default") {
    await env.DB.prepare("UPDATE firms SET trial_ends_at = ?1 WHERE id = ?2").bind(trialEndsAt, firm.id).run();
  }
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}
async function trialEndsAt(firmId: string): Promise<string> {
  const row = await env.DB.prepare("SELECT trial_ends_at FROM firms WHERE id = ?1").bind(firmId).first<{ trial_ends_at: string }>();
  return row!.trial_ends_at;
}
function mockStripe(): { bodies: URLSearchParams[] } {
  const bodies: URLSearchParams[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
    bodies.push(new URLSearchParams(String(init?.body)));
    return new Response(JSON.stringify({ id: "cs_test_mt", url: "https://checkout.stripe.com/pay/cs_test_mt" }), { status: 200 });
  });
  return { bodies };
}
async function checkout(cookie: string, tier = "firm_starter"): Promise<Response> {
  return workerFetch(
    new Request("https://deadline-radar.com/firm/billing/checkout", {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie, Origin: "https://deadline-radar.com" },
      body: JSON.stringify({ tier }),
    })
  );
}
async function licenses(cookie: string): Promise<Record<string, unknown>> {
  const res = await workerFetch(new Request("https://deadline-radar.com/firm/licenses", { headers: { Cookie: cookie } }));
  expect(res.status).toBe(200);
  return (await res.json()) as Record<string, unknown>;
}
const hoursFromNow = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();

afterEach(() => vi.restoreAllMocks());

describe("deferredFirstChargeAt()", () => {
  const now = Date.parse("2026-11-12T12:00:00.000Z");
  const none = { foundingTrial: false, referralCoupon: false };
  it("returns the trial end when at least the minimum lead remains", () => {
    const end = new Date(now + DEFERRED_CHARGE_MIN_LEAD_MS).toISOString();
    expect(deferredFirstChargeAt(end, none, now)).toBe(end);
  });
  it("CONTROL: one ms under the minimum lead -> charged now", () => {
    expect(deferredFirstChargeAt(new Date(now + DEFERRED_CHARGE_MIN_LEAD_MS - 1).toISOString(), none, now)).toBeNull();
  });
  it("SEC-84 invariant: lead minus the session TTL stays >= 48h floor + 1h margin", () => {
    // trial_end is frozen at mint; the subscription is created at completion,
    // up to the TTL later. Both constants are needed to state the invariant.
    expect(DEFERRED_CHECKOUT_SESSION_TTL_S).toBeGreaterThanOrEqual(30 * 60); // Stripe's expires_at minimum
    expect(DEFERRED_CHARGE_MIN_LEAD_MS - DEFERRED_CHECKOUT_SESSION_TTL_S * 1000).toBeGreaterThanOrEqual(49 * 3_600_000);
  });
  it("CONTROL: no trial, an expired trial, or an unparseable value -> charged now", () => {
    expect(deferredFirstChargeAt(null, none, now)).toBeNull();
    expect(deferredFirstChargeAt("2026-11-01T00:00:00.000Z", none, now)).toBeNull();
    expect(deferredFirstChargeAt("not a date", none, now)).toBeNull();
  });
  it("CONTROL: founding trial or referral coupon -> charged now", () => {
    const end = new Date(now + 10 * 86_400_000).toISOString();
    expect(deferredFirstChargeAt(end, { foundingTrial: true, referralCoupon: false }, now)).toBeNull();
    expect(deferredFirstChargeAt(end, { foundingTrial: false, referralCoupon: true }, now)).toBeNull();
  });
});

describe("POST /firm/billing/checkout during a trial", () => {
  it("fresh signup (14-day trial): Stripe trial_end = the firm's trial_ends_at, card still collected, success_trial return", async () => {
    const { firmId, cookie } = await makeFirm();
    const { bodies } = mockStripe();
    expect((await checkout(cookie)).status).toBe(200);
    const b = bodies[0]!;
    expect(b.get("subscription_data[trial_end]")).toBe(String(Math.floor(Date.parse(await trialEndsAt(firmId)) / 1000)));
    expect(b.get("subscription_data[trial_period_days]")).toBeNull();
    // payment_method_collection unset = Stripe's "always": the card is taken now.
    expect(b.get("payment_method_collection")).toBeNull();
    expect(b.get("success_url")).toMatch(/#account\?checkout=success_trial$/);
    expect(b.get("line_items[0][price]")).toBe("price_st_a");
    // SEC-84: the session expires within the TTL, not Stripe's 24h default.
    const exp = Number(b.get("expires_at"));
    const nowS = Math.floor(Date.now() / 1000);
    expect(exp).toBeGreaterThan(nowS);
    expect(exp).toBeLessThanOrEqual(nowS + DEFERRED_CHECKOUT_SESSION_TTL_S);
    expect(Number(b.get("subscription_data[trial_end]")) - exp).toBeGreaterThanOrEqual(48 * 3600);
  });

  it("CONTROL: 49.9h of trial left (under the 50h minimum) -> no trial_end, no expires_at, ordinary success return", async () => {
    const { cookie } = await makeFirm(hoursFromNow(49.9));
    const { bodies } = mockStripe();
    expect((await checkout(cookie)).status).toBe(200);
    expect(bodies[0]!.get("subscription_data[trial_end]")).toBeNull();
    expect(bodies[0]!.get("expires_at")).toBeNull();
    expect(bodies[0]!.get("success_url")).toMatch(/#account\?checkout=success$/);
  });

  it("50.1h of trial left (over the minimum) -> trial_end set, session bounded to the TTL", async () => {
    const { cookie } = await makeFirm(hoursFromNow(50.1));
    const { bodies } = mockStripe();
    expect((await checkout(cookie)).status).toBe(200);
    expect(bodies[0]!.get("subscription_data[trial_end]")).not.toBeNull();
    const exp = bodies[0]!.get("expires_at");
    expect(exp).not.toBeNull();
    expect(Number(exp)).toBeLessThanOrEqual(Math.floor(Date.now() / 1000) + DEFERRED_CHECKOUT_SESSION_TTL_S);
  });

  it("CONTROL: expired trial / no trial -> no trial_end", async () => {
    for (const t of [hoursFromNow(-1), null]) {
      const { cookie } = await makeFirm(t);
      const { bodies } = mockStripe();
      expect((await checkout(cookie)).status).toBe(200);
      expect(bodies[0]!.get("subscription_data[trial_end]")).toBeNull();
      vi.restoreAllMocks();
    }
  });

  it("CONTROL: a referred firm with its coupon unspent is charged now (reward accounting needs a paid session)", async () => {
    const { firmId, cookie } = await makeFirm();
    await env.DB.prepare("UPDATE firms SET referral_discount_pending = 1 WHERE id = ?1").bind(firmId).run();
    const { bodies } = mockStripe();
    expect((await checkout(cookie)).status).toBe(200);
    expect(bodies[0]!.get("discounts[0][coupon]")).not.toBeNull();
    expect(bodies[0]!.get("subscription_data[trial_end]")).toBeNull();
  });

  it("a founding grant keeps its own 365-day no-card trial and never also gets trial_end", async () => {
    await env.DB.prepare("DELETE FROM founding_firm_grants").run();
    const { firmId, cookie } = await makeFirm();
    await env.DB.prepare(
      "INSERT INTO founding_firm_grants (slot, firm_id, granted_at, verified_by, evidence_note, trial_started_at) VALUES (3, ?1, ?2, 'devin', 'test', NULL)"
    )
      .bind(firmId, new Date().toISOString())
      .run();
    const { bodies } = mockStripe();
    expect((await checkout(cookie, "firm_growth")).status).toBe(200);
    expect(bodies[0]!.get("subscription_data[trial_period_days]")).toBe("365");
    expect(bodies[0]!.get("subscription_data[trial_end]")).toBeNull();
    expect((await licenses(cookie)).checkout_first_charge_at).toBeNull();
    await env.DB.prepare("DELETE FROM founding_firm_grants").run();
  });
});

describe("checkout.session.completed for a deferred-charge checkout", () => {
  it("writes the paid tier even though nothing was paid yet (payment_status no_payment_required)", async () => {
    const { firmId } = await makeFirm();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const payload = JSON.stringify({
      id: `evt_${crypto.randomUUID()}`,
      type: "checkout.session.completed",
      data: {
        object: {
          customer: "cus_mt_1",
          subscription: `sub_mt_${crypto.randomUUID().slice(0, 8)}`,
          payment_status: "no_payment_required",
          metadata: { firm_id: firmId, target_plan_tier: "firm_starter", billing_interval: "annual" },
        },
      },
    });
    const sig = await signPayload(Math.floor(Date.now() / 1000), payload);
    const res = await workerFetch(
      new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "content-type": "application/json", "Stripe-Signature": sig }, body: payload })
    );
    expect(res.status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_starter");
    expect(firm?.stripe_subscription_id).toMatch(/^sub_mt_/);
  });
});

describe("GET /firm/licenses checkout_first_charge_at", () => {
  it("fresh signup partner: equals trial_ends_at", async () => {
    const { firmId, cookie } = await makeFirm();
    const data = await licenses(cookie);
    expect(data.checkout_first_charge_at).toBe(new Date(await trialEndsAt(firmId)).toISOString());
  });
  it("CONTROL: under the minimum lead, referred firm, or already subscribed -> null", async () => {
    const short = await makeFirm(hoursFromNow(49.9));
    expect((await licenses(short.cookie)).checkout_first_charge_at).toBeNull();
    const referred = await makeFirm();
    await env.DB.prepare("UPDATE firms SET referral_discount_pending = 1 WHERE id = ?1").bind(referred.firmId).run();
    expect((await licenses(referred.cookie)).checkout_first_charge_at).toBeNull();
    const paid = await makeFirm();
    await env.DB.prepare("UPDATE firms SET plan_tier = 'firm_starter', stripe_subscription_id = 'sub_x' WHERE id = ?1").bind(paid.firmId).run();
    expect((await licenses(paid.cookie)).checkout_first_charge_at).toBeNull();
  });
  it("checkout_founding_trial: true for an unused grant at ANY lead time (BILL-63), false otherwise", async () => {
    await env.DB.prepare("DELETE FROM founding_firm_grants").run();
    const plain = await makeFirm();
    expect((await licenses(plain.cookie)).checkout_founding_trial).toBe(false);
    const cookies: string[] = [];
    for (const [slot, lead] of [[4, undefined], [5, hoursFromNow(48)]] as const) {
      const f = await makeFirm(lead);
      cookies.push(f.cookie);
      await env.DB.prepare(
        "INSERT INTO founding_firm_grants (slot, firm_id, granted_at, verified_by, evidence_note, trial_started_at) VALUES (?1, ?2, ?3, 'devin', 'test', NULL)"
      )
        .bind(slot, f.firmId, new Date().toISOString())
        .run();
      const data = await licenses(f.cookie);
      expect(data.checkout_founding_trial).toBe(true);
      expect(data.checkout_first_charge_at).toBeNull();
    }
    // CONTROL: a grant already started is not a pending founding checkout.
    await env.DB.prepare("UPDATE founding_firm_grants SET trial_started_at = ?1 WHERE slot = 4").bind(new Date().toISOString()).run();
    expect((await licenses(cookies[0]!)).checkout_founding_trial).toBe(false);
    await env.DB.prepare("DELETE FROM founding_firm_grants").run();
  });
});
