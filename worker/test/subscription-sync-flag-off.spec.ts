/**
 * Self-serve plan change (2026-10-07), SHIPPED state: the SELF_SERVE_PLAN_CHANGE
 * Env switch is unset. The route must not exist and the new webhook branch must
 * be a no-op that never calls Stripe. Only an exact "on" enables it.
 */
import { env } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as store from "../src/store";
import { selfServePlanChangeEnabled } from "../src/tiers";

const SECRET = "whsec_test_flagoff";
function ctx(): ExecutionContext {
  return { waitUntil() {}, passThroughOnException() {}, props: {} } as unknown as ExecutionContext;
}
async function workerFetch(request: Request, overrides: Record<string, unknown> = {}): Promise<Response> {
  const worker = (await import("../src/index")).default;
  return worker.fetch(
    request,
    { ...env, STRIPE_SECRET_KEY: "sk_test_x", STRIPE_WEBHOOK_SECRET: SECRET, STRIPE_PRICE_FIRM_SCALE: "price_sc_a", ...overrides } as never,
    ctx()
  );
}
async function sign(payload: string): Promise<string> {
  const ts = Math.floor(Date.now() / 1000);
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${ts}.${payload}`));
  return `t=${ts},v1=${[...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("")}`;
}
afterEach(() => vi.restoreAllMocks());

describe("self-serve plan change is OFF as shipped", () => {
  it("only the exact string \"on\" enables it", () => {
    expect(selfServePlanChangeEnabled({ ...env } as never)).toBe(false);
    for (const v of ["", "true", "1", "ON", "yes", " on"]) {
      expect(selfServePlanChangeEnabled({ ...env, SELF_SERVE_PLAN_CHANGE: v } as never), v).toBe(false);
    }
    expect(selfServePlanChangeEnabled({ ...env, SELF_SERVE_PLAN_CHANGE: "on" } as never)).toBe(true);
  });
  it("POST /firm/billing/portal 404s even for a valid subscribed partner", async () => {
    const firm = await store.createFirm(env.DB, { name: "Off Firm", adminEmail: `off-${crypto.randomUUID()}@example.com` });
    const { rawSessionToken } = await store.createSession(env.DB, firm.id);
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id='sub_off', stripe_customer_id='cus_off', plan_tier='firm_growth' WHERE id=?1").bind(firm.id).run();
    const spy = vi.spyOn(globalThis, "fetch");
    const r = await workerFetch(
      new Request("https://deadline-radar.com/firm/billing/portal", { method: "POST", headers: { Cookie: `dr_firm_session=${rawSessionToken}`, Origin: "https://deadline-radar.com" } }),
      { STRIPE_PORTAL_CONFIGS: JSON.stringify({ none: "bpc_x" }) }
    );
    expect(r.status).toBe(404);
    expect(spy).not.toHaveBeenCalled();
  });
  it("customer.subscription.updated is acknowledged but changes nothing and never calls Stripe", async () => {
    const firm = await store.createFirm(env.DB, { name: "Off Firm 2", adminEmail: `off2-${crypto.randomUUID()}@example.com` });
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id='sub_off2', stripe_customer_id='cus_off2', plan_tier='firm_growth' WHERE id=?1").bind(firm.id).run();
    const spy = vi.spyOn(globalThis, "fetch");
    const payload = JSON.stringify({ id: "evt_off_1", type: "customer.subscription.updated", data: { object: { id: "sub_off2" } } });
    const r = await workerFetch(new Request("https://deadline-radar.com/stripe/webhook", { method: "POST", headers: { "Stripe-Signature": await sign(payload) }, body: payload }));
    expect(r.status).toBe(200);
    expect(spy).not.toHaveBeenCalled();
    expect((await store.getFirmById(env.DB, firm.id))?.plan_tier).toBe("firm_growth");
  });
  it("the already_subscribed checkout refusal still says contact support", async () => {
    const firm = await store.createFirm(env.DB, { name: "Off Firm 3", adminEmail: `off3-${crypto.randomUUID()}@example.com` });
    const { rawSessionToken } = await store.createSession(env.DB, firm.id);
    await env.DB.prepare("UPDATE firms SET stripe_subscription_id='sub_off3', plan_tier='firm_growth' WHERE id=?1").bind(firm.id).run();
    const r = await workerFetch(
      new Request("https://deadline-radar.com/firm/billing/checkout", {
        method: "POST",
        headers: { Cookie: `dr_firm_session=${rawSessionToken}`, Origin: "https://deadline-radar.com", "content-type": "application/json" },
        body: JSON.stringify({ tier: "firm_growth", interval: "annual" }),
      }),
      { STRIPE_PRICE_FIRM_GROWTH: "price_gr_a" }
    );
    const body = (await r.json()) as { error: string; code: string };
    expect(body.code).toBe("already_subscribed");
    expect(body.error).toMatch(/contact support/);
  });
});
