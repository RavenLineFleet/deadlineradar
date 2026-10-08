#!/usr/bin/env python3
"""Real Stripe TEST-mode check for self-serve plan change (2026-10-07).

Never mocked, never live mode. Proves against Stripe's real API what the
Worker tests can only assume:

  1. every one of the 5 portal configurations (configure_stripe_portal.py) is
     accepted by billing_portal/sessions for a real customer -> a hosted URL;
  2. a real subscription's item/price shape is what tiers.ts
     deriveSubscriptionState() reads (items.data[].price.id / .quantity), after
     each kind of switch the portal performs (tier up, tier down, annual ->
     monthly, monthly -> annual) -- the switches are made with the same API call
     the portal makes (POST /v1/subscriptions/{id} items[0][price],
     proration_behavior=create_prorations);
  3. the referral coupon (duration=once, applied to the subscription exactly
     like applyCouponToSubscription()) survives or is consumed by a portal
     switch -- reports what the next invoice does so the answer is measured,
     not assumed.

Writes the fetched subscription JSON after each step to
worker/test/fixtures/stripe_subscription_after_*.json (customer/ids scrubbed)
so the Worker's deriveSubscriptionState test runs on REAL payloads.
Cleans up every object it creates (subscription, customer, coupon).

    python scripts/check_stripe_portal_integration.py [--write-fixtures]

Exit 0 = every assertion held; 1 = any failure.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_ENV = REPO_ROOT.parent / ".secrets" / "stripe.env"
FIXTURES = REPO_ROOT / "worker" / "test" / "fixtures"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if SECRETS_ENV.exists():
        for line in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


class Api:
    def __init__(self, secret: str):
        self._auth = "Basic " + base64.b64encode(f"{secret}:".encode()).decode()

    def call(self, method: str, path: str, params: list[tuple[str, str]] | None = None) -> dict:
        data = urllib.parse.urlencode(params).encode() if params else None
        req = urllib.request.Request(
            "https://api.stripe.com/v1/" + path, data=data, method=method,
            headers={"Authorization": self._auth, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {json.load(e).get('error', {}).get('message')}")


failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        failures.append(label)


def scrub(sub: dict) -> dict:
    """Keep the shape the Worker reads, drop identifying values."""
    return {
        "id": "sub_FIXTURE", "status": sub["status"], "customer": "cus_FIXTURE",
        "cancel_at_period_end": sub["cancel_at_period_end"],
        "items": {"data": [
            {"price": {"id": i["price"]["id"]}, "quantity": i["quantity"], "current_period_end": i.get("current_period_end")}
            for i in sub["items"]["data"]
        ]},
    }


def main() -> int:
    write_fixtures = "--write-fixtures" in sys.argv
    env = load_env()
    secret = env.get("STRIPE_TEST_SECRET_KEY")
    if not secret or not secret.startswith("sk_test_"):
        raise SystemExit("REFUSING: needs STRIPE_TEST_SECRET_KEY (sk_test_*); this check never touches live mode")
    api = Api(secret)
    cfg_path = REPO_ROOT.parent / ".secrets" / "portal_configs_test.json"
    configs = json.loads(cfg_path.read_text(encoding="utf-8"))
    P = {}
    for tier in ("STARTER", "GROWTH", "STANDARD", "SCALE"):
        P[f"{tier}_a"] = env[f"STRIPE_TEST_V2_PRICE_FIRM_{tier}"]
        P[f"{tier}_m"] = env[f"STRIPE_TEST_V2_PRICE_FIRM_{tier}_MONTHLY"]

    cust = sub = coupon = None
    try:
        cust = api.call("POST", "customers", [("email", "portal-check@example.invalid"), ("metadata[dr_check]", "portal")])["id"]
        pm = api.call("POST", "payment_methods/pm_card_visa/attach", [("customer", cust)])["id"]
        api.call("POST", f"customers/{cust}", [("invoice_settings[default_payment_method]", pm)])
        coupon = api.call("POST", "coupons", [("percent_off", "10"), ("duration", "once"), ("name", "portal-check-referral")])["id"]
        sub = api.call("POST", "subscriptions", [("customer", cust), ("items[0][price]", P["GROWTH_a"]), ("metadata[dr_check]", "portal")])["id"]
        # Exactly applyCouponToSubscription(): discounts[0][coupon] on the live subscription.
        api.call("POST", f"subscriptions/{sub}", [("discounts[0][coupon]", coupon)])

        print("1. portal sessions for all 5 configurations")
        for key, bpc in configs.items():
            s = api.call("POST", "billing_portal/sessions", [("customer", cust), ("configuration", bpc), ("return_url", "https://deadline-radar.com/firm-dashboard/#account")])
            check(s.get("url", "").startswith("https://billing.stripe.com/"), f"config {key}: session URL minted")
            if key == "firm_growth":
                print("     (portal URL for a manual look is not printed -- it is a live capability link)")

        def snapshot(label: str, expect_price: str, fixture: str) -> dict:
            s = api.call("GET", f"subscriptions/{sub}")
            invs = api.call("GET", f"invoices?subscription={sub}&limit=20")["data"]
            print(f"     [{label}] discounts on sub={len(s.get('discounts') or [])}; invoices (newest first)="
                  f"{[(i['total'], sum(d.get('amount', 0) for d in (i.get('total_discount_amounts') or []))) for i in invs]} (total, discount)")
            items = s["items"]["data"]
            check(len(items) == 1 and items[0]["price"]["id"] == expect_price and items[0]["quantity"] == 1, f"{label}: one item, expected price id, quantity 1")
            check(s["status"] == "active", f"{label}: status active")
            if write_fixtures:
                FIXTURES.mkdir(parents=True, exist_ok=True)
                (FIXTURES / f"stripe_subscription_after_{fixture}.json").write_text(json.dumps(scrub(s), indent=2) + "\n", encoding="utf-8")
            return s

        def switch(to_price: str) -> None:
            item = api.call("GET", f"subscriptions/{sub}")["items"]["data"][0]["id"]
            api.call("POST", f"subscriptions/{sub}", [("items[0][id]", item), ("items[0][price]", to_price), ("proration_behavior", "create_prorations")])

        print("2. switches the portal performs, then the shape the Worker reads")
        snapshot("start growth/annual", P["GROWTH_a"], "growth_annual")
        switch(P["STANDARD_a"]); snapshot("tier up -> standard/annual", P["STANDARD_a"], "standard_annual")
        switch(P["STARTER_a"]); snapshot("tier down -> starter/annual", P["STARTER_a"], "starter_annual")
        switch(P["STARTER_m"]); s = snapshot("annual -> monthly (starter)", P["STARTER_m"], "starter_monthly")
        switch(P["SCALE_a"]); snapshot("monthly -> annual (scale)", P["SCALE_a"], "scale_annual")

        print("3. referral coupon (duration=once) across a portal switch")
        s = api.call("GET", f"subscriptions/{sub}?expand[]=latest_invoice")
        inv = s.get("latest_invoice") or {}
        disc = s.get("discounts") or []
        print(f"     discounts on subscription after the switches: {len(disc)}")
        print(f"     latest invoice: total={inv.get('total')} amount_due={inv.get('amount_due')} "
              f"total_discount_amounts={[d.get('amount') for d in (inv.get('total_discount_amounts') or [])]}")
        check(True, "coupon behaviour measured (see the two lines above; report them, do not assume)")

    finally:
        for fn, label in ((lambda: sub and api.call("DELETE", f"subscriptions/{sub}"), "subscription"),
                          (lambda: cust and api.call("DELETE", f"customers/{cust}"), "customer"),
                          (lambda: coupon and api.call("DELETE", f"coupons/{coupon}"), "coupon")):
            try:
                fn()
            except Exception as e:  # cleanup must not mask the result
                print(f"  (cleanup {label} failed: {e})")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
