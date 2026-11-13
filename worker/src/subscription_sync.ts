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
 *  - A transient Stripe/D1 failure throws (-> non-2xx -> Stripe retries the
 *    event); a permanent Stripe 4xx on the referral re-apply alerts and the
 *    tier is mirrored anyway (MED-B).
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
import { StripeApiError, applyCouponToSubscription, fetchStripeSubscription } from "./stripe";
import {
  selfServePlanChangeEnabled,
  deriveSubscriptionState,
  firmTierByPlanTier,
  referralTierCouponId,
  seatCapForFirmTier,
} from "./tiers";
import { requireSendApproval } from "./scheduler";
import { sendEmail } from "./sender";
import { buildBillingSyncAlertEmail, buildPlanChangeOverCapEmail, buildWebhookSignatureFailureAlertEmail } from "./emails";

const INTERNAL_NOTIFY_EMAIL = "support@deadline-radar.com";
const SYNCED_STATUSES = new Set(["active", "trialing", "past_due"]);
/** Statuses in which the subscription no longer pays for anything, so the
 * firm drops to `free` (SecurityLab SEC-83). Before mid-trial checkout every
 * paid tier had been paid at least once; a deferred checkout grants the tier
 * with nothing charged, so a first charge that fails and ends in `unpaid`
 * (Stripe's "mark unpaid" failed-payment setting) or `paused` must revoke it
 * here -- customer.subscription.deleted only fires on cancellation. The
 * subscription id is kept, so a later recovery to `active` re-derives the
 * tier through the normal path below. `past_due` stays entitled; how long
 * it lasts is the retry schedule PLUS the failed-payment setting: "cancel"
 * and "mark unpaid" end it (deleted / `unpaid` above), "leave past_due"
 * never does (SEC-85, needs the live dashboard read). */
const REVOKED_STATUSES = new Set(["unpaid", "incomplete_expired", "paused"]);

/** The dropped discounts must not be a non-referral coupon (SecurityLab LOW-1):
 * a dashboard-applied courtesy coupon vanishing in a switch must not be
 * "replaced" by a referral-tier coupon. When Stripe sends the discount objects
 * the coupon id is checked against the referral prefix; when it sends bare
 * discount ids (the default) the coupon is not knowable from the event, and
 * referral is the only coupon source in code, so it is accepted. */
function droppedDiscountsAreReferralCoupons(prev: unknown[], referralPrefix: string): boolean {
  return prev.every((d) => {
    if (d && typeof d === "object") {
      const coupon = (d as { coupon?: { id?: unknown } | string }).coupon;
      if (typeof coupon === "string") return coupon.startsWith(referralPrefix);
      if (coupon && typeof coupon.id === "string") return coupon.id.startsWith(referralPrefix);
    }
    return true;
  });
}

/** Worth retrying: a network failure, Stripe 5xx, or 429. Anything else
 * (a 4xx such as a coupon object that does not exist in live mode) will fail
 * identically on every retry. */
function isTransientStripeError(err: unknown): boolean {
  if (err instanceof StripeApiError) return err.status === 429 || err.status >= 500;
  return true;
}

