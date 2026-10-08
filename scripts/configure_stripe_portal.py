#!/usr/bin/env python3
"""Create / update the Stripe Customer Portal configurations behind
POST /firm/billing/portal (self-serve plan change, 2026-10-07).

One configuration per "smallest tier the firm may switch to", because a portal
configuration's plan list is per-configuration, not per-customer, and a paid
firm downgrading below its staff count is NOT roster-paused (see
worker/src/subscription_sync.ts): the Worker picks the configuration from the
firm's live roster (tiers.ts portalConfigurationIdForRoster()), so a firm is
never offered a tier that cannot hold its staff.

    firm_starter   -> Essentials, Growth, Professional, Enterprise  (annual + monthly each)
    firm_growth    ->             Growth, Professional, Enterprise
    firm_standard  ->                     Professional, Enterprise
    firm_scale     ->                                   Enterprise
    none           -> NO plan switching (roster > 35, or a firm_scale + per-seat
                      add-on subscription, which the portal cannot switch)

Every configuration: payment-method update ON, invoice history ON, cancel ON
(at period end, no proration/refund -- matches the app's own no-refund
cancel), customer profile editing OFF (the account email is our login
identity; it must not diverge from Stripe's copy), plan switching with
proration_behavior=create_prorations, quantity editing OFF.

Idempotent: configurations are found by metadata[dr_portal_key] and UPDATED in
place; a re-run never creates duplicates. Dry-run by default.

    python scripts/configure_stripe_portal.py --mode test            # show plan
    python scripts/configure_stripe_portal.py --mode test --apply
    python scripts/configure_stripe_portal.py --mode live --apply    # config only, no charges

Keys come from the environment (STRIPE_TEST_SECRET_KEY / STRIPE_SECRET_KEY) or
AssetLab's own .secrets/stripe.env (two directories above this repo) -- never
printed. Prints the STRIPE_PORTAL_CONFIGS JSON (ids are not secrets) and writes
it to .secrets/portal_configs_<mode>.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIER_ORDER = ["STARTER", "GROWTH", "STANDARD", "SCALE"]
KEYS = {  # config key -> tiers it may switch to
    "firm_starter": ["STARTER", "GROWTH", "STANDARD", "SCALE"],
    "firm_growth": ["GROWTH", "STANDARD", "SCALE"],
    "firm_standard": ["STANDARD", "SCALE"],
    "firm_scale": ["SCALE"],
    "none": [],
}
RETURN_URL = "https://deadline-radar.com/firm-dashboard/#account"
PRIVACY_URL = "https://deadline-radar.com/privacy/"
TERMS_URL = "https://deadline-radar.com/terms/"

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_ENV = REPO_ROOT.parent / ".secrets" / "stripe.env"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if SECRETS_ENV.exists():
        for line in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def price_env_names(mode: str, tier: str) -> tuple[str, str]:
    # V2 = the per-tier-Product prices from create_stripe_tier_products.py (the
    # portal rejects the original one-Product-for-everything layout).
    base = ("STRIPE_TEST_V2_PRICE_FIRM_" if mode == "test" else "STRIPE_V2_PRICE_FIRM_") + tier
    return base, base + "_MONTHLY"


class Stripe:
    def __init__(self, secret: str):
        self._auth = "Basic " + base64.b64encode(f"{secret}:".encode()).decode()

    def call(self, method: str, path: str, params: list[tuple[str, str]] | None = None) -> dict:
        data = urllib.parse.urlencode(params).encode() if params else None
        req = urllib.request.Request(
            "https://api.stripe.com/v1/" + path, data=data, method=method,
            headers={"Authorization": self._auth, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = json.load(e)
            raise SystemExit(f"Stripe {method} {path} -> {e.code}: {body.get('error', {}).get('message')}")


def build_params(key: str, products: dict[str, list[str]], mode: str, create: bool) -> list[tuple[str, str]]:
    p: list[tuple[str, str]] = [
        ("business_profile[headline]", "Manage your Deadline-Radar billing"),
        ("business_profile[privacy_policy_url]", PRIVACY_URL),
        ("business_profile[terms_of_service_url]", TERMS_URL),
        ("default_return_url", RETURN_URL),
        ("features[customer_update][enabled]", "false"),
        ("features[invoice_history][enabled]", "true"),
        ("features[payment_method_update][enabled]", "true"),
        ("features[subscription_cancel][enabled]", "true"),
        ("features[subscription_cancel][mode]", "at_period_end"),
        ("features[subscription_cancel][proration_behavior]", "none"),
        ("metadata[dr_portal_key]", key),
        ("metadata[dr_portal_mode]", mode),
    ]
    if products:
        p += [("features[subscription_update][enabled]", "true"), ("features[subscription_update][default_allowed_updates][0]", "price"),
              ("features[subscription_update][proration_behavior]", "create_prorations")]
        for i, (product, prices) in enumerate(products.items()):
            p.append((f"features[subscription_update][products][{i}][product]", product))
            for j, price in enumerate(prices):
                p.append((f"features[subscription_update][products][{i}][prices][{j}]", price))
    else:
        p.append(("features[subscription_update][enabled]", "false"))
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["test", "live"], required=True)
    ap.add_argument("--apply", action="store_true", help="create/update (default: dry-run)")
    args = ap.parse_args()

    env = load_env()
    secret = env.get("STRIPE_TEST_SECRET_KEY" if args.mode == "test" else "STRIPE_SECRET_KEY")
    if not secret:
        raise SystemExit("REFUSING: no secret key for this mode in the environment or .secrets/stripe.env")
    expected_prefix = "sk_test_" if args.mode == "test" else "sk_live_"
    if not secret.startswith(expected_prefix):
        raise SystemExit(f"REFUSING: --mode {args.mode} but the key is not a {expected_prefix}* key")
    api = Stripe(secret)

    # Resolve every price -> its product; refuse if prices are missing, inactive,
    # or sit on different products than expected (portal groups by product).
    price_ids: dict[str, dict[str, str]] = {}
    product_of: dict[str, str] = {}
    for tier in TIER_ORDER:
        annual_env, monthly_env = price_env_names(args.mode, tier)
        for interval, name in (("annual", annual_env), ("monthly", monthly_env)):
            pid = env.get(name)
            if not pid:
                raise SystemExit(f"REFUSING: {name} is not set")
            price = api.call("GET", f"prices/{pid}")
            if not price.get("active"):
                raise SystemExit(f"REFUSING: {name} ({pid}) is not active")
            want = "year" if interval == "annual" else "month"
            if (price.get("recurring") or {}).get("interval") != want:
                raise SystemExit(f"REFUSING: {name} ({pid}) is not a {want}ly price")
            price_ids.setdefault(tier, {})[interval] = pid
            product_of[pid] = price["product"]
    firm_products = set(product_of.values())
    print(f"mode={args.mode} firm-tier prices resolved: {len(product_of)} across {len(firm_products)} product(s)")
    if len(firm_products) != len(TIER_ORDER):
        raise SystemExit("REFUSING: expected exactly one Product per tier (the portal needs unique intervals per product); run create_stripe_tier_products.py")

    existing: dict[str, str] = {}
    listing = api.call("GET", "billing_portal/configurations?limit=100")
    for c in listing["data"]:
        k = (c.get("metadata") or {}).get("dr_portal_key")
        if k and (c.get("metadata") or {}).get("dr_portal_mode") == args.mode and c.get("active"):
            if k in existing:
                raise SystemExit(f"REFUSING: two active configurations carry dr_portal_key={k}; deactivate one by hand")
            existing[k] = c["id"]

    result: dict[str, str] = {}
    for key, tiers in KEYS.items():
        products: dict[str, list[str]] = {}
        for tier in tiers:
            for interval in ("annual", "monthly"):
                pid = price_ids[tier][interval]
                products.setdefault(product_of[pid], []).append(pid)
        params = build_params(key, products, args.mode, create=key not in existing)
        n_prices = sum(len(v) for v in products.values())
        action = "update" if key in existing else "create"
        print(f"  {key:14s} {action:6s} plan-switch prices offered: {n_prices}")
        if not args.apply:
            continue
        if key in existing:
            res = api.call("POST", f"billing_portal/configurations/{existing[key]}", params)
        else:
            res = api.call("POST", "billing_portal/configurations", params)
        result[key] = res["id"]

    if not args.apply:
        print("dry-run: nothing written. Re-run with --apply.")
        return 0
    out = json.dumps(result, separators=(",", ":"))
    print("STRIPE_PORTAL_CONFIGS=" + out)
    dest = REPO_ROOT.parent / ".secrets" / f"portal_configs_{args.mode}.json"
    dest.write_text(out + "\n", encoding="utf-8")
    print(f"wrote {dest.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
