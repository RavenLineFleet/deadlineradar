/**
 * Tier metadata for paid firm plans (2026-08-05). Kept separate from the
 * allow/deny logic in entitlements.ts, mirroring how mobility.ts stays
 * separate from index.ts -- one module owns "what a tier IS", the other owns
 * "does this session get through the gate".
 *
 * Every firm tier gets the IDENTICAL feature set (Roster, Calendar, Map, CPE
 * Hours, Practice Privilege Check) -- gated by staff-count capacity only,
 * never by capability. This is a deliberate reaction to CE Broker's #1
 * review complaint (crippling their cheap tier's features to force
 * upgrades); do not add a capability difference between these tiers.
 */

import type { Env } from "./env";
import { SELF_SERVE_SEAT_CAP } from "./validation";
import { isPreCutoverSignup, PAID_PLAN_TIERS } from "./entitlements";

/** Roadmap #151 (2026-08-10): the free-tier seat cap for a firm that signs
 * up AFTER the value-line cutover -- SELF_SERVE_SEAT_CAP (25) stays the cap
 * for every firm that signed up BEFORE it (isPreCutoverSignup() in
 * seatCapForFirmTier() below), same grandfather mechanism as the other
 * four #151 gates.
 *
 * BILL-16 (AuditLab, 2026-08-29): unlike the paid FIRM_TIERS seat caps
 * below, generate.py's "free, up to 3 staff" marketing copy (3 sites,
 * varied phrasing) is NOT gated against this constant -- deliberate scope
 * call, not an oversight, since a robust regex across the varied phrasing
 * wasn't worth building for a claim with zero billing impact. If this
 * number ever changes, grep generate.py for "up to 3 staff" by hand. */
export const NEW_SIGNUP_FREE_SEAT_CAP = 3;

export interface FirmTierDef {
  planTier: string;
  label: string;
  priceUsd: number;
  // PR6 (Devin, 2026-10-02, via orchestrator): the monthly cadence of the
  // SAME tier, approved alongside priceUsd (dr_review_checklist.md) --
  // $20/$29/$39/$55 respectively, each a smaller discount off priceUsd/12
  // than the prior tier (roughly 17/14/15/17%) than committing to a flat
  // percentage would give, since priceUsd itself wasn't chosen on a flat
  // formula either (see this file's own 2026-08-09 re-tier comment).
  monthlyPriceUsd: number;
  seatCap: number;
}

/** PR6 per-seat add-on (Devin approved 2026-10-02 12:01 MDT, dr_review_
 * checklist.md): the formula for a firm above firm_scale's 35-seat cap,
 * which until now was flatly "contact us, no formula." $15/seat/yr or
 * $1.50/seat/mo -- a SEPARATE Stripe Price (quantity = extra seats beyond
 * 35), added as a second checkout line item alongside firm_scale's own,
 * never a replacement for it. */
export const PER_SEAT_ADDON_ANNUAL_USD = 15;
export const PER_SEAT_ADDON_MONTHLY_USD = 1.5;

// Ordered ascending by seat cap -- firmTierForSeatCount() below depends on
// that order to find the cheapest tier a given headcount qualifies for.
// Labels renamed 2026-08-06 (Devin's pick) -- planTier slugs (firm_starter/
// firm_growth/firm_standard/firm_scale) are internal identifiers, stored in
// D1 and read by stripePriceIdForTier()'s switch below; they stay as-is,
// only `label` (the customer-facing name) changes.
//
// Re-tiered 2026-08-09 (Devin's own proposal, via orchestrator): the old
// 3-band flat-fee structure (199/5, 349/15, 500/25) had real cliffs -- one
// hire past a boundary jumped the WHOLE invoice (5->6 staff was +75%). This
// 4-band structure smooths that: the worst single-hire jump is now +50%
// (5->6), +33% (10->11), +38% (20->21). firm_starter/firm_growth/
// firm_standard REUSE their existing slugs with new price/cap (no real
// paying customers existed on any paid tier at the time of this change, so
// no migration concern) -- firm_scale is the one genuinely NEW slug, for
// the new top band. Labels shifted up one level to match: "Enterprise" now
// means the real top tier (35 seats), not the old 25-seat one.
// BILL-22/23 (SecurityLab, MEDIUM, confirmed by AuditLab, 2026-10-03): the
// expected shape every Stripe Price should have, beyond amount/currency/
// interval -- ONE shared definition so stripe.ts/scheduler.ts's cron and
// check_stripe_price_reconciliation.py's pre-push gate can't drift from
// each other (the Python side keeps its own copy of the same two values,
// same "duplicated deliberately, kept in sync by hand" precedent the rest
// of this cross-language boundary already uses). interval_count=1 means
// "bills every interval", not every N of them (interval_count=3 on
// "month" bills quarterly); usage_type="licensed" (not "metered") is
// required for any price sent with a line-item quantity -- today, only
// the per-seat add-ons, but asserted uniformly since nothing here bills
// metered.
export const EXPECTED_PRICE_INTERVAL_COUNT = 1;
export const EXPECTED_PRICE_USAGE_TYPE = "licensed";

