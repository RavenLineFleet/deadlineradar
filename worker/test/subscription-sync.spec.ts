/**
 * Self-serve plan change (2026-10-07): POST /firm/billing/portal and the
 * customer.subscription.updated webhook mirror (subscription_sync.ts).
 *
 * SELF_SERVE_PLAN_CHANGE is an Env switch that ships unset (off); this file
 * sets it to "on" in BASE_ENV to exercise the live behaviour, and
 * test/subscription-sync-flag-off.spec.ts pins the shipped (unset) behaviour. Stripe is never called -- globalThis.fetch is
 * replaced by a router keyed on URL.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import {
  deriveSubscriptionState,
  portalConfigurationIdForRoster,
  stripePriceIdForPerSeatAddon,
  stripePriceIdForTier,
} from "../src/tiers";

const SECRET = "whsec_test_sync";
const PRICES: Record<string, string> = {
  STRIPE_PRICE_FIRM_STARTER: "price_st_a",
  STRIPE_PRICE_FIRM_GROWTH: "price_gr_a",
  STRIPE_PRICE_FIRM_STANDARD: "price_sd_a",
  STRIPE_PRICE_FIRM_SCALE: "price_sc_a",
  STRIPE_PRICE_FIRM_STARTER_MONTHLY: "price_st_m",
  STRIPE_PRICE_FIRM_GROWTH_MONTHLY: "price_gr_m",
  STRIPE_PRICE_FIRM_STANDARD_MONTHLY: "price_sd_m",
  STRIPE_PRICE_FIRM_SCALE_MONTHLY: "price_sc_m",
  STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL: "price_ad_a",
  STRIPE_PRICE_PER_SEAT_ADDON_MONTHLY: "price_ad_m",
};
const CONFIGS = JSON.stringify({
  firm_starter: "bpc_starter1",
  firm_growth: "bpc_growth1",
  firm_standard: "bpc_standard1",
  firm_scale: "bpc_scale1",
  none: "bpc_none1",
});
const BASE_ENV = { SELF_SERVE_PLAN_CHANGE: "on", STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET, STRIPE_PORTAL_CONFIGS: CONFIGS, ...PRICES };

function ctx(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}
async function workerFetch(request: Request, overrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(request, { ...env, ...BASE_ENV, ...overrides } as never, ctx());
}

async function signPayload(secret: string, ts: number, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  return `t=${ts},v1=${[...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}
async function postUpdated(subId: string, eventId: string, overrides: Record<string, unknown> = {}): Promise<Response> {
  const payload = JSON.stringify({ id: eventId, type: "customer.subscription.updated", data: { object: { id: subId } } });
  const sig = await signPayload(SECRET, Math.floor(Date.now() / 1000), payload);
  return workerFetch(
    new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "content-type": "application/json", "Stripe-Signature": sig }, body: payload }),
    overrides
  );
}

interface SubStub {
  status?: string;
  customer?: string;
  cancel_at_period_end?: boolean;
  period_end?: number;
  items: Array<{ price: string; quantity: number }>;
}
interface Calls {
  subGets: string[];
  portalPosts: URLSearchParams[];
  resend: Array<{ to: string; subject: string }>;
}
/** Routes Stripe/Resend URLs; `subs` is read at call time so a test can swap the "current" state. */
function installFetch(subs: Record<string, SubStub | undefined>): Calls {
  const calls: Calls = { subGets: [], portalPosts: [], resend: [] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const subMatch = url.match(/api\.stripe\.com\/v1\/subscriptions\/([^/?]+)$/);
    if (subMatch && (!init || !init.method || init.method === "GET")) {
      const id = decodeURIComponent(subMatch[1]!);
      calls.subGets.push(id);
      const s = subs[id];
      if (!s) return new Response(JSON.stringify({ error: { message: "No such subscription" } }), { status: 404 });
      return new Response(
        JSON.stringify({
          id,
          status: s.status ?? "active",
          customer: s.customer ?? "cus_sync_1",
          cancel_at_period_end: s.cancel_at_period_end ?? false,
          items: { data: s.items.map((i) => ({ price: { id: i.price }, quantity: i.quantity, current_period_end: s.period_end ?? 1_900_000_000 })) },
        }),
        { status: 200 }
      );
    }
    if (url.endsWith("/v1/billing_portal/sessions")) {
      calls.portalPosts.push(new URLSearchParams(String(init?.body)));
      return new Response(JSON.stringify({ url: "https://billing.stripe.com/p/session/test_abc" }), { status: 200 });
    }
    if (url.includes("resend.com")) {
      const body = JSON.parse(String(init?.body));
      calls.resend.push({ to: body.to[0], subject: body.subject });
      return new Response(JSON.stringify({ id: "re_1" }), { status: 200 });
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

async function makeFirm(opts: { tier?: string; interval?: "annual" | "monthly"; sub?: string | null; customer?: string | null; demo?: boolean } = {}) {
  const firm = await store.createFirm(env.DB, { name: "Sync Firm", adminEmail: `sync-${crypto.randomUUID()}@example.com` });
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  const sub = opts.sub === undefined ? `sub_${crypto.randomUUID().slice(0, 8)}` : opts.sub;
  await env.DB.prepare(
    "UPDATE firms SET plan_tier = ?1, billing_interval = ?2, stripe_subscription_id = ?3, stripe_customer_id = ?4, demo_locked = ?5 WHERE id = ?6"
  )
    .bind(opts.tier ?? "firm_growth", opts.interval ?? "annual", sub, opts.customer === undefined ? "cus_sync_1" : opts.customer, opts.demo ? 1 : 0, firm.id)
    .run();
  return { firmId: firm.id, cookie: `dr_firm_session=${rawSessionToken}`, sub: sub as string };
}
async function addRoster(firmId: string, n: number): Promise<void> {
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
async function postPortal(cookie: string, overrides: Record<string, unknown> = {}): Promise<Response> {
  return workerFetch(
    new Request("https://deadline-radar.com/firm/billing/portal", { method: "POST", headers: { Cookie: cookie, Origin: "https://deadline-radar.com" } }),
    overrides
  );
}

describe("deriveSubscriptionState -- the 10-price reverse map, never guessing", () => {
  const e = { ...env, ...PRICES } as never;
  it("round-trips every tier x interval through stripePriceIdForTier", () => {
    for (const tier of ["firm_starter", "firm_growth", "firm_standard", "firm_scale"]) {
      for (const interval of ["annual", "monthly"] as const) {
        const priceId = stripePriceIdForTier(e, tier, interval)!;
        expect(deriveSubscriptionState(e, [{ priceId, quantity: 1 }])).toEqual({ ok: true, planTier: tier, interval, addonQuantity: 0 });
      }
    }
  });
  it("accepts firm_scale + per-seat add-on on the same interval and returns the add-on quantity", () => {
    const r = deriveSubscriptionState(e, [
      { priceId: "price_sc_m", quantity: 1 },
      { priceId: stripePriceIdForPerSeatAddon(e, "monthly"), quantity: 7 },
    ]);
    expect(r).toEqual({ ok: true, planTier: "firm_scale", interval: "monthly", addonQuantity: 7 });
  });
  it.each([
    ["no items", []],
    ["unknown price", [{ priceId: "price_nope", quantity: 1 }]],
    ["null price id", [{ priceId: null, quantity: 1 }]],
    ["tier quantity 2", [{ priceId: "price_st_a", quantity: 2 }]],
    ["tier quantity null", [{ priceId: "price_st_a", quantity: null }]],
    ["two tier prices", [{ priceId: "price_st_a", quantity: 1 }, { priceId: "price_gr_a", quantity: 1 }]],
    ["add-on only", [{ priceId: "price_ad_a", quantity: 3 }]],
    ["add-on on a non-scale tier", [{ priceId: "price_gr_a", quantity: 1 }, { priceId: "price_ad_a", quantity: 3 }]],
    ["add-on interval mismatch", [{ priceId: "price_sc_a", quantity: 1 }, { priceId: "price_ad_m", quantity: 3 }]],
    ["add-on quantity 0", [{ priceId: "price_sc_a", quantity: 1 }, { priceId: "price_ad_a", quantity: 0 }]],
  ])("rejects %s", (_name, items) => {
    expect(deriveSubscriptionState(e, items as never).ok).toBe(false);
  });
  it("BILL-38: two firm-tier bindings holding the same price id fail closed (no last-match-wins)", () => {
    const dup = { ...(e as object), STRIPE_PRICE_FIRM_SCALE: "price_st_a" } as never; // starter annual id pasted into scale too
    const r = deriveSubscriptionState(dup, [{ priceId: "price_st_a", quantity: 1 }]);
    expect(r.ok).toBe(false);
    // control: the correctly-bound env still derives the right tier
    const good = deriveSubscriptionState(e, [{ priceId: "price_st_a", quantity: 1 }]);
    expect(good.ok && good.planTier).toBe("firm_starter");
  });
  it("an UNSET price binding never matches a null/undefined price id", () => {
    const bare = { ...env } as never; // no STRIPE_PRICE_* bound
    expect(deriveSubscriptionState(bare, [{ priceId: "price_st_a", quantity: 1 }]).ok).toBe(false);
  });
});

describe("portalConfigurationIdForRoster", () => {
  const e = (cfg?: string) => ({ ...env, STRIPE_PORTAL_CONFIGS: cfg }) as never;
  it("picks the smallest tier that still covers the roster; none beyond the top cap or with an add-on", () => {
    expect(portalConfigurationIdForRoster(e(CONFIGS), 0, false)).toBe("bpc_starter1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 5, false)).toBe("bpc_starter1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 6, false)).toBe("bpc_growth1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 20, false)).toBe("bpc_standard1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 21, false)).toBe("bpc_scale1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 35, false)).toBe("bpc_scale1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 36, false)).toBe("bpc_none1");
    expect(portalConfigurationIdForRoster(e(CONFIGS), 3, true)).toBe("bpc_none1");
  });
  it("returns null for unset / malformed / missing-key / non-bpc ids", () => {
    expect(portalConfigurationIdForRoster(e(undefined), 3, false)).toBeNull();
    expect(portalConfigurationIdForRoster(e("{not json"), 3, false)).toBeNull();
    expect(portalConfigurationIdForRoster(e(JSON.stringify({ none: "bpc_x" })), 3, false)).toBeNull();
    expect(portalConfigurationIdForRoster(e(JSON.stringify({ firm_starter: "evil&x=1" })), 3, false)).toBeNull();
  });
});

describe("POST /firm/billing/portal", () => {
  it("401 without a session", async () => {
    installFetch({});
    const r = await workerFetch(new Request("https://deadline-radar.com/firm/billing/portal", { method: "POST", headers: { Origin: "https://deadline-radar.com" } }));
    expect(r.status).toBe(401);
  });
  it("200 + portal_url for a subscribed partner; sends the customer and the roster-selected configuration", async () => {
    const subs: Record<string, SubStub | undefined> = {};
    const calls = installFetch(subs);
    const { firmId, cookie, sub } = await makeFirm({ tier: "firm_growth" });
    subs[sub] = { items: [{ price: "price_gr_a", quantity: 1 }] };
    await addRoster(firmId, 7); // > starter cap 5 -> growth config
    const r = await postPortal(cookie);
    expect(r.status).toBe(200);
    expect(((await r.json()) as { portal_url: string }).portal_url).toMatch(/^https:\/\/billing\.stripe\.com\//);
    expect(calls.portalPosts).toHaveLength(1);
    expect(calls.portalPosts[0]!.get("customer")).toBe("cus_sync_1");
    expect(calls.portalPosts[0]!.get("configuration")).toBe("bpc_growth1");
    expect(calls.portalPosts[0]!.get("return_url")).toMatch(/\/firm-dashboard\/#account$/);
  });
  it("a firm whose tier is below its roster is never offered the smaller tiers (config follows the LIVE roster, not plan_tier)", async () => {
    const subs: Record<string, SubStub | undefined> = {};
    const calls = installFetch(subs);
    const { firmId, cookie, sub } = await makeFirm({ tier: "firm_scale" });
    subs[sub] = { items: [{ price: "price_sc_a", quantity: 1 }] };
    await addRoster(firmId, 12);
    await postPortal(cookie);
    expect(calls.portalPosts[0]!.get("configuration")).toBe("bpc_standard1");
  });
  // SecurityLab MEDIUM-2: add-on presence comes from the subscription's items, not roster size.
  it("over-cap firm with NO add-on is offered the bigger tiers (the primary upgrade path), not the no-switching config", async () => {
    const subs: Record<string, SubStub | undefined> = {};
    const calls = installFetch(subs);
    const { firmId, cookie, sub } = await makeFirm({ tier: "firm_starter" });
    subs[sub] = { items: [{ price: "price_st_a", quantity: 1 }] };
    await addRoster(firmId, 12); // starter cap 5, no add-on on the subscription
    expect((await postPortal(cookie)).status).toBe(200);
    expect(calls.portalPosts[0]!.get("configuration")).toBe("bpc_standard1");
  });
  it("firm_scale + add-on whose roster shrank back under the cap still gets the no-switching config", async () => {
    const subs: Record<string, SubStub | undefined> = {};
    const calls = installFetch(subs);
    const { firmId, cookie, sub } = await makeFirm({ tier: "firm_scale" });
    subs[sub] = { items: [{ price: "price_sc_a", quantity: 1 }, { price: "price_ad_a", quantity: 4 }] };
    await addRoster(firmId, 10);
    expect((await postPortal(cookie)).status).toBe(200);
    expect(calls.portalPosts[0]!.get("configuration")).toBe("bpc_none1");
  });
  // Founding Firms (2026-10-09): portal behaviour on a trial subscription is unmeasured, so a granted firm gets no plan switching.
  it("Founding Firms: a granted firm gets the no-switching config on a plain Growth sub; the same firm without a grant gets the Growth config", async () => {
    const subs: Record<string, SubStub | undefined> = {};
    const calls = installFetch(subs);
    const { firmId, cookie, sub } = await makeFirm({ tier: "firm_growth" });
    subs[sub] = { status: "trialing", items: [{ price: "price_gr_a", quantity: 1 }] };
    await addRoster(firmId, 7);
    expect((await postPortal(cookie)).status).toBe(200);
    expect(calls.portalPosts[0]!.get("configuration")).toBe("bpc_growth1"); // control: no grant
    await env.DB.prepare(
      "INSERT INTO founding_firm_grants (slot, firm_id, granted_at, verified_by, evidence_note) VALUES (1, ?1, ?2, 'devin', 'test')"
    )
      .bind(firmId, new Date().toISOString())
      .run();
    expect((await postPortal(cookie)).status).toBe(200);
    expect(calls.portalPosts[1]!.get("configuration")).toBe("bpc_none1");
  });
  it("a subscription Stripe doesn't have, or whose shape isn't recognised, gets the no-switching config", async () => {
    const subs: Record<string, SubStub | undefined> = {};
    const calls = installFetch(subs);
    const a = await makeFirm({ tier: "firm_growth" }); // subs[a.sub] undefined -> 404
    await addRoster(a.firmId, 7);
    await postPortal(a.cookie);
    const b = await makeFirm({ tier: "firm_growth" });
    subs[b.sub] = { items: [{ price: "price_mystery", quantity: 1 }] };
    await addRoster(b.firmId, 7);
    await postPortal(b.cookie);
    expect(calls.portalPosts.map((p) => p.get("configuration"))).toEqual(["bpc_none1", "bpc_none1"]);
  });
  it("502 when the subscription lookup itself fails (no portal session minted)", async () => {
    const calls = installFetch({});
    const { cookie } = await makeFirm();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ error: { message: "boom" } }), { status: 500 }));
    expect((await postPortal(cookie)).status).toBe(502);
    expect(calls.portalPosts).toHaveLength(0);
  });
  it("origin check: 400 on a foreign Origin, no Stripe call", async () => {
    const calls = installFetch({});
    const { cookie } = await makeFirm();
    const r = await workerFetch(new Request("https://deadline-radar.com/firm/billing/portal", { method: "POST", headers: { Cookie: cookie, Origin: "https://evil.example" } }));
    expect(r.status).toBe(400);
    expect(calls.portalPosts).toHaveLength(0);
  });
  it("400 when the firm has no subscription or no customer id; no Stripe call", async () => {
    const calls = installFetch({});
    const a = await makeFirm({ sub: null });
    expect((await postPortal(a.cookie)).status).toBe(400);
    const b = await makeFirm({ customer: null });
    expect((await postPortal(b.cookie)).status).toBe(400);
    expect(calls.portalPosts).toHaveLength(0);
  });
  it("403 on a demo-locked firm", async () => {
    const calls = installFetch({});
    const { cookie } = await makeFirm({ demo: true });
    expect((await postPortal(cookie)).status).toBe(403);
    expect(calls.portalPosts).toHaveLength(0);
  });
  it("503 when STRIPE_PORTAL_CONFIGS is unset (fails closed, no Stripe call)", async () => {
    const calls = installFetch({});
    const { cookie } = await makeFirm();
    expect((await postPortal(cookie, { STRIPE_PORTAL_CONFIGS: undefined })).status).toBe(503);
    expect(calls.portalPosts).toHaveLength(0);
  });
  it("503 when STRIPE_SECRET_KEY is unset", async () => {
    installFetch({});
    const { cookie } = await makeFirm();
    expect((await postPortal(cookie, { STRIPE_SECRET_KEY: undefined })).status).toBe(503);
  });
  it("a different firm's session cannot mint a portal for this firm's customer: the customer id comes from the SESSION's firm row only", async () => {
    const calls = installFetch({});
    const a = await makeFirm({ customer: "cus_firm_a" });
    await makeFirm({ customer: "cus_firm_b" });
    await postPortal(a.cookie);
    expect(calls.portalPosts[0]!.get("customer")).toBe("cus_firm_a");
  });
  it("429 after the rate limit", async () => {
    installFetch({});
    const { cookie } = await makeFirm();
    let last = 200;
    for (let i = 0; i < 12; i++) last = (await postPortal(cookie)).status;
    expect(last).toBe(429);
  });
});

