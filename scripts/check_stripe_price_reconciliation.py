#!/usr/bin/env python3
"""Stripe price + referral-coupon reconciliation check (2026-08-05, AuditLab BILL-3;
coupon half added 2026-08-20, AuditLab STRIPE-1 re-verify).

Two independent sources of truth for what a paid tier costs:
    displayed   worker/src/tiers.ts   { planTier, label, priceUsd, seatCap }
    charged     the real Stripe Price object each STRIPE_PRICE_* env var
                (a wrangler secret) points to -- unit_amount + interval

Nothing before this asserted the two agree. A mis-configured price ID, or an
amount edited directly in the Stripe dashboard, would leave the site
advertising one price while Stripe actually charges another, with no build
step, test, or guard noticing -- exactly the "silent mismatch" class this
whole reliability push exists to catch, applied to money instead of a
renewal date. This is the SAME shape as check_deadline_currency() in
preship_gate.py: convert a silent wrong answer into a loud, explicit failure.

STRIPE-1's re-verify (2026-08-20) found a second, worse instance of the same
class this script didn't cover: `env.ts`'s STRIPE_COUPON_REFERRAL is a coupon
ID PREFIX (referralTierCouponId() appends tier 1-10 -- see that function and
env.ts's own docstring), and worker/src/index.ts only checks whether the ENV
VAR is set, never whether the 10 coupons it names actually exist in Stripe.
Two failure modes, and the original finding only named the loud one:
  - var set, coupon missing in Stripe  -> createCheckoutSession() throws ->
    502. Loud, self-reporting.
  - var UNSET entirely                 -> referralCouponId is undefined,
    checkout proceeds with no discount applied, NOTHING fails. The site
    promises "10% off per referral, up to 100%"; a referred firm is charged
    full price; no error, no log line, no signal to anyone. Silent
    under-delivery on a money promise is worse than a loud 502.
This script cannot detect the "var unset" mode from outside (that's a
config fact, not a Stripe query) -- it reports it plainly instead of
guessing, and verifies the 10 coupons exist whenever the var IS set.

Deliberately NOT wired into preship_gate.py's automatic flow: every other
check there is fully offline (reads local JSON/HTML, no network, no secret
needed), which is why it runs on every local `python generate.py`. This one
needs a live Stripe secret key and a real network call to a paid third-party
API -- a genuinely different dependency shape, so it stays a separate,
deliberately-run script. Run it by hand before any deploy that touches
Stripe price/coupon configuration (a new tier, a changed STRIPE_PRICE_*
secret, before flipping from test-mode to live-mode keys at Gate 2), not on
every build.

AuditLab BILL-17 (MEDIUM, 2026-09-09): this script only ever ran when
someone remembered to, so a price desync between deploys (a dashboard edit,
say) went undetected until the next manual run. worker/src/scheduler.ts's
runStripePriceParityAlertPass() now covers the PRICE half of the same check
automatically every night (gated behind requireSendApproval per the
standing consent-gate directive) -- this script remains the pre-deploy
gate for a NEW tier/secret change (it fails the deploy itself, not just
alerts after the fact) and is still the only thing that checks the
referral-coupon half at all.

Usage:
    export STRIPE_SECRET_KEY=sk_test_...   (or source AssetLab's OWN
                                             .secrets/stripe.env, two directories
                                             above this repo's root -- NEVER copy
                                             it into the repo tree itself; see
                                             SEC-4, 2026-08-20, for why that
                                             specific mistake is one `git add -A`
                                             away from a public leak)
    export STRIPE_COUPON_REFERRAL=dr-referral-tier-   (optional -- matches
                                             the wrangler secret; omit to skip
                                             the coupon half entirely)
    python scripts/check_stripe_price_reconciliation.py

Exit code 0 = every configured price matches tiers.ts, and (if
STRIPE_COUPON_REFERRAL is set) all 10 referral coupons exist in Stripe.
Exit code 1 = at least one price mismatch, a configured price ID Stripe
doesn't recognise (checkout would 500), or a referral coupon Stripe doesn't
recognise (checkout would 502) -- all real, loud problems this script turns
into an explicit failure instead of a support ticket.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Mirrors worker/src/tiers.ts's FIRM_TIERS exactly. Kept as a literal
# duplicate here (not parsed out of the .ts file) -- four numbers is cheap
# to keep in sync by eye, and a Python->TypeScript parser would be more
# code and more failure modes than the thing it's guarding against. If
# this ever drifts from tiers.ts, that IS the bug this script exists to
# catch on the OTHER side (Stripe) -- keep both sides honest by hand.
#
# STRIPE_PRICE_INDIVIDUAL removed 2026-08-09 -- the $39/yr Individual tier
# was folded into free (see tiers.ts/entitlements.ts's own comments); it
# never had a real checkout path, so there's no live Stripe price left to
# reconcile against.
## ValueLab pricing-ruling smaller item (approved, batched 2026-08-21): the
# four labels below were the OLD tier names (Starter/Growth/Standard/Scale)
# from before the 2026-08-09 re-tier -- harmless today since nothing here
# compares the label itself against Stripe, only price_usd, but the day this
# check ever prints a mismatch, it would name the wrong tier in the error.
# Real current labels per tiers.ts's own FIRM_TIERS (source of truth).
# PR6 (2026-10-02, AuditLab PR6-A): this script's own coverage had the
# SAME gap the nightly cron pass (runStripePriceParityAlertPass,
# scheduler.ts) had before this commit -- four entries for the four
# ANNUAL prices only, with no entry at all for the 6 prices PR6 added
# (4 monthly tiers + 2 per-seat). "interval" added so a monthly entry
# checks against "month"/monthlyPriceUsd, not the annual expectation.
# BILL-22/23 (SecurityLab, confirmed by AuditLab, 2026-10-03): every price
# in EXPECTED_TIERS is expected to share this same shape -- a real,
# unarchived, licensed (not metered) price billing exactly once per
# interval. ONE shared definition (not a magic literal at each of the 3
# new per-price checks below) so the two new rules can't drift from each
# other inside this file, and the worker-side mirror
# (worker/src/scheduler.ts's runStripePriceParityAlertPass(), via
# stripe.ts) uses the identical two values -- kept in sync by hand across
# the Python/TypeScript boundary, same "duplicated deliberately" precedent
# EXPECTED_TIERS itself already sets against tiers.ts.
EXPECTED_PRICE_INTERVAL_COUNT = 1
EXPECTED_PRICE_USAGE_TYPE = "licensed"

EXPECTED_TIERS = {
    "STRIPE_PRICE_FIRM_STARTER": {"label": "Essentials (annual)", "price_usd": 199, "interval": "year"},
    "STRIPE_PRICE_FIRM_GROWTH": {"label": "Growth (annual)", "price_usd": 299, "interval": "year"},
    "STRIPE_PRICE_FIRM_STANDARD": {"label": "Professional (annual)", "price_usd": 399, "interval": "year"},
    "STRIPE_PRICE_FIRM_SCALE": {"label": "Enterprise (annual)", "price_usd": 549, "interval": "year"},
    "STRIPE_PRICE_FIRM_STARTER_MONTHLY": {"label": "Essentials (monthly)", "price_usd": 20, "interval": "month"},
    "STRIPE_PRICE_FIRM_GROWTH_MONTHLY": {"label": "Growth (monthly)", "price_usd": 29, "interval": "month"},
    "STRIPE_PRICE_FIRM_STANDARD_MONTHLY": {"label": "Professional (monthly)", "price_usd": 39, "interval": "month"},
    "STRIPE_PRICE_FIRM_SCALE_MONTHLY": {"label": "Enterprise (monthly)", "price_usd": 55, "interval": "month"},
    "STRIPE_PRICE_PER_SEAT_ADDON_ANNUAL": {"label": "Per-seat add-on (annual)", "price_usd": 15, "interval": "year"},
    "STRIPE_PRICE_PER_SEAT_ADDON_MONTHLY": {"label": "Per-seat add-on (monthly)", "price_usd": 1.5, "interval": "month"},
}

# referralTierCouponId() (worker/src/index.ts) -- MAX_REFERRAL_TIER, kept as
# a literal duplicate here for the same reason EXPECTED_TIERS is: cheap to
# keep in sync by eye, and if it ever drifts, that mismatch is real signal
# about the OTHER side (Stripe), which is exactly what this check exists for.
MAX_REFERRAL_TIER = 10


def fetch_price(secret_key: str, price_id: str) -> dict:
    req = urllib.request.Request(
        f"https://api.stripe.com/v1/prices/{price_id}",
        headers={"Authorization": f"Basic {_basic_auth(secret_key)}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_coupon(secret_key: str, coupon_id: str) -> dict:
    req = urllib.request.Request(
        f"https://api.stripe.com/v1/coupons/{coupon_id}",
        headers={"Authorization": f"Basic {_basic_auth(secret_key)}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _basic_auth(secret_key: str) -> str:
    import base64

    return base64.b64encode(f"{secret_key}:".encode("utf-8")).decode("ascii")


def monthly_billing_enabled() -> bool:
    """Reads worker/src/tiers.ts's MONTHLY_BILLING_ENABLED (SecurityLab BLOCK-1,
    2026-10-07): while true the site offers Monthly, so an unset *_MONTHLY price
    id is a MISMATCH (checkout would 503), not a skippable 'not configured'."""
    import re
    from pathlib import Path
    ts = Path(__file__).resolve().parent.parent / "worker" / "src" / "tiers.ts"
    m = re.search(r"export const MONTHLY_BILLING_ENABLED\s*=\s*(true|false)", ts.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("REFUSING: could not find MONTHLY_BILLING_ENABLED in worker/src/tiers.ts")
    return m.group(1) == "true"


def main() -> int:
    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        print("REFUSING: STRIPE_SECRET_KEY not set. Export it (test or live) and re-run.", file=sys.stderr)
        return 1

    monthly_on = monthly_billing_enabled()
    mismatches = []
    missing_env = []
    api_errors = []
    checked = 0

    for env_name, expected in EXPECTED_TIERS.items():
        price_id = os.environ.get(env_name)
        if not price_id:
            if monthly_on and env_name.endswith("_MONTHLY"):
                mismatches.append((env_name, expected["label"], "<unset>", [
                    "env var unset while MONTHLY_BILLING_ENABLED -- the site offers monthly but checkout would 503"]))
            else:
                missing_env.append(env_name)
            continue
        try:
            price = fetch_price(secret_key, price_id)
        except urllib.error.HTTPError as e:
            api_errors.append((env_name, price_id, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"))
            continue
        except urllib.error.URLError as e:
            api_errors.append((env_name, price_id, str(e)))
            continue

        checked += 1
        expected_cents = round(expected["price_usd"] * 100)
        actual_cents = price.get("unit_amount")
        actual_interval = (price.get("recurring") or {}).get("interval")
        actual_currency = price.get("currency")
        # BILL-22 (SecurityLab, MEDIUM, confirmed by AuditLab, 2026-10-03):
        # a Stripe Price's unit_amount is immutable -- changing a price
        # means creating a new one and ARCHIVING the old, so an archived
        # id with a still-correct amount/interval/currency is the LIKELY
        # misconfiguration, not an exotic one, and it used to pass this
        # gate clean while refusing every checkout for that tier. `is not
        # True` (not a truthiness test) so a missing key is a problem too.
        actual_active = price.get("active")
        # BILL-23/23b (SecurityLab, MEDIUM, confirmed by AuditLab,
        # 2026-10-03): interval alone doesn't fix the billing PERIOD --
        # interval_count=3 on a "month" price bills quarterly, passing
        # every check above clean while charging 1/3 as often. usage_type
        # must be "licensed" (not "metered") for the per-seat add-on
        # prices specifically, since they're the only line items sent
        # with a quantity (stripe.ts) -- asserted on all 10 uniformly
        # since nothing in this app bills any price any other way.
        actual_interval_count = (price.get("recurring") or {}).get("interval_count")
        actual_usage_type = (price.get("recurring") or {}).get("usage_type")

        problems = []
        if actual_cents != expected_cents:
            problems.append(f"unit_amount={actual_cents} (expected {expected_cents}, i.e. ${expected['price_usd']})")
        if actual_interval != expected["interval"]:
            problems.append(f"recurring.interval={actual_interval!r} (expected {expected['interval']!r})")
        if actual_currency != "usd":
            problems.append(f"currency={actual_currency!r} (expected 'usd')")
        if actual_active is not True:
            problems.append(f"active={actual_active!r} (expected True -- Stripe refuses checkout on an archived price)")
        if actual_interval_count != EXPECTED_PRICE_INTERVAL_COUNT:
            problems.append(
                f"recurring.interval_count={actual_interval_count!r} (expected {EXPECTED_PRICE_INTERVAL_COUNT!r} -- "
                f"otherwise bills every {actual_interval_count} {expected['interval']}s, not every {expected['interval']})"
            )
        if actual_usage_type != EXPECTED_PRICE_USAGE_TYPE:
            problems.append(f"recurring.usage_type={actual_usage_type!r} (expected {EXPECTED_PRICE_USAGE_TYPE!r})")
        if problems:
            mismatches.append((env_name, expected["label"], price_id, problems))

    print(f"Stripe price reconciliation check -- {checked} price(s) fetched and compared against tiers.ts")

    if missing_env:
        print(f"\nNOT CONFIGURED ({len(missing_env)}) -- env var unset, skipped:")
        for name in missing_env:
            print(f"  {name}")

    if api_errors:
        print(f"\nAPI ERROR ({len(api_errors)}) -- Stripe rejected or couldn't be reached for this price id:")
        for env_name, price_id, err in api_errors:
            print(f"  {env_name} ({price_id}): {err}")

    if mismatches:
        print(f"\nMISMATCH ({len(mismatches)}) -- site advertises one price, Stripe would charge another:")
        for env_name, label, price_id, problems in mismatches:
            print(f"  {env_name} [{label}] ({price_id}):")
            for p in problems:
                print(f"    - {p}")

    if not mismatches and not api_errors and checked > 0:
        print("\nPASS -- every configured price matches tiers.ts exactly.")

    # --- Referral coupon check (STRIPE-1 re-verify, 2026-08-20) ---------
    coupon_prefix = os.environ.get("STRIPE_COUPON_REFERRAL")
    coupon_missing = []
    coupon_api_errors = []
    coupons_checked = 0

    if not coupon_prefix:
        print(
            "\nREFERRAL COUPONS: STRIPE_COUPON_REFERRAL not set in this shell -- skipped. "
            "This does NOT mean the prod Worker has it unset (that's a separate wrangler secret, "
            "not readable from here) -- it means this run can't verify either way. If the prod "
            "Worker's STRIPE_COUPON_REFERRAL is genuinely unset, every referral checkout proceeds "
            "at full price with NO error anywhere (worker/src/index.ts:3276) -- confirm that secret "
            "is set in prod separately from this script."
        )
    else:
        for tier in range(1, MAX_REFERRAL_TIER + 1):
            coupon_id = f"{coupon_prefix}{tier}"
            try:
                coupon = fetch_coupon(secret_key, coupon_id)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    coupon_missing.append(coupon_id)
                else:
                    coupon_api_errors.append((coupon_id, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"))
                continue
            except urllib.error.URLError as e:
                coupon_api_errors.append((coupon_id, str(e)))
                continue

            coupons_checked += 1
            expected_pct = tier * 10
            actual_pct = coupon.get("percent_off")
            actual_duration = coupon.get("duration")
            problems = []
            if actual_pct != expected_pct:
                problems.append(f"percent_off={actual_pct} (expected {expected_pct}, i.e. tier {tier} = {expected_pct}% off)")
            if actual_duration != "once":
                problems.append(f"duration={actual_duration!r} (expected 'once')")
            if problems:
                coupon_api_errors.append((coupon_id, "; ".join(problems)))

        print(f"\nReferral coupon check -- {coupons_checked}/{MAX_REFERRAL_TIER} coupon(s) fetched under prefix {coupon_prefix!r}")
        if coupon_missing:
            print(f"\nMISSING IN STRIPE ({len(coupon_missing)}) -- checkout for this referral tier would 502:")
            for cid in coupon_missing:
                print(f"  {cid}")
        if coupon_api_errors:
            print(f"\nCOUPON PROBLEM ({len(coupon_api_errors)}):")
            for cid, err in coupon_api_errors:
                print(f"  {cid}: {err}")
        if not coupon_missing and not coupon_api_errors and coupons_checked == MAX_REFERRAL_TIER:
            print(f"\nPASS -- all {MAX_REFERRAL_TIER} referral coupons exist under prefix {coupon_prefix!r} with the correct percent_off.")

    return 1 if (mismatches or api_errors or coupon_missing or coupon_api_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
