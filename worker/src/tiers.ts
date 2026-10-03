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