describe("customer.subscription.updated -> firm row", () => {
  it("annual -> monthly + tier change is mirrored from Stripe's refetched state", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_starter", interval: "annual" });
    installFetch({ [sub]: { items: [{ price: "price_sd_m", quantity: 1 }] } });
    expect((await postUpdated(sub, `evt_${sub}_1`)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_standard");
    expect(firm?.billing_interval).toBe("monthly");
    expect(firm?.stripe_subscription_id).toBe(sub);
  });
  it("interval-only switch (same tier, annual -> monthly) is mirrored", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth", interval: "annual" });
    installFetch({ [sub]: { items: [{ price: "price_gr_m", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_iv`);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_growth");
    expect(firm?.billing_interval).toBe("monthly");
  });
  it("replay is idempotent: same event twice = same row, no error", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_starter" });
    installFetch({ [sub]: { items: [{ price: "price_gr_a", quantity: 1 }] } });
    expect((await postUpdated(sub, `evt_${sub}_r`)).status).toBe(200);
    expect((await postUpdated(sub, `evt_${sub}_r`)).status).toBe(200);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
  });
  it("order-safe: an OLD event delivered after a newer one still lands on Stripe's CURRENT state", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_starter" });
    const subs: Record<string, SubStub | undefined> = { [sub]: { items: [{ price: "price_gr_a", quantity: 1 }] } };
    installFetch(subs);
    await postUpdated(sub, `evt_${sub}_a`);
    subs[sub] = { items: [{ price: "price_sd_a", quantity: 1 }] }; // user switched again
    await postUpdated(sub, `evt_${sub}_b`);
    // Stripe now redelivers the FIRST event late; the payload carries nothing, state is refetched.
    await postUpdated(sub, `evt_${sub}_a`);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("unknown price: firm row untouched, still 200", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth", interval: "annual" });
    installFetch({ [sub]: { items: [{ price: "price_mystery", quantity: 1 }] } });
    expect((await postUpdated(sub, `evt_${sub}_u`)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.plan_tier).toBe("firm_growth");
    expect(firm?.billing_interval).toBe("annual");
  });
  it("BILL-36: every delivered event leaves a bare-type ledger row (step-9 proof), without gating the sync -- a redelivery still re-runs it", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_starter" });
    const subs: Record<string, SubStub | undefined> = { [sub]: { items: [{ price: "price_gr_a", quantity: 1 }] } };
    installFetch(subs);
    const evt = `evt_${sub}_led`;
    await postUpdated(sub, evt);
    const row = await env.DB.prepare("SELECT event_type FROM stripe_webhook_events WHERE id = ?1").bind(evt).first<{ event_type: string }>();
    expect(row?.event_type).toBe("customer.subscription.updated");
    // Same event id redelivered with Stripe now saying something else: the sync must still run (not deduped as "already seen").
    subs[sub] = { items: [{ price: "price_sd_a", quantity: 1 }] };
    await postUpdated(sub, evt);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("LOW-2: a portal cancel on an unrecognised subscription shape still mirrors cancel_at_period_end, tier untouched", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth", interval: "annual" });
    installFetch({ [sub]: { cancel_at_period_end: true, period_end: 1_900_000_000, items: [{ price: "price_mystery", quantity: 1 }] } });
    expect((await postUpdated(sub, `evt_${sub}_lc`)).status).toBe(200);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.cancel_at_period_end).toBe(1);
    expect(firm?.plan_tier).toBe("firm_growth");
  });
  it("unknown quantity (tier price x3): untouched", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth" });
    installFetch({ [sub]: { items: [{ price: "price_sd_a", quantity: 3 }] } });
    await postUpdated(sub, `evt_${sub}_q`);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
  });
  it("cancel / resume in the portal sync cancel_at_period_end + current_period_end without touching the tier", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth" });
    const subs: Record<string, SubStub | undefined> = { [sub]: { cancel_at_period_end: true, period_end: 1_900_000_000, items: [{ price: "price_gr_a", quantity: 1 }] } };
    installFetch(subs);
    await postUpdated(sub, `evt_${sub}_c1`);
    let firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.cancel_at_period_end).toBe(1);
    expect(firm?.current_period_end).toBe(new Date(1_900_000_000 * 1000).toISOString());
    expect(firm?.plan_tier).toBe("firm_growth");
    subs[sub] = { cancel_at_period_end: false, period_end: 1_900_000_000, items: [{ price: "price_gr_a", quantity: 1 }] };
    await postUpdated(sub, `evt_${sub}_c2`);
    firm = await store.getFirmById(env.DB, firmId);
    expect(firm?.cancel_at_period_end).toBe(0);
  });
  it("a subscription that belongs to a different Stripe customer is not applied", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth", customer: "cus_sync_1" });
    installFetch({ [sub]: { customer: "cus_someone_else", items: [{ price: "price_sd_a", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_x`);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
  });
  it.each(["canceled", "incomplete", "unpaid", "paused", "incomplete_expired"])("status %s is not mirrored (deleted webhook owns cancellation)", async (status) => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth" });
    installFetch({ [sub]: { status, items: [{ price: "price_sd_a", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_${status}`);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
  });
  it("past_due still mirrors a tier change (dunning does not freeze the plan record)", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth" });
    installFetch({ [sub]: { status: "past_due", items: [{ price: "price_sd_a", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_pd`);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_standard");
  });
  it("no firm holds the subscription id yet (event beat checkout.session.completed): 200 no-op, Stripe not even queried", async () => {
    const calls = installFetch({});
    expect((await postUpdated("sub_orphan_xyz", "evt_orphan_1")).status).toBe(200);
    expect(calls.subGets).toHaveLength(0);
  });
  it("Stripe fetch failure (non-404) surfaces as non-2xx so Stripe retries", async () => {
    const { sub } = await makeFirm();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ error: { message: "boom" } }), { status: 500 }));
    expect((await postUpdated(sub, `evt_${sub}_f`)).status).not.toBe(200);
  });
  it("a subscription Stripe says does not exist leaves the row alone", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_growth" });
    installFetch({});
    expect((await postUpdated(sub, `evt_${sub}_nf`)).status).toBe(200);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_growth");
  });
  it("a bad webhook signature is still rejected (the new branch does not bypass verification)", async () => {
    const { sub } = await makeFirm();
    const calls = installFetch({ [sub]: { items: [{ price: "price_sd_a", quantity: 1 }] } });
    const payload = JSON.stringify({ id: "evt_badsig", type: "customer.subscription.updated", data: { object: { id: sub } } });
    const r = await workerFetch(new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "Stripe-Signature": "t=1,v1=deadbeef" }, body: payload }));
    expect(r.status).toBeGreaterThanOrEqual(400);
    expect(calls.subGets).toHaveLength(0);
  });
});

