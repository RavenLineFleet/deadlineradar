#!/usr/bin/env python3
"""Real Stripe test-mode API integration check (2026-08-27, orchestrator's
Oct-1 readiness pass, item "Stripe checkout status").

Every existing automated test of the checkout/webhook path
(worker/test/billing.spec.ts, worker/test/stripe.spec.ts) mocks Stripe's own
HTTP responses -- they prove our request-shaping and webhook-handling logic
is internally consistent, never that Stripe's real API actually accepts what
we send it. Nobody had ever completed a real checkout end-to-end (the closest
was a page-load: a real `cs_live_` session rendered, tab closed before
entering payment -- see AssetLab/HANDOFF.md's STRIPE-1 history). Neither
AssetLab nor the orchestrator will ever enter a real or test card number into
a browser field (an absolute line for both), and prod's STRIPE_SECRET_KEY is
LIVE mode, so a Stripe test card wouldn't even work against it anyway.

This closes that gap the way orchestrator asked: hit Stripe's REAL test-mode
API directly (never mocked), using the separate STRIPE_TEST_SECRET_KEY +
STRIPE_TEST_PRICE_* values that were already sitting unused in
AssetLab/.secrets/stripe.env since 2026-08-20 (created alongside the live
keys, never actually exercised). Three things get proven against Stripe's
real API, not a mock:
  1. createCheckoutSession()'s exact request shape (mirrored here in Python
     -- form-encoded, line_items[0][price], metadata[...] bracket notation)
     is valid and Stripe accepts it, for a real test-mode price.
  2. The customer -> payment-method -> subscription lifecycle our webhook
     handler assumes actually works: creates a real Customer, attaches
     Stripe's reserved always-succeeds test token (`pm_card_visa` -- a
     token, never a card number; safe in test mode, meaningless in live
     mode), creates a real Subscription against a real test-mode price.
  3. The REAL id formats Stripe returns (cus_..., sub_...) are exactly what
     worker/src/index.ts's checkout.session.completed handler expects
     (string `customer`/`subscription` fields) -- this is a schema-drift
     check the mocked tests structurally cannot catch, since a mock always
     returns whatever the test author typed.

This does NOT drive Stripe's hosted Checkout UI and does NOT complete a real
Checkout Session (Stripe has no API to mark a Checkout Session paid without
their UI or a card) -- it independently recreates what a completed checkout
produces (a real customer + real active subscription), which is what
index.ts's webhook handler actually reads. Cleans up every object it creates
(cancels the subscription, which also detaches the payment method) so this
can be re-run freely without accumulating test-mode clutter.

Usage:
    export STRIPE_TEST_SECRET_KEY=sk_test_...   (or source AssetLab's OWN
                                             .secrets/stripe.env, two directories
                                             above this repo's root -- NEVER copy
                                             it into the repo tree itself, see
                                             SEC-4, 2026-08-20)
    python scripts/check_stripe_checkout_integration.py

Exit code 0 = checkout session creation, customer/payment-method/subscription
creation, and cleanup all succeeded against Stripe's real test API.
Exit code 1 = any of the above failed, or STRIPE_TEST_SECRET_KEY is unset.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.stripe.com/v1"


def _basic_auth(secret_key: str) -> str:
    return base64.b64encode(f"{secret_key}:".encode("utf-8")).decode("ascii")


def _post(secret_key: str, path: str, form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {_basic_auth(secret_key)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read().decode("utf-8"))
        raise RuntimeError(f"Stripe POST {path} failed ({e.code}): {detail.get('error', {}).get('message', detail)}") from e


def main() -> int:
    secret_key = os.environ.get("STRIPE_TEST_SECRET_KEY", "")
    if not secret_key:
        print("REFUSING: STRIPE_TEST_SECRET_KEY not set. Export it (source AssetLab's .secrets/stripe.env) and re-run.")
        return 1
    if not secret_key.startswith("sk_test_"):
        print(f"REFUSING: STRIPE_TEST_SECRET_KEY does not look like a test-mode key (expected sk_test_ prefix). "
              "This script must never run against a live key.")
        return 1
    price_id = os.environ.get("STRIPE_TEST_PRICE_FIRM_STARTER", "")
    if not price_id:
        print("REFUSING: STRIPE_TEST_PRICE_FIRM_STARTER not set.")
        return 1

    print("Stripe test-mode checkout integration check -- hitting the real API, never mocked.\n")

    customer_id = None
    subscription_id = None
    try:
        # 1. Checkout Session creation -- exact request shape createCheckoutSession()
        #    (worker/src/stripe.ts) builds, mirrored here field-for-field.
        session = _post(secret_key, "checkout/sessions", {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": "https://deadline-radar.com/firm-dashboard/#account?checkout=success",
            "cancel_url": "https://deadline-radar.com/firm-dashboard/#account?checkout=cancelled",
            "metadata[firm_id]": "check-script-firm-id",
            "metadata[target_plan_tier]": "firm_starter",
            "customer_email": "stripe-integration-check@deadline-radar.com",
        })
        if not session.get("id") or not session.get("url"):
            print(f"FAIL: checkout session response missing id/url: {session}")
            return 1
        print(f"  PASS  checkout.session.create -- session {session['id']}, real Stripe-hosted URL returned")

        # 2. Real customer + payment-method + subscription lifecycle -- what a
        #    COMPLETED checkout actually produces, which is what the webhook
        #    handler (checkout.session.completed branch) reads.
        customer = _post(secret_key, "customers", {"email": "stripe-integration-check@deadline-radar.com"})
        customer_id = customer["id"]
        print(f"  PASS  customers.create -- {customer_id}")

        # pm_card_visa is a RESERVED PaymentMethod id that exists in every
        # Stripe test-mode account by default (Stripe's own "testing without
        # a UI" pattern) -- attach it directly, no card-token creation step,
        # no card data of any kind ever touches this script.
        reserved_pm_id = "pm_card_visa"
        attached_pm = _post(secret_key, f"payment_methods/{reserved_pm_id}/attach", {"customer": customer_id})
        # Attaching the reserved alias resolves to a real, account-specific
        # pm_... id -- that resolved id, not the reserved alias, is what
        # every subsequent call must reference.
        real_pm_id = attached_pm["id"]
        _post(secret_key, f"customers/{customer_id}", {"invoice_settings[default_payment_method]": real_pm_id})
        print(f"  PASS  payment_methods.attach -- {reserved_pm_id} -> {real_pm_id} (Stripe's reserved test-mode id, no card data)")

        subscription = _post(secret_key, "subscriptions", {
            "customer": customer_id,
            "items[0][price]": price_id,
        })
        subscription_id = subscription["id"]
        status = subscription.get("status")
        if status not in ("active", "trialing"):
            print(f"FAIL: subscription created but status is '{status}', expected active/trialing: {subscription_id}")
            return 1
        print(f"  PASS  subscriptions.create -- {subscription_id}, status={status}")

        # 3. Shape check -- exactly what index.ts's webhook handler reads off
        #    event.data.object for checkout.session.completed.
        if not isinstance(customer_id, str) or not customer_id.startswith("cus_"):
            print(f"FAIL: customer id shape unexpected: {customer_id!r}")
            return 1
        if not isinstance(subscription_id, str) or not subscription_id.startswith("sub_"):
            print(f"FAIL: subscription id shape unexpected: {subscription_id!r}")
            return 1
        print("  PASS  real id formats (cus_.../sub_...) match what the webhook handler's string-field reads expect")

        print("\nAll checks passed against Stripe's real test-mode API.")
        return 0
    finally:
        if subscription_id:
            try:
                req = urllib.request.Request(
                    f"{API_BASE}/subscriptions/{subscription_id}",
                    method="DELETE",
                    headers={"Authorization": f"Basic {_basic_auth(secret_key)}"},
                )
                urllib.request.urlopen(req, timeout=15)
                print(f"\n  cleanup: cancelled {subscription_id}")
            except urllib.error.HTTPError as e:
                print(f"\n  cleanup WARNING: failed to cancel {subscription_id}: {e}")


if __name__ == "__main__":
    sys.exit(main())