// BILL-22/23 (HomeLab, 2026-10-03): handleFirmBillingCheckout (index.ts)
// rejects interval: "monthly" with a friendly message while this is false,
// before even looking up a price id. generate.py's MONTHLY_BILLING_ENABLED/
// DR_MONTHLY_BILLING_ENABLED are the matching UI-side half (pricing page
// + dashboard upgrade panel). 2026-10-07: ENABLED on the monthly-billing
// branch -- live-mode monthly + per-seat Prices exist and a live-mode
// check_stripe_price_reconciliation.py run was clean (10/10 prices, 10/10
// coupons duration=once). Do not merge/deploy before SecurityLab +
// AuditLab PASS and the STRIPE_PRICE_*_MONTHLY / PER_SEAT_ADDON_* Worker
// secrets are bound. If a re-run of that script ever fails, flip all
// three flags back together.
export const MONTHLY_BILLING_ENABLED = true;

export const FIRM_TIERS: FirmTierDef[] = [
  { planTier: "firm_starter", label: "Essentials", priceUsd: 199, monthlyPriceUsd: 20, seatCap: 5 },
  { planTier: "firm_growth", label: "Growth", priceUsd: 299, monthlyPriceUsd: 29, seatCap: 10 },
  { planTier: "firm_standard", label: "Professional", priceUsd: 399, monthlyPriceUsd: 39, seatCap: 20 },
  { planTier: "firm_scale", label: "Enterprise", priceUsd: 549, monthlyPriceUsd: 55, seatCap: 35 },
];

const FIRM_TIER_SEAT_CAPS: Record<string, number> = Object.fromEntries(
  FIRM_TIERS.map((t) => [t.planTier, t.seatCap])
);

/** The fallback for `free`/`pilot`/any unrecognised tier -- SELF_SERVE_SEAT_CAP
 * (25) for a firm that signed up before the roadmap #151 value-line
 * cutover (grandfathered), NEW_SIGNUP_FREE_SEAT_CAP (3) for one that
 * signed up after. A named paid tier's own FIRM_TIER_SEAT_CAPS entry is
 * unaffected either way -- this only changes what the FREE fallback means.
 *
 * AuditLab/SecurityLab TIER-2 (MEDIUM, 2026-09-26): `firm`/`firm_annual`/
 * `premium` are recognised as PAID by entitlements.ts's PAID_PLAN_TIERS
 * (its own comment: "the original manually-set tiers... still honored") but
 * had no entry in FIRM_TIER_SEAT_CAPS above -- so a post-cutover firm on one
 * of them fell through to the FREE branch below and got capped at 3 despite
 * being fully paid everywhere else. Deliberately NOT added as entries to
 * FIRM_TIERS above -- that array is order-dependent (firmTierForSeatCount()
 * finds the first/cheapest tier covering a headcount for CHECKOUT) and a
 * legacy, non-purchasable tier appearing there could route a real customer
 * to it, or change firmTierByPlanTier()'s null-vs-def behavior. Reading
 * PAID_PLAN_TIERS directly instead of hand-listing these three slugs here
 * makes the two files agree BY CONSTRUCTION -- a future paid tier added to
 * entitlements.ts without a matching FIRM_TIER_SEAT_CAPS entry now gets a
 * PAID default (SELF_SERVE_SEAT_CAP, 25 -- the uniform cap for any paid firm
 * before the 2026-08-09 per-tier re-tier) instead of silently falling into
 * the free-tier split below, closing the class of bug, not just today's
 * three instances. */
