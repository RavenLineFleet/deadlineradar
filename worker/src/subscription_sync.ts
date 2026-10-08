/**
 * Self-serve plan change (2026-10-07). Mirrors a subscription change made in
 * Stripe's hosted Customer Portal (plan switch, monthly<->annual switch,
 * cancel / resume) into the firm row, via the customer.subscription.updated
 * webhook. Before this, the only writers of plan_tier / billing_interval were
 * checkout.session.completed and customer.subscription.deleted -- the portal
 * would have changed what Stripe bills while the dashboard kept entitling the
 * old tier.
 *
 * Properties this module guarantees (each pinned by test/subscription-sync.spec.ts):
 *  - ORDER-SAFE: the event payload is never trusted for state. The
 *    subscription is refetched from Stripe and the firm row is set to what
 *    Stripe says RIGHT NOW, so two quick portal changes delivered out of
 *    order converge to the same final row either way.
 *  - REPLAY-SAFE: applying is a pure function of the refetched state, so a
 *    redelivered event writes the same values again. Only the two emails are
 *    deduped (claimed per event id), so a replay never sends twice.
 *  - NEVER GUESSES: any subscription shape deriveSubscriptionState() does
 *    not recognise exactly leaves plan_tier / billing_interval untouched and
 *    raises an internal alert instead.
 *  - A Stripe/D1 failure throws (-> 500 -> Stripe retries the event).
 *
 * Roster vs tier cap: the portal configuration handed to each firm
 * (tiers.ts portalConfigurationIdForRoster()) only offers tiers that still
 * cover its roster, so a downgrade below staff count is prevented BEFORE it
 * happens. A paid firm over its tier cap is deliberately NOT roster-paused
 * (reconcileRosterPauseState() only ever acts on unpaid firms; changing that
 * would alter entitlements for every legacy paid tier). The residual race --
 * staff added after the portal session was created -- applies the new tier
 * (Stripe is the billing truth), leaves the roster untouched, and emails the
 * partner; the existing seat-cap check blocks further adds.
 */
import type { Env } from "./env";
import * as store from "./store";
import { applyCouponToSubscription, fetchStripeSubscription } from "./stripe";
import {
  selfServePlanChangeEnabled,
  deriveSubscriptionState,
  firmTierByPlanTier,
  referralTierCouponId,
  seatCapForFirmTier,
} from "./tiers";
import { requireSendApproval } from "./scheduler";
import { sendEmail } from "./sender";
import { buildBillingSyncAlertEmail, buildPlanChangeOverCapEmail } from "./emails";

const INTERNAL_NOTIFY_EMAIL = "support@deadline-radar.com";
const SYNCED_STATUSES = new Set(["active", "trialing", "past_due"]);

async function alertUnsynced(env: Env, eventId: string, firmId: string, subscriptionId: string, problem: string): Promise<void> {
  console.log(`[subscription-sync] NOT applied for firm ${firmId} sub ${subscriptionId}: ${problem}`);
  if (!requireSendApproval(env, "billingSyncAlert") || !env.RESEND_API_KEY) return;
  const claimed = await store.recordWebhookEventIfNew(env.DB, `${eventId}:alert`, "customer.subscription.updated:alert", firmId);
  if (!claimed) return;
  await sendEmail(
    env.RESEND_API_KEY,
    INTERNAL_NOTIFY_EMAIL,
    buildBillingSyncAlertEmail({ firmId, subscriptionId, problem }),
    env.EMAIL_ALLOWLIST,
    env.EMAIL_PREVIEW_LOG_BODY
  );
}

