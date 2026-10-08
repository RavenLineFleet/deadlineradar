/**
 * Self-serve plan change (2026-10-07): fetchStripeSubscription() + deriveSubscriptionState()
 * against subscription JSON captured from Stripe's REAL test-mode API after each kind of
 * switch the portal performs (scripts/check_stripe_portal_integration.py --write-fixtures;
 * ids scrubbed, price ids are test-mode Price ids, not secrets). The hand-built stubs in
 * subscription-sync.spec.ts prove the logic; these prove the logic reads what Stripe sends.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchStripeSubscription } from "../src/stripe";
import { deriveSubscriptionState } from "../src/tiers";
import growthAnnual from "./fixtures/stripe_subscription_after_growth_annual.json";
import standardAnnual from "./fixtures/stripe_subscription_after_standard_annual.json";
import starterAnnual from "./fixtures/stripe_subscription_after_starter_annual.json";
import starterMonthly from "./fixtures/stripe_subscription_after_starter_monthly.json";
import scaleAnnual from "./fixtures/stripe_subscription_after_scale_annual.json";

const CASES = [
  { name: "growth/annual", body: growthAnnual, tier: "firm_growth", interval: "annual", key: "STRIPE_PRICE_FIRM_GROWTH" },
  { name: "standard/annual", body: standardAnnual, tier: "firm_standard", interval: "annual", key: "STRIPE_PRICE_FIRM_STANDARD" },
  { name: "starter/annual", body: starterAnnual, tier: "firm_starter", interval: "annual", key: "STRIPE_PRICE_FIRM_STARTER" },
  { name: "starter/monthly", body: starterMonthly, tier: "firm_starter", interval: "monthly", key: "STRIPE_PRICE_FIRM_STARTER_MONTHLY" },
  { name: "scale/annual", body: scaleAnnual, tier: "firm_scale", interval: "annual", key: "STRIPE_PRICE_FIRM_SCALE" },
] as const;

// Bind each tier/interval to the exact Price id Stripe returned in its own fixture.
const ENV = Object.fromEntries(CASES.map((c) => [c.key, c.body.items.data[0]!.price.id])) as never;

afterEach(() => vi.restoreAllMocks());

describe("real Stripe subscription payloads", () => {
  it.each(CASES)("$name: parsed by fetchStripeSubscription and mapped to the right tier + interval", async (c) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(c.body), { status: 200 }));
    const snap = await fetchStripeSubscription("sk_test_x", "sub_x");
    expect(snap).not.toBeNull();
    expect(snap!.status).toBe("active");
    expect(snap!.items).toHaveLength(1);
    expect(typeof snap!.currentPeriodEnd).toBe("string");
    expect(deriveSubscriptionState(ENV, snap!.items)).toEqual({ ok: true, planTier: c.tier, interval: c.interval, addonQuantity: 0 });
  });
  it("a real payload against an env with NO price bindings is refused, never guessed", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(growthAnnual), { status: 200 }));
    const snap = await fetchStripeSubscription("sk_test_x", "sub_x");
    expect(deriveSubscriptionState({} as never, snap!.items).ok).toBe(false);
  });
});
