/**
 * Founding Firms (Devin decision 2026-10-09): 365-day Stripe trial, no card,
 * auto-cancel at day 365, capped at 5 by D1. Stripe is mocked; the Stripe-side
 * behaviour of the exact parameters was measured separately in test mode
 * (AssetLab/state/FOUNDING_FIRMS_DESIGN.md, scripts_ff/).
 *
 * Every property below has a control: the same request WITHOUT a grant must
 * look exactly like today's checkout, so a passing "trial params present" can
 * never be an artifact of the test sending them unconditionally.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";

const SECRET = "whsec_test_ff";
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
async function workerFetch(request: Request, overrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...STRIPE_ENV, ...overrides } as never, ctx());
}
async function signPayload(secret: string, ts: number, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  return `t=${ts},v1=${[...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}
async function makeFirm(): Promise<{ firmId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name: "Founding Test Firm", adminEmail: `ff-${crypto.randomUUID()}@example.com` });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}` };
}
async function grant(firmId: string, slot: number, startedAt: string | null = null): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO founding_firm_grants (slot, firm_id, granted_at, verified_by, evidence_note, trial_started_at) VALUES (?1, ?2, ?3, 'devin', 'test evidence', ?4)"
  )
    .bind(slot, firmId, new Date().toISOString(), startedAt)
    .run();
}
async function clearGrants(): Promise<void> {
  await env.DB.prepare("DELETE FROM founding_firm_grants").run();
}
async function addRoster(firmId: string, n: number): Promise<void> {
  await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firmId).run();
  for (let i = 0; i < n; i++) {
    await store.addPending(env.DB, {
      email: `s${i}-${crypto.randomUUID().slice(0, 8)}@example.com`,
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
}
function mockStripe(): { bodies: URLSearchParams[] } {
  const bodies: URLSearchParams[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
    bodies.push(new URLSearchParams(String(init?.body)));
    return new Response(JSON.stringify({ id: "cs_test_ff", url: "https://checkout.stripe.com/pay/cs_test_ff" }), { status: 200 });
  });
  return { bodies };
}
async function checkout(cookie: string, body: Record<string, unknown>): Promise<Response> {
  return workerFetch(
    new Request("https://deadline-radar.com/firm/billing/checkout", {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie, Origin: "https://deadline-radar.com" },
      body: JSON.stringify(body),
    })
  );
}
async function completed(firmId: string, subId: string, metadata: Record<string, string>, eventId = `evt_${crypto.randomUUID()}`): Promise<Response> {
  const payload = JSON.stringify({
    id: eventId,
    type: "checkout.session.completed",
    data: { object: { customer: "cus_ff_1", subscription: subId, payment_status: "no_payment_required", metadata: { firm_id: firmId, ...metadata } } },
  });
  const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
  return workerFetch(new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "content-type": "application/json", "Stripe-Signature": sig }, body: payload }));
}
function trialKeys(b: URLSearchParams): string[] {
  return [...b.keys()].filter((k) => k.startsWith("subscription_data") || k === "payment_method_collection");
}

afterEach(() => vi.restoreAllMocks());

describe("founding_firm_grants table: the cap is structural", () => {
  it("slots 1-5 insert; a 6th, slot 0 and a duplicate firm are all refused by the schema", async () => {
    await clearGrants();
    const ids: string[] = [];
    for (let slot = 1; slot <= 5; slot++) {
      const { firmId } = await makeFirm();
      ids.push(firmId);
      await grant(firmId, slot);
    }
    const sixth = await makeFirm();
    await expect(grant(sixth.firmId, 6)).rejects.toThrow();
    await expect(grant(sixth.firmId, 0)).rejects.toThrow();
    await expect(grant(ids[0]!, 3)).rejects.toThrow(); // same firm, other slot
    const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM founding_firm_grants").first<{ n: number }>();
    expect(count?.n).toBe(5);
  });
  it("a slot survives its firm's hard delete (no FK, no cascade): exactly 5 ever", async () => {
    await clearGrants();
    const { firmId } = await makeFirm();
    await grant(firmId, 1);
    await env.DB.prepare("UPDATE firms SET status = ?1, deletion_requested_at = ?2 WHERE id = ?3")
      .bind(store.FIRM_STATUS_DELETED, new Date(Date.now() - 31 * 86_400_000).toISOString(), firmId)
      .run();
    const deleted = await store.hardDeleteExpiredFirms(env.DB, env.DOCUMENTS, new Date());
    expect(deleted).toContain(firmId);
    expect(await store.getFirmById(env.DB, firmId)).toBeNull();
    expect((await env.DB.prepare("SELECT COUNT(*) AS n FROM founding_firm_grants").first<{ n: number }>())?.n).toBe(1);
    // the slot is still consumed: it cannot be granted again
    const next = await makeFirm();
    await expect(grant(next.firmId, 1)).rejects.toThrow();
  });
  it("SecurityLab MEDIUM-2: hard delete releases firm_id AND stripe_subscription_id from the retained row", async () => {
    await clearGrants();
    const { firmId } = await makeFirm();
    await grant(firmId, 2);
    await env.DB.prepare("UPDATE founding_firm_grants SET trial_started_at = ?1, stripe_subscription_id = 'sub_pii' WHERE slot = 2")
      .bind(new Date().toISOString())
      .run();
    await env.DB.prepare("UPDATE firms SET status = ?1, deletion_requested_at = ?2 WHERE id = ?3")
      .bind(store.FIRM_STATUS_DELETED, new Date(Date.now() - 31 * 86_400_000).toISOString(), firmId)
      .run();
    expect(await store.hardDeleteExpiredFirms(env.DB, env.DOCUMENTS, new Date())).toContain(firmId);
    const row = await env.DB.prepare("SELECT * FROM founding_firm_grants WHERE slot = 2").first<Record<string, unknown>>();
    expect(row).not.toBeNull();
    expect(row?.firm_id).toBeNull();
    expect(row?.stripe_subscription_id).toBeNull();
    expect(row?.trial_started_at).not.toBeNull(); // history of use is kept
  });
  it("CONTROL: another firm's grant row is untouched by a different firm's hard delete", async () => {
    await clearGrants();
    const keep = await makeFirm();
    await grant(keep.firmId, 1);
    await env.DB.prepare("UPDATE founding_firm_grants SET stripe_subscription_id = 'sub_keep' WHERE slot = 1").run();
    const gone = await makeFirm();
    await env.DB.prepare("UPDATE firms SET status = ?1, deletion_requested_at = ?2 WHERE id = ?3")
      .bind(store.FIRM_STATUS_DELETED, new Date(Date.now() - 31 * 86_400_000).toISOString(), gone.firmId)
      .run();
    await store.hardDeleteExpiredFirms(env.DB, env.DOCUMENTS, new Date());
    const row = await store.getFoundingFirmGrant(env.DB, keep.firmId);
    expect(row?.stripe_subscription_id).toBe("sub_keep");
  });
});

describe("POST /firm/billing/checkout with a founding grant", () => {
  it("CONTROL: no grant -> no trial parameters at all, annual price, referral coupon still applies", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await env.DB.prepare("UPDATE firms SET referral_discount_pending = 1 WHERE id = ?1").bind(firmId).run();
    const { bodies } = mockStripe();
    expect((await checkout(cookie, { tier: "firm_growth" })).status).toBe(200);
    const b = bodies[0]!;
    expect(trialKeys(b)).toEqual([]);
    expect(b.get("metadata[founding_firm_slot]")).toBeNull();
    expect(b.get("discounts[0][coupon]")).not.toBeNull();
  });

  it("granted + unstarted: 365-day trial, no card, cancel at end, Growth annual price, slot in metadata, NO coupon even for a referred firm", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await env.DB.prepare("UPDATE firms SET referral_discount_pending = 1 WHERE id = ?1").bind(firmId).run();
    await grant(firmId, 2);
    const { bodies } = mockStripe();
    expect((await checkout(cookie, { tier: "firm_growth" })).status).toBe(200);
    const b = bodies[0]!;
    expect(b.get("subscription_data[trial_period_days]")).toBe("365");
    expect(b.get("subscription_data[trial_settings][end_behavior][missing_payment_method]")).toBe("cancel");
    expect(b.get("payment_method_collection")).toBe("if_required");
    expect(b.get("line_items[0][price]")).toBe("price_gr_a");
    expect(b.get("metadata[target_plan_tier]")).toBe("firm_growth");
    expect(b.get("metadata[billing_interval]")).toBe("annual");
    expect(b.get("metadata[founding_firm_slot]")).toBe("2");
    expect([...b.keys()].filter((k) => k.startsWith("discounts"))).toEqual([]);
  });

  it("granted: a client asking for monthly never reaches Stripe with a monthly price", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await grant(firmId, 1);
    const { bodies } = mockStripe();
    const r = await checkout(cookie, { tier: "firm_growth", interval: "monthly" });
    // MONTHLY_BILLING_ENABLED may be off in this build (400 before any Stripe
    // call); when on, the founding path must still force annual.
    if (r.status === 200) {
      expect(bodies[0]!.get("line_items[0][price]")).toBe("price_gr_a");
      expect(bodies[0]!.get("metadata[billing_interval]")).toBe("annual");
    } else {
      expect(bodies).toHaveLength(0);
    }
  });

  it("granted but asking for a tier other than Growth: 400, no Stripe call", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await grant(firmId, 1);
    const { bodies } = mockStripe();
    const r = await checkout(cookie, { tier: "firm_standard" });
    expect(r.status).toBe(400);
    expect(((await r.json()) as { error: string }).error).toMatch(/Growth/);
    expect(bodies).toHaveLength(0);
  });

  it("granted with a roster above Growth's cap: 400 naming the cap, no Stripe call", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await addRoster(firmId, 11);
    await grant(firmId, 1);
    const { bodies } = mockStripe();
    const r = await checkout(cookie, { tier: "firm_growth" });
    expect(r.status).toBe(400);
    expect(((await r.json()) as { error: string }).error).toMatch(/up to 10 staff/);
    expect(bodies).toHaveLength(0);
  });

  it("a grant whose trial already started is NOT a second free year: plain checkout again", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await grant(firmId, 1, new Date().toISOString());
    const { bodies } = mockStripe();
    expect((await checkout(cookie, { tier: "firm_growth" })).status).toBe(200);
    expect(trialKeys(bodies[0]!)).toEqual([]);
    expect(bodies[0]!.get("metadata[founding_firm_slot]")).toBeNull();
  });

  it("HIGH-1 backstop: an unstarted grant on a firm that already has a stripe_customer_id gets NO trial (a lost stamp cannot give a second free year)", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await env.DB.prepare("UPDATE firms SET stripe_customer_id = 'cus_prior' WHERE id = ?1").bind(firmId).run();
    await grant(firmId, 1);
    const { bodies } = mockStripe();
    expect((await checkout(cookie, { tier: "firm_growth" })).status).toBe(200);
    expect(trialKeys(bodies[0]!)).toEqual([]);
    expect(bodies[0]!.get("metadata[founding_firm_slot]")).toBeNull();
  });

  it("another firm's grant never leaks: an un-granted firm gets no trial while a granted firm exists", async () => {
    await clearGrants();
    const granted = await makeFirm();
    await grant(granted.firmId, 1);
    const other = await makeFirm();
    const { bodies } = mockStripe();
    expect((await checkout(other.cookie, { tier: "firm_growth" })).status).toBe(200);
    expect(trialKeys(bodies[0]!)).toEqual([]);
  });
});

describe("checkout.session.completed for a founding trial", () => {
  it("stamps the grant once, flips the firm to Growth, and fires NO referral reward ($0 session)", async () => {
    await clearGrants();
    const { firmId } = await makeFirm();
    await grant(firmId, 3);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const r = await completed(firmId, "sub_ff_1", { target_plan_tier: "firm_growth", billing_interval: "annual", founding_firm_slot: "3" });
    expect(r.status).toBe(200);
    const row = await store.getFoundingFirmGrant(env.DB, firmId);
    expect(row?.trial_started_at).not.toBeNull();
    expect(row?.stripe_subscription_id).toBe("sub_ff_1");
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_growth");
    expect(firm?.stripe_subscription_id).toBe("sub_ff_1");
    // No Stripe coupon call may have been made (referral reward path).
    const couponCalls = fetchSpy.mock.calls.filter(
      (c) => String(c[0]).includes("api.stripe.com") && String((c[1] as RequestInit | undefined)?.body ?? "").includes("coupon")
    );
    expect(couponCalls).toEqual([]);
  });

  it("HIGH-1: a stamp that THROWS does not fail the webhook or lose the tier; the unstamped grant cannot be reused (customer id is set)", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await grant(firmId, 1);
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    const spy = vi.spyOn(store, "claimFoundingFirmTrialStart").mockRejectedValue(new Error("D1 transient"));
    const r = await completed(firmId, "sub_lost", { target_plan_tier: "firm_growth", founding_firm_slot: "1" });
    spy.mockRestore();
    expect(r.status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_growth");
    expect(firm?.stripe_subscription_id).toBe("sub_lost");
    expect((await store.getFoundingFirmGrant(env.DB, firmId))?.trial_started_at).toBeNull(); // stamp really was lost
    // Day 365 passes: Stripe cancels, firm drops to free, then tries to buy.
    await env.DB.prepare("UPDATE firms SET plan_tier = 'free', stripe_subscription_id = NULL WHERE id = ?1").bind(firmId).run();
    vi.restoreAllMocks();
    const { bodies } = mockStripe();
    expect((await checkout(cookie, { tier: "firm_growth" })).status).toBe(200);
    expect(trialKeys(bodies[0]!)).toEqual([]);
  });

  it("a second completed session for the same grant does not restamp (first subscription id wins)", async () => {
    await clearGrants();
    const { firmId } = await makeFirm();
    await grant(firmId, 1);
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await completed(firmId, "sub_first", { target_plan_tier: "firm_growth", founding_firm_slot: "1" });
    await completed(firmId, "sub_second", { target_plan_tier: "firm_growth", founding_firm_slot: "1" });
    expect((await store.getFoundingFirmGrant(env.DB, firmId))?.stripe_subscription_id).toBe("sub_first");
  });

  it("metadata naming someone else's slot, or a slot that is not 1-5, stamps nothing", async () => {
    await clearGrants();
    const victim = await makeFirm();
    await grant(victim.firmId, 4);
    const attacker = await makeFirm();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await completed(attacker.firmId, "sub_x", { target_plan_tier: "firm_growth", founding_firm_slot: "4" });
    await completed(attacker.firmId, "sub_y", { target_plan_tier: "firm_growth", founding_firm_slot: "9" });
    await completed(attacker.firmId, "sub_z", { target_plan_tier: "firm_growth", founding_firm_slot: "4; DROP" });
    expect((await store.getFoundingFirmGrant(env.DB, victim.firmId))?.trial_started_at).toBeNull();
  });

  it("CONTROL: a normal paid session (no founding metadata) leaves every grant untouched", async () => {
    await clearGrants();
    const granted = await makeFirm();
    await grant(granted.firmId, 1);
    const paying = await makeFirm();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await completed(paying.firmId, "sub_paid", { target_plan_tier: "firm_growth" });
    expect((await store.getFoundingFirmGrant(env.DB, granted.firmId))?.trial_started_at).toBeNull();
  });
});

describe("day 365: Stripe cancels the trial -> customer.subscription.deleted", () => {
  it("drops the firm to free and keeps the grant stamped, so there is no second free year", async () => {
    await clearGrants();
    const { firmId, cookie } = await makeFirm();
    await grant(firmId, 1);
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await completed(firmId, "sub_end", { target_plan_tier: "firm_growth", founding_firm_slot: "1" });
    const payload = JSON.stringify({ id: `evt_del_${firmId}`, type: "customer.subscription.deleted", data: { object: { id: "sub_end" } } });
    const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
    const r = await workerFetch(
      new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "content-type": "application/json", "Stripe-Signature": sig }, body: payload })
    );
    expect(r.status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("free");
    expect(firm?.stripe_subscription_id).toBeNull();
    vi.restoreAllMocks();
    const { bodies } = mockStripe();
    expect((await checkout(cookie, { tier: "firm_growth" })).status).toBe(200);
    expect(trialKeys(bodies[0]!)).toEqual([]);
  });
});