export function seatCapForFirmTier(planTier: string, createdAt: string): number {
  const namedCap = FIRM_TIER_SEAT_CAPS[planTier];
  if (namedCap !== undefined) return namedCap;
  if (PAID_PLAN_TIERS.has(planTier)) return SELF_SERVE_SEAT_CAP;
  return isPreCutoverSignup(createdAt) ? SELF_SERVE_SEAT_CAP : NEW_SIGNUP_FREE_SEAT_CAP;
}

/** The cheapest firm tier whose seat cap covers `seatCount`, or null if no
 * defined tier covers it (35+ staff as of the 2026-08-09 re-tier -- unchanged
 * "contact us", no formula, Devin's explicit call). Checkout must never let
 * a firm buy a tier smaller than this for its current roster. */
export function firmTierForSeatCount(seatCount: number): FirmTierDef | null {
  return FIRM_TIERS.find((t) => seatCount <= t.seatCap) ?? null;
}

/** Founding Firms (Devin, 2026-10-09): the one plan a founding firm's free
 * year can be on, and its length. Trial access is a Stripe trial with no card
 * that Stripe cancels at day 365 (see handleFirmBillingCheckout). */
export const FOUNDING_FIRM_PLAN_TIER = "firm_growth";
export const FOUNDING_FIRM_TRIAL_DAYS = 365;

export function firmTierByPlanTier(planTier: string): FirmTierDef | null {
  return FIRM_TIERS.find((t) => t.planTier === planTier) ?? null;
}

/** Which Env secret holds this tier's Stripe Price id. Test-mode and
 * live-mode prices are different ids on the same Stripe account, so this
 * indirection (rather than a hardcoded id) is what makes the Gate 1 -> Gate 2
 * swap a pure secret rotation. Returns null for an unrecognised tier.
 *
 * `INDIVIDUAL_TIER`/`"individual"` REMOVED 2026-08-09 (Devin's decision):
 * the $39/yr Individual tier never had a real checkout path (this switch's
 * own "individual" case was unreachable from handleFirmBillingCheckout(),
 * which resolves tiers via firmTierByPlanTier() -- FIRM_TIERS only, never
 * included INDIVIDUAL_TIER) and zero real rows ever held plan_tier=
 * 'individual' (confirmed against prod D1 before removing). Folded into
 * the free tier -- see entitlements.ts's own solo-free exception. */
// PR6 (2026-10-02): `interval` defaults to "annual" so every existing
// call site (none of which know about monthly yet until index.ts's
// checkout route is updated to pass it through) keeps resolving the
// exact same env var it always has -- adding the parameter is not itself
// a behavior change for annual checkout.
export function stripePriceIdForTier(env: Env, planTier: string, interval: "annual" | "monthly" = "annual"): string | null {
  if (interval === "monthly") {
    switch (planTier) {
      case "firm_starter":
        return env.STRIPE_PRICE_FIRM_STARTER_MONTHLY ?? null;
      case "firm_growth":
        return env.STRIPE_PRICE_FIRM_GROWTH_MONTHLY ?? null;
      case "firm_standard":
        return env.STRIPE_PRICE_FIRM_STANDARD_MONTHLY ?? null;
      case "firm_scale":
        return env.STRIPE_PRICE_FIRM_SCALE_MONTHLY ?? null;
      default:
        return null;
    }
  }
  switch (planTier) {
    case "firm_starter":
      return env.STRIPE_PRICE_FIRM_STARTER ?? null;
    case "firm_growth":
      return env.STRIPE_PRICE_FIRM_GROWTH ?? null;
    case "firm_standard":
      return env.STRIPE_PRICE_FIRM_STANDARD ?? null;
    case "firm_scale":
      return env.STRIPE_PRICE_FIRM_SCALE ?? null;
    default:
      return null;
  }
}