export async function applySubscriptionUpdatedEvent(
  env: Env,
  eventId: string,
  subscriptionId: string,
  dashboardUrl: string,
  previousAttributes: Record<string, unknown> | null = null
): Promise<void> {
  if (!selfServePlanChangeEnabled(env)) return;
  if (!env.STRIPE_SECRET_KEY) return;

  const firm = await store.findFirmByStripeSubscriptionId(env.DB, subscriptionId);
  // No firm yet: this event beat checkout.session.completed (which is what
  // first stores the subscription id and writes the tier). Nothing to mirror.
  if (!firm) return;

  const snapshot = await fetchStripeSubscription(env.STRIPE_SECRET_KEY, subscriptionId);
  if (!snapshot) {
    await alertUnsynced(env, eventId, firm.id, subscriptionId, "subscription not found in Stripe");
    return;
  }
  // canceled -> customer.subscription.deleted owns the revert to free;
  // incomplete/unpaid/paused are not states this mirror should entitle on.
  if (!SYNCED_STATUSES.has(snapshot.status)) return;
  if (firm.stripe_customer_id && snapshot.customerId && snapshot.customerId !== firm.stripe_customer_id) {
    await alertUnsynced(env, eventId, firm.id, subscriptionId, "subscription belongs to a different Stripe customer than the firm record");
    return;
  }

  const derived = deriveSubscriptionState(env, snapshot.items);
  if (!derived.ok) {
    await alertUnsynced(env, eventId, firm.id, subscriptionId, derived.reason);
    return;
  }

  const tierChanged = derived.planTier !== firm.plan_tier || derived.interval !== firm.billing_interval;
  // MEASURED against Stripe's real test-mode API (scripts/check_stripe_portal_
  // integration.py, 2026-10-07): a plan/interval switch DROPS a pending
  // duration=once discount without applying it to any invoice. The referrer's
  // reward (applyReferralRewardIfEligible -> a once coupon on their live
  // subscription, consumed at the next renewal) would therefore vanish if they
  // switch plans first. The switch event says so itself: previous_attributes
  // carries the old non-empty `discounts` AND a changed `items`, while the
  // refetched subscription has none -- a renewal consuming the coupon never
  // changes `items`, so the two cases cannot be confused. Re-apply the tier
  // coupon the referrer's reward count implies (replace semantics, so a
  // redelivery is harmless). Best-effort: a failure alerts, never blocks the
  // tier mirror below.
  const prevDiscounts = previousAttributes?.discounts;
  if (
    tierChanged &&
    Array.isArray(prevDiscounts) &&
    prevDiscounts.length > 0 &&
    previousAttributes !== null &&
    "items" in previousAttributes &&
    snapshot.discountCount === 0 &&
    env.STRIPE_COUPON_REFERRAL
  ) {
    try {
      const rewarded = await store.countRewardedReferrals(env.DB, firm.id);
      if (rewarded > 0) {
        await applyCouponToSubscription(env.STRIPE_SECRET_KEY, subscriptionId, referralTierCouponId(env.STRIPE_COUPON_REFERRAL, rewarded));
      } else {
        await alertUnsynced(env, eventId, firm.id, subscriptionId, "plan switch removed a discount but the firm has no recorded referral reward to re-apply");
      }
    } catch (err) {
      await alertUnsynced(env, eventId, firm.id, subscriptionId, `could not re-apply the referral reward after a plan switch: ${String(err)}`);
    }
  }
  if (tierChanged) {
    await store.updateFirmBilling(env.DB, firm.id, {
      planTier: derived.planTier,
      stripeCustomerId: firm.stripe_customer_id ?? snapshot.customerId ?? "",
      stripeSubscriptionId: subscriptionId,
      billingInterval: derived.interval,
    });
  }
  // cancel_at_period_end / current_period_end are display state; mirror them
  // whenever Stripe's differ (portal cancel and portal resume both land here).
  if (
    snapshot.currentPeriodEnd &&
    (Boolean(firm.cancel_at_period_end) !== snapshot.cancelAtPeriodEnd || firm.current_period_end !== snapshot.currentPeriodEnd)
  ) {
    await store.updateFirmCancellation(env.DB, firm.id, {
      cancelAtPeriodEnd: snapshot.cancelAtPeriodEnd,
      currentPeriodEnd: snapshot.currentPeriodEnd,
    });
  }
  if (!tierChanged) return;

  // Best-effort, same posture as the checkout branch: restoring/reconciling
  // pause state must never fail the webhook after the tier write succeeded.
  try {
    await store.reconcileRosterPauseState(env.DB, firm.id);
  } catch (err) {
    console.log(`[subscription-sync] roster-pause reconcile failed for firm ${firm.id}: ${String(err)}`);
  }

  const rosterCount = await store.countFirmLicenses(env.DB, firm.id);
  const newCap = seatCapForFirmTier(derived.planTier, firm.created_at);
  // firm_scale + add-on legitimately exceeds 35 (add-on covers the excess).
  const effectiveCap = derived.addonQuantity > 0 ? newCap + derived.addonQuantity : newCap;
  if (rosterCount > effectiveCap) {
    console.log(`[subscription-sync] firm ${firm.id} now on ${derived.planTier} (cap ${effectiveCap}) with ${rosterCount} staff`);
    if (
      requireSendApproval(env, "planChangeOverCapNotice") &&
      env.RESEND_API_KEY &&
      !firm.demo_locked &&
      !firm.is_test_tenant &&
      (await store.recordWebhookEventIfNew(env.DB, `${eventId}:overcap`, "customer.subscription.updated:overcap", firm.id))
    ) {
      await sendEmail(
        env.RESEND_API_KEY,
        firm.admin_email,
        buildPlanChangeOverCapEmail({
          firmName: firm.name,
          newTierLabel: firmTierByPlanTier(derived.planTier)?.label ?? derived.planTier,
          newSeatCap: effectiveCap,
          rosterCount,
          dashboardUrl,
        }),
        env.EMAIL_ALLOWLIST,
        env.EMAIL_PREVIEW_LOG_BODY
      );
    }
  }
}