export async function alertUnsynced(env: Env, eventId: string, firmId: string, subscriptionId: string, problem: string): Promise<void> {
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

/**
 * BILL-68 (AuditLab 2026-11-12, CRITICAL; detector per SecurityLab 17:05Z):
 * the webhook's 400 "Invalid signature." exit was completely silent, which is
 * how a test-endpoint secret on a live key sat unnoticed for 99 days. Every
 * failure with a Stripe-Signature header now logs, and the first per UTC day
 * leaves a durable `stripe_sig_fail:<day>` ledger row (readable in D1 even
 * with the send off) and, only if "webhookSignatureFailureAlert" is in
 * SEND_APPROVED_PASSES, one internal email. A request with no header is a
 * scanner, not Stripe, and is only logged. Never throws: the caller's 400
 * must not turn into a 500.
 *
 * SEC-91 (SecurityLab 18:52Z, AuditLab 19:07Z, 2026-11-12, LOW): the evidence
 * row and the email's once-a-day claim are separate keys. The evidence row is
 * written first and unconditionally (BILL-68's point: it must not depend on
 * the send being approved). The email claims `stripe_sig_fail_alert:<day>`
 * only AFTER the send gate, so a closed gate never spends the day's email,
 * and a failed send releases the claim so a later failure can retry it.
 * Not closed, by design: any anonymous POST with a junk Stripe-Signature can
 * still take the day's one email slot (bounded to one email a day; the 400
 * and the evidence row are unaffected).
 */
export async function noteWebhookSignatureFailure(env: Env, hadSignatureHeader: boolean): Promise<void> {
  console.warn(
    `[stripe-webhook] signature verification FAILED (Stripe-Signature ${hadSignatureHeader ? "present" : "absent"}); ` +
      `if Stripe sent this, STRIPE_WEBHOOK_SECRET does not match the endpoint (BILL-68)`
  );
  if (!hadSignatureHeader) return;
  try {
    const dayUtc = new Date().toISOString().slice(0, 10);
    await store.recordDailyWebhookMarkerIfNew(env.DB, `stripe_sig_fail:${dayUtc}`, "stripe_sig_fail");
    if (!requireSendApproval(env, "webhookSignatureFailureAlert") || !env.RESEND_API_KEY) return;
    const alertId = `stripe_sig_fail_alert:${dayUtc}`;
    const claimed = await store.recordDailyWebhookMarkerIfNew(env.DB, alertId, "stripe_sig_fail_alert");
    if (!claimed) return;
    let sent = false;
    try {
      sent = await sendEmail(
        env.RESEND_API_KEY,
        INTERNAL_NOTIFY_EMAIL,
        buildWebhookSignatureFailureAlertEmail({ dayUtc }),
        env.EMAIL_ALLOWLIST,
        env.EMAIL_PREVIEW_LOG_BODY
      );
    } finally {
      if (!sent) await store.releaseDailyWebhookMarker(env.DB, alertId);
    }
  } catch (err) {
    console.log(`[stripe-webhook] signature-failure record/alert failed: ${String(err)}`);
  }
}

/**
 * BILL-69 (AuditLab 2026-11-12, MEDIUM): the SEC-86 mode-mismatch 400 was
 * silent, and it is the exit that carries traffic (all 21 of 11-12's
 * test-clock events would land here). A buggy mode guard looks exactly like
 * BILL-68: card charged, tier never flips, no trace. Every rejection now
 * logs, and the first per UTC day leaves a `stripe_mode_reject:<day>` row.
 * No email (no new send trigger). Only Stripe-signed events reach this exit.
 * Never throws: the caller's 400 must not turn into a 500.
 */
export async function noteWebhookModeMismatch(
  env: Env,
  eventId: string,
  eventType: string,
  eventLivemode: unknown,
  expectedLivemode: boolean | null
): Promise<void> {
  console.warn(
    `[stripe-webhook] event mode MISMATCH: ${eventId} (${eventType}) livemode=${JSON.stringify(eventLivemode) ?? "absent"}, ` +
      `deployment expects ${expectedLivemode === null ? "unknown (key prefix unrecognised)" : String(expectedLivemode)}; rejected (SEC-86/BILL-69)`
  );
  try {
    const dayUtc = new Date().toISOString().slice(0, 10);
    await store.recordDailyWebhookMarkerIfNew(env.DB, `stripe_mode_reject:${dayUtc}`, "stripe_mode_reject");
  } catch (err) {
    console.log(`[stripe-webhook] mode-mismatch record failed: ${String(err)}`);
  }
}

/**
 * BILL-69 widening (SecurityLab 17:56Z + AuditLab 18:06Z, 2026-11-12, LOW):
 * the two other post-signature exits, "Invalid payload." and "Invalid event.",
 * were still silent. Only a validly-signed sender (Stripe) reaches them, so a
 * drop there is the same defect class as the mode exit: Stripe gives up after
 * its retries and nothing on our side says so. Logs every drop and leaves one
 * `stripe_drop:<UTC day>` row. No email. Never throws.
 */
export async function noteWebhookDroppedEvent(env: Env, reason: "invalid_payload" | "invalid_event"): Promise<void> {
  console.warn(`[stripe-webhook] signed event DROPPED (${reason}); Stripe sent a body this handler cannot use (BILL-69)`);
  try {
    const dayUtc = new Date().toISOString().slice(0, 10);
    await store.recordDailyWebhookMarkerIfNew(env.DB, `stripe_drop:${dayUtc}`, "stripe_drop");
  } catch (err) {
    console.log(`[stripe-webhook] drop record failed: ${String(err)}`);
  }
}

/**
 * SEC-90 remedy 1 (SecurityLab 18:13Z, AuditLab 18:17Z, 2026-11-12, HIGH):
 * the /stripe/webhook dispatcher caught every handler throw with a bare
 * `catch {` and no trace. This makes any throw visible: console.error with
 * the error text, plus one `stripe_handler_throw:<UTC day>` row. The daily
 * row means "something in the handler threw today", from ANY point, before
 * or after the ledger insert (SEC-92): the dispatcher cannot tell which.
 * The per-event record is `<event id>:throw`, written inside
 * handleStripeWebhook for throws after the event is parsed. Repair is
 * remedy 3 (store.reclaimWebhookEvent). No email. Never throws.
 */
export async function noteWebhookHandlerThrow(env: Env, err: unknown): Promise<void> {
  console.error(`[stripe-webhook] handler threw, returning 400 so Stripe retries (SEC-90): ${String(err)}`);
  try {
    const dayUtc = new Date().toISOString().slice(0, 10);
    await store.recordDailyWebhookMarkerIfNew(env.DB, `stripe_handler_throw:${dayUtc}`, "stripe_handler_throw");
  } catch (markErr) {
    console.log(`[stripe-webhook] handler-throw record failed: ${String(markErr)}`);
  }
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
  // unpaid/incomplete_expired/paused revoke below; incomplete is not a state
  // this mirror entitles on (and leaves a free firm free).
  if (!SYNCED_STATUSES.has(snapshot.status) && !REVOKED_STATUSES.has(snapshot.status)) return;
  if (firm.stripe_customer_id && snapshot.customerId && snapshot.customerId !== firm.stripe_customer_id) {
    await alertUnsynced(env, eventId, firm.id, subscriptionId, "subscription belongs to a different Stripe customer than the firm record");
    return;
  }
  if (REVOKED_STATUSES.has(snapshot.status)) {
    if (firm.plan_tier !== "free") {
      await store.updateFirmBilling(env.DB, firm.id, {
        planTier: "free",
        stripeCustomerId: firm.stripe_customer_id ?? snapshot.customerId ?? "",
        stripeSubscriptionId: subscriptionId,
      });
      try {
        await store.reconcileRosterPauseState(env.DB, firm.id);
      } catch (err) {
        console.log(`[subscription-sync] roster-pause reconcile failed for firm ${firm.id}: ${String(err)}`);
      }
      console.log(`[subscription-sync] firm ${firm.id} reverted to free: subscription ${subscriptionId} is ${snapshot.status}`);
    }
    return;
  }

  // cancel_at_period_end / current_period_end are display state that does not
  // depend on the tier, so they are mirrored BEFORE the derive gate: a portal
  // cancel (or resume) on a subscription whose shape is not recognised must
  // still show on the dashboard (portal cancel and resume both land here).
  if (
    snapshot.currentPeriodEnd &&
    (Boolean(firm.cancel_at_period_end) !== snapshot.cancelAtPeriodEnd || firm.current_period_end !== snapshot.currentPeriodEnd)
  ) {
    await store.updateFirmCancellation(env.DB, firm.id, {
      cancelAtPeriodEnd: snapshot.cancelAtPeriodEnd,
      currentPeriodEnd: snapshot.currentPeriodEnd,
    });
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
  // redelivery is harmless). A TRANSIENT failure (network, 5xx, 429) alerts and
  // THROWS before the tier write, so the webhook goes non-2xx and Stripe retries
  // it (tier row still stale => tierChanged still true => this branch runs
  // again); swallowing it would let the tier write land and the reward be lost
  // for good (SecurityLab MEDIUM-1). A PERMANENT failure (4xx, e.g. the per-tier
  // coupon object does not exist) alerts and CONTINUES: retrying cannot succeed,
  // and the money-correct tier mirror must never be blocked by the courtesy
  // reward (SecurityLab MEDIUM-B) -- the alert is the recovery signal.
  const prevDiscounts = previousAttributes?.discounts;
  if (
    tierChanged &&
    Array.isArray(prevDiscounts) &&
    prevDiscounts.length > 0 &&
    previousAttributes !== null &&
    "items" in previousAttributes &&
    snapshot.discountCount === 0 &&
    env.STRIPE_COUPON_REFERRAL &&
    droppedDiscountsAreReferralCoupons(prevDiscounts, env.STRIPE_COUPON_REFERRAL)
  ) {
    try {
      const rewarded = await store.countRewardedReferrals(env.DB, firm.id);
      if (rewarded > 0) {
        await applyCouponToSubscription(env.STRIPE_SECRET_KEY, subscriptionId, referralTierCouponId(env.STRIPE_COUPON_REFERRAL, rewarded));
      } else {
        await alertUnsynced(env, eventId, firm.id, subscriptionId, "plan switch removed a discount but the firm has no recorded referral reward to re-apply");
      }
    } catch (err) {
      const transient = isTransientStripeError(err);
      await alertUnsynced(
        env,
        eventId,
        firm.id,
        subscriptionId,
        `could not re-apply the referral reward after a plan switch (${transient ? "transient, event will be retried" : "permanent, tier mirrored without it"}): ${String(err)}`
      );
      if (transient) throw err;
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