/** PR6 per-seat add-on Price id for the given interval -- see
 * PER_SEAT_ADDON_ANNUAL_USD/PER_SEAT_ADDON_MONTHLY_USD's own comment. */
export function stripePriceIdForPerSeatAddon(env: Env, interval: "annual" | "monthly"): string | null {
  return interval === "monthly" ? env.STRIPE_PRICE_PER_SEAT_ADDON_MONTHLY ?? null : env.STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL ?? null;
}

/** Self-serve plan/interval change via the Stripe Customer Portal
 * (Devin, 2026-10-07, "customers must change monthly<->annual and plan
 * up/down without contacting support"). An Env switch (SELF_SERVE_PLAN_CHANGE
 * === "on"), not a build constant, so the Worker code can ship dark and be
 * enabled with one `wrangler secret put` once SecurityLab + AuditLab PASS and
 * the portal configurations (scripts/configure_stripe_portal.py) exist in
 * live mode. generate.py's DR_SELF_SERVE_PLAN_CHANGE_ENABLED is the matching
 * UI-side half -- flip both together. Unset (the shipped state): POST
 * /firm/billing/portal 404s, the customer.subscription.updated branch is a
 * no-op that never calls Stripe, and the already_subscribed refusal keeps
 * saying "contact support". */
export function selfServePlanChangeEnabled(env: Env): boolean {
  return env.SELF_SERVE_PLAN_CHANGE === "on";
}

export type SubscriptionItemLike = { priceId: string | null; quantity: number | null };

export type DerivedSubscriptionState =
  | { ok: true; planTier: string; interval: "annual" | "monthly"; addonQuantity: number }
  | { ok: false; reason: string };

/**
 * Maps a live Stripe subscription's items back to (tier, interval) via the
 * 10 STRIPE_PRICE_* bindings -- the reverse of stripePriceIdForTier() /
 * stripePriceIdForPerSeatAddon(). Pure, no I/O. NEVER guesses: anything it
 * does not recognise exactly (unknown price, a tier price with quantity != 1,
 * two tier prices, mixed intervals, an add-on on a tier other than
 * firm_scale, an add-on with no/invalid quantity) is `ok: false` with a
 * reason, and the caller leaves the firm's stored state untouched and alerts.
 * The add-on is accepted here because checkout legitimately creates it
 * (tier + add-on as a 2-item subscription); its quantity is returned but is
 * not persisted anywhere (no column holds extra seats today).
 */
export function deriveSubscriptionState(env: Env, items: SubscriptionItemLike[]): DerivedSubscriptionState {
  if (items.length === 0) return { ok: false, reason: "subscription has no items" };
  let tier: { planTier: string; interval: "annual" | "monthly" } | null = null;
  let addon: { interval: "annual" | "monthly"; quantity: number } | null = null;
  for (const item of items) {
    const priceId = item.priceId;
    if (!priceId) return { ok: false, reason: "item with no price id" };
    let matchedTier: { planTier: string; interval: "annual" | "monthly" } | null = null;
    for (const t of FIRM_TIERS) {
      for (const interval of ["annual", "monthly"] as const) {
        if (stripePriceIdForTier(env, t.planTier, interval) === priceId) {
          // Fail closed (AuditLab BILL-38): two STRIPE_PRICE_FIRM_* bindings
          // holding the same id (a bad `wrangler secret put`) must never let
          // the last match win and silently mirror the wrong tier.
          if (matchedTier) return { ok: false, reason: `price id ${priceId} is bound to more than one firm tier/interval (misconfigured STRIPE_PRICE_FIRM_* secrets)` };
          matchedTier = { planTier: t.planTier, interval };
        }
      }
    }
    if (matchedTier) {
      if (tier) return { ok: false, reason: "more than one firm-tier price on the subscription" };
      if (item.quantity !== 1) return { ok: false, reason: `firm-tier price with quantity ${item.quantity ?? "null"} (expected 1)` };
      tier = matchedTier;
      continue;
    }
    const addonInterval = (["annual", "monthly"] as const).find((i) => stripePriceIdForPerSeatAddon(env, i) === priceId);
    if (addonInterval) {
      if (addon) return { ok: false, reason: "more than one per-seat add-on price on the subscription" };
      if (!Number.isInteger(item.quantity) || (item.quantity as number) < 1) {
        return { ok: false, reason: `per-seat add-on with invalid quantity ${item.quantity ?? "null"}` };
      }
      addon = { interval: addonInterval, quantity: item.quantity as number };
      continue;
    }
    return { ok: false, reason: `unrecognised price id ${priceId}` };
  }
  if (!tier) return { ok: false, reason: "no firm-tier price on the subscription" };
  if (addon && addon.interval !== tier.interval) return { ok: false, reason: "add-on and firm-tier prices are on different intervals" };
  if (addon && tier.planTier !== "firm_scale") return { ok: false, reason: "per-seat add-on on a tier other than firm_scale" };
  return { ok: true, planTier: tier.planTier, interval: tier.interval, addonQuantity: addon?.quantity ?? 0 };
}