describe("emails from the sync are consent-gated and deduped", () => {
  it("over-cap downgrade: NO email while planChangeOverCapNotice is unapproved; tier still applied, roster untouched", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_standard" });
    await addRoster(firmId, 8);
    const calls = installFetch({ [sub]: { items: [{ price: "price_st_a", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_oc`, { RESEND_API_KEY: "re_x", SEND_APPROVED_PASSES: "" });
    expect(calls.resend).toHaveLength(0);
    expect((await store.getFirmById(env.DB, firmId))?.plan_tier).toBe("firm_starter");
    expect(await store.countFirmLicenses(env.DB, firmId)).toBe(8);
  });
  it("over-cap downgrade with the pass approved: exactly ONE email to the partner, even on replay", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_standard" });
    await addRoster(firmId, 8);
    const calls = installFetch({ [sub]: { items: [{ price: "price_st_a", quantity: 1 }] } });
    const o = { RESEND_API_KEY: "re_x", SEND_APPROVED_PASSES: "planChangeOverCapNotice" };
    await postUpdated(sub, `evt_${sub}_oc2`, o);
    await postUpdated(sub, `evt_${sub}_oc2`, o);
    const firm = await store.getFirmById(env.DB, firmId);
    expect(calls.resend).toHaveLength(1);
    expect(calls.resend[0]!.to).toBe(firm?.admin_email);
  });
  it("within-cap change sends nothing even when approved", async () => {
    const { sub } = await makeFirm({ tier: "firm_standard" });
    const calls = installFetch({ [sub]: { items: [{ price: "price_gr_a", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_ok`, { RESEND_API_KEY: "re_x", SEND_APPROVED_PASSES: "planChangeOverCapNotice,billingSyncAlert" });
    expect(calls.resend).toHaveLength(0);
  });
  it("unknown state: internal alert only when billingSyncAlert is approved, once per event", async () => {
    const { sub } = await makeFirm({ tier: "firm_growth" });
    const calls = installFetch({ [sub]: { items: [{ price: "price_mystery", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_al0`, { RESEND_API_KEY: "re_x", SEND_APPROVED_PASSES: "" });
    expect(calls.resend).toHaveLength(0);
    const o = { RESEND_API_KEY: "re_x", SEND_APPROVED_PASSES: "billingSyncAlert" };
    await postUpdated(sub, `evt_${sub}_al1`, o);
    await postUpdated(sub, `evt_${sub}_al1`, o);
    expect(calls.resend).toHaveLength(1);
    expect(calls.resend[0]!.to).toBe("support@deadline-radar.com");
  });
  it("demo/test-tenant firms never get the customer over-cap email", async () => {
    const { firmId, sub } = await makeFirm({ tier: "firm_standard" });
    await addRoster(firmId, 8);
    await env.DB.prepare("UPDATE firms SET is_test_tenant = 1 WHERE id = ?1").bind(firmId).run();
    const calls = installFetch({ [sub]: { items: [{ price: "price_st_a", quantity: 1 }] } });
    await postUpdated(sub, `evt_${sub}_tt`, { RESEND_API_KEY: "re_x", SEND_APPROVED_PASSES: "planChangeOverCapNotice" });
    expect(calls.resend).toHaveLength(0);
  });
});
