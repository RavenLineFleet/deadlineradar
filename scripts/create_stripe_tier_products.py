#!/usr/bin/env python3
"""Create one Stripe Product per firm tier, each with its annual + monthly Price.

Why this exists (found 2026-10-07 building self-serve plan change): Stripe's
Customer Portal requires, per Product, that the prices offered for switching
have UNIQUE billing intervals (one annual + one monthly). Deadline-Radar's
original setup put all 8 firm prices (4 tiers x 2 intervals) on ONE product
("Deadline-Radar Firm Plan"), which the portal rejects outright. A Price's
product cannot be changed after creation, so the portal needs new Prices on
per-tier Products. Same amounts as worker/src/tiers.ts FIRM_TIERS (parsed from
that file, so this script cannot drift from the advertised prices).

Idempotent: Products are found by metadata[dr_tier_product]=<planTier>;
Prices by lookup_key `dr-v2-<planTier>-<annual|monthly>`. A re-run creates
nothing new. Dry-run by default. NO charge is ever made (Product/Price objects
only). Existing prices and existing subscriptions are untouched.

    python scripts/create_stripe_tier_products.py --mode test --apply
    python scripts/create_stripe_tier_products.py --mode live --apply

Writes the new ids to AssetLab/.secrets/stripe.env as
STRIPE_[TEST_]V2_PRICE_FIRM_<TIER>[_MONTHLY] (ids are not secrets; the key is
never printed). scripts/configure_stripe_portal.py reads those names. At deploy
time the Worker's STRIPE_PRICE_FIRM_<TIER>[_MONTHLY] secrets are re-pointed at
them (the env var NAMES the Worker and the reconciliation script use do not
change).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRETS_ENV = REPO_ROOT.parent / ".secrets" / "stripe.env"
TIERS_TS = REPO_ROOT / "worker" / "src" / "tiers.ts"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if SECRETS_ENV.exists():
        for line in SECRETS_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def parse_tiers() -> list[dict]:
    text = TIERS_TS.read_text(encoding="utf-8")
    rows = re.findall(
        r'\{\s*planTier:\s*"(firm_\w+)",\s*label:\s*"([^"]+)",\s*priceUsd:\s*(\d+),\s*monthlyPriceUsd:\s*(\d+),\s*seatCap:\s*(\d+)\s*\}',
        text,
    )
    if len(rows) != 4:
        raise SystemExit(f"REFUSING: expected 4 FIRM_TIERS rows in tiers.ts, parsed {len(rows)}")
    return [dict(planTier=r[0], label=r[1], annual=int(r[2]), monthly=int(r[3]), seatCap=int(r[4])) for r in rows]


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
            raise SystemExit(f"Stripe {method} {path} -> {e.code}: {json.load(e).get('error', {}).get('message')}")


def env_name(mode: str, tier_slug: str, interval: str) -> str:
    base = ("STRIPE_TEST_V2_PRICE_FIRM_" if mode == "test" else "STRIPE_V2_PRICE_FIRM_") + tier_slug.replace("firm_", "").upper()
    return base + ("_MONTHLY" if interval == "monthly" else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["test", "live"], required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    env = load_env()
    secret = env.get("STRIPE_TEST_SECRET_KEY" if args.mode == "test" else "STRIPE_SECRET_KEY")
    if not secret:
        raise SystemExit("REFUSING: no secret key for this mode")
    prefix = "sk_test_" if args.mode == "test" else "sk_live_"
    if not secret.startswith(prefix):
        raise SystemExit(f"REFUSING: --mode {args.mode} but the key is not a {prefix}* key")
    api = Stripe(secret)

    products = {}
    for p in api.call("GET", "products?limit=100&active=true")["data"]:
        slug = (p.get("metadata") or {}).get("dr_tier_product")
        if slug:
            products[slug] = p["id"]

    out: dict[str, str] = {}
    for t in parse_tiers():
        slug = t["planTier"]
        pid = products.get(slug)
        if pid:
            print(f"  {slug:14s} product exists {pid}")
        elif args.apply:
            pid = api.call("POST", "products", [
                ("name", f"Deadline-Radar {t['label']} Plan"),
                ("description", f"Deadline-Radar firm plan for up to {t['seatCap']} staff -- Roster, Calendar, Map, CPE Hours, and Practice Privilege Check."),
                ("metadata[dr_tier_product]", slug),
            ])["id"]
            print(f"  {slug:14s} product CREATED {pid}")
        else:
            print(f"  {slug:14s} product would be created ({'Deadline-Radar ' + t['label'] + ' Plan'})")
        for interval, usd, rec in (("annual", t["annual"], "year"), ("monthly", t["monthly"], "month")):
            lookup = f"dr-v2-{slug}-{interval}"
            found = api.call("GET", f"prices?lookup_keys[]={lookup}&limit=1")["data"]
            if found:
                price = found[0]
                want = (usd * 100, "usd", rec, pid)
                got = (price["unit_amount"], price["currency"], (price.get("recurring") or {}).get("interval"), price["product"])
                if got != want or not price["active"]:
                    raise SystemExit(f"REFUSING: existing price {lookup} does not match tiers.ts ({got} vs {want}, active={price['active']})")
                out[env_name(args.mode, slug, interval)] = price["id"]
                print(f"    {interval:8s} ${usd:<4d} price exists {price['id']}")
            elif args.apply:
                price = api.call("POST", "prices", [
                    ("product", pid), ("currency", "usd"), ("unit_amount", str(usd * 100)),
                    ("recurring[interval]", rec), ("recurring[interval_count]", "1"), ("recurring[usage_type]", "licensed"),
                    ("lookup_key", lookup), ("nickname", f"{t['label']} ({slug}) {interval}"),
                    ("tax_behavior", "exclusive"), ("metadata[plan_tier]", slug), ("metadata[billing_interval]", interval),
                ])
                out[env_name(args.mode, slug, interval)] = price["id"]
                print(f"    {interval:8s} ${usd:<4d} price CREATED {price['id']}")
            else:
                print(f"    {interval:8s} ${usd:<4d} price would be created ({lookup})")

    if not args.apply:
        print("dry-run: nothing written. Re-run with --apply.")
        return 0

    existing = SECRETS_ENV.read_text(encoding="utf-8") if SECRETS_ENV.exists() else ""
    have = {l.split("=", 1)[0].strip() for l in existing.splitlines() if "=" in l and not l.lstrip().startswith("#")}
    add = [f"{k}={v}" for k, v in out.items() if k not in have]
    changed = [k for k, v in out.items() if k in have and env.get(k) != v]
    if changed:
        raise SystemExit(f"REFUSING: .secrets/stripe.env already has different values for {changed}")
    if add:
        with SECRETS_ENV.open("a", encoding="utf-8", newline="\n") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(add) + "\n")
    print(f"{len(out)} ids resolved; {len(add)} appended to .secrets/stripe.env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