/** Which Customer Portal Configuration (bpc_...) a firm's portal session
 * must use. One configuration per "smallest tier the firm may switch to":
 * the portal's plan list is per-configuration, not per-customer, so the only
 * way to stop a downgrade below the live roster BEFORE it happens (a paid
 * firm over its tier cap is not roster-paused -- reconcileRosterPauseState()
 * only acts on unpaid firms) is to hand each firm the configuration whose
 * allowed prices are exactly the tiers that still cover its roster. `none`
 * (roster beyond the top tier's cap, or a firm_scale + add-on subscription,
 * which the portal cannot switch anyway) is a configuration with cancel /
 * payment-method / invoices but NO plan switching.
 * STRIPE_PORTAL_CONFIGS is a JSON object {"firm_starter":"bpc_..",
 * "firm_growth":..,"firm_standard":..,"firm_scale":..,"none":..}; returns
 * null if unset/unparseable/missing the needed key (caller 503s). */
export function portalConfigurationIdForRoster(env: Env, rosterCount: number, hasAddon: boolean): string | null {
  let map: Record<string, unknown>;
  try {
    map = JSON.parse(env.STRIPE_PORTAL_CONFIGS ?? "");
  } catch {
    return null;
  }
  if (!map || typeof map !== "object") return null;
  const smallest = hasAddon ? null : firmTierForSeatCount(rosterCount);
  const key = smallest ? smallest.planTier : "none";
  const id = map[key];
  return typeof id === "string" && /^bpc_[A-Za-z0-9]+$/.test(id) ? id : null;
}

/** Founding Firms (AuditLab FFT-6): the portal configuration for a firm on a
 * LIVE founding trial -- no plan switching AND no payment-method update, because
 * adding a card during the free year converts it into a $299 charge on day 365.
 * STRIPE_PORTAL_CONFIGS carries it under "founding_trial". Fail closed: null
 * when unset/malformed, and the caller refuses the portal rather than falling
 * back to a configuration that lets a card be added. */
export function portalConfigurationIdForFoundingTrial(env: Env): string | null {
  let map: Record<string, unknown>;
  try {
    map = JSON.parse(env.STRIPE_PORTAL_CONFIGS ?? "");
  } catch {
    return null;
  }
  if (!map || typeof map !== "object") return null;
  const id = map["founding_trial"];
  return typeof id === "string" && /^bpc_[A-Za-z0-9]+$/.test(id) ? id : null;
}

/** Roadmap #31 compounding tiers (2026-08-11, Devin's spec): "10% off each
 * time [a referral converts], up to 10 times, which is 100% off." Tier N
 * (1-10) maps to the Nth successful referral -> N*10% off, capped at tier
 * 10 (100%). See env.ts's own STRIPE_COUPON_REFERRAL docstring for why this
 * is a prefix, not a single id, and MAX_REFERRAL_TIER for the cap. (Moved
 * here from index.ts 2026-10-07.) */
export const MAX_REFERRAL_TIER = 10;
export function referralTierCouponId(prefix: string, tier: number): string {
  return `${prefix}${Math.max(1, Math.min(tier, MAX_REFERRAL_TIER))}`;
}
