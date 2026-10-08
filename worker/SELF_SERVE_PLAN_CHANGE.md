# Self-serve plan / billing-period change (Stripe Customer Portal)

Devin 2026-10-07: customers change monthly<->annual and plan up/down without contacting support.
Ships DARK. Nothing below runs until the enable sequence at the bottom.

## How it works
- `POST /firm/billing/portal` (partner-only, origin-checked, rate-limited, demo-locked) returns a Stripe
  portal URL for the firm's own customer. Dashboard "Manage billing" button calls it.
- The portal does plan switch, monthly<->annual, proration (`create_prorations`), cancel (at period end),
  payment method, invoices. Customer-profile editing is OFF (the account email is our login identity).
- `customer.subscription.updated` -> `subscription_sync.ts` mirrors the result into `firms`
  (plan_tier, billing_interval, cancel_at_period_end, current_period_end). It REFETCHES the subscription
  (order-safe), is replay-safe, and NEVER GUESSES: an unrecognised price/quantity/shape leaves the row
  untouched and raises a consent-gated internal alert.

## Findings that shaped it (all measured against Stripe test mode)
1. **The portal rejects the old price layout.** All 8 firm prices lived on ONE product; the portal needs
   unique intervals per product. `scripts/create_stripe_tier_products.py` creates one Product per tier with
   its annual + monthly Price (amounts parsed from `tiers.ts`). At deploy the Worker's 8
   `STRIPE_PRICE_FIRM_*` secrets are re-pointed at the new ids (names unchanged). Live had zero real
   subscriptions at build time, so no legacy-price mapping is needed (re-check before enabling).
2. **Downgrade below staff count is prevented, not repaired.** A paid firm over its tier cap is not
   roster-paused (`reconcileRosterPauseState` acts on unpaid firms only). So 5 portal configurations
   exist ("smallest tier the firm may switch to" + `none`) and the Worker picks one from the LIVE roster
   (`portalConfigurationIdForRoster`). Race (staff added after the session was minted): the new tier is
   applied, nobody is paused/removed, partner is emailed (consent-gated), existing seat-cap blocks adds.
3. **A plan switch DROPS a pending `once` discount** without applying it to any invoice (measured). A
   referrer's reward coupon would silently vanish. The sync re-applies the referrer's tier coupon when the
   event shows `previous_attributes.discounts` non-empty AND `items` changed AND none remain.
4. Firms carrying the per-seat add-on (2 items) get the no-switching configuration (portal cannot switch
   multi-item subscriptions): they can still cancel / update payment / see invoices.
5. A paid firm whose current tier is not in its roster-selected configuration (over-cap) may see no
   "Update subscription" in the portal; cancel/payment still work. Not exercised (rare).
6. The add-on (no-switching configuration) is read from the subscription's items, not inferred from roster size;
   an unfetchable/unrecognised subscription also gets the no-switching configuration.
7. A failed referral-coupon re-apply throws (webhook non-2xx -> Stripe retries) BEFORE the tier write, so the retry
   still sees the switch and re-applies. Bare discount ids in `previous_attributes` cannot be tied to a coupon;
   only object-form discounts are checked against the referral prefix (residual: a dashboard courtesy coupon
   dropped by a switch would be replaced by the referral coupon when the firm has recorded rewards).

## Consent gates (new send triggers -- held OFF, per the no-new-send-triggers policy)
`SEND_APPROVED_PASSES` names: `billingSyncAlert` (internal), `planChangeOverCapNotice` (partner email).
`SEND_APPROVED_PASSES` is a single write-only comma list -- read the current value from the deploy record
and put the FULL list back (see AuditLab BILL-32) before adding either name.

## Enable sequence (after SecurityLab + AuditLab PASS)
Each step that touches live Stripe/Worker config is its own plan-first with pre/post readback.
1. `python scripts/create_stripe_tier_products.py --mode live --apply`   (objects only, no charges)
2. `python scripts/configure_stripe_portal.py --mode live --apply`       (config only) -> `.secrets/portal_configs_live.json`
3. Live reconciliation with the V2 ids mapped onto the canonical names (`check_stripe_price_reconciliation.py`).
4. `wrangler secret put` x8 `STRIPE_PRICE_FIRM_*[_MONTHLY]` (V2 ids), x1 `STRIPE_PORTAL_CONFIGS`.
5. Deploy the Worker with `SELF_SERVE_PLAN_CHANGE` still OFF (handler present, no-ops; site literals still off).
6. **Subscribe `customer.subscription.updated` on the live webhook endpoint (AuditLab BILL-35 -- a hard gate).**
   `POST /v1/webhook_endpoints/<id>` additive on `enabled_events` (keep the existing four), pre/post readback,
   no secret printed; rollback = POST the prior four back. The sync has exactly ONE entry point (this webhook): if
   the event is not delivered the dashboard silently keeps the old plan while Stripe bills the new one.
   Do this BEFORE step 8; never after.
7. **Add `billingSyncAlert` and `planChangeOverCapNotice` to `SEND_APPROVED_PASSES`** (SecurityLab HIGH-1). Without them
   every safeguard in this design -- the unknown-state alert, the failed-coupon-re-apply alert, the downgrade-race
   partner email -- is console.log only. `SEND_APPROVED_PASSES` is write-only: read the current list from the
   deploy record and put the FULL list back (BILL-32) with the two names appended. Both are send triggers:
   needs Devin's explicit consent per the no-new-send-triggers policy before this step.
8. Set `SELF_SERVE_PLAN_CHANGE=on` (secret), flip BOTH generate.py literals, rebuild + deploy site.
9. Verify with Devin's own test subscription: portal switch -> `/api/firm/licenses` shows new tier; and confirm a
   `customer.subscription.updated` row lands in `stripe_webhook_events` (proves delivery, not just the handler).

Rollback: unset `SELF_SERVE_PLAN_CHANGE` (route 404s, sync no-ops); flip the generate.py literals back.

## Verification scripts
`scripts/check_stripe_portal_integration.py` (real test-mode API, cleans up), `worker/test/subscription-*.spec.ts`.
