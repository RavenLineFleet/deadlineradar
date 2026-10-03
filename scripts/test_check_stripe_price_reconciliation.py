"""Tests for BILL-22/23 (SecurityLab, confirmed by AuditLab, 2026-10-03):
check_stripe_price_reconciliation.py -- the designated pre-push gate for
the live-key step -- used to assert only unit_amount/currency/
recurring.interval per price, missing three fields a real Stripe
dashboard edit can get wrong while every existing assertion still passes:

    python -m pytest scripts/test_check_stripe_price_reconciliation.py -q

    1. baseline: all 10 prices correct (active, interval_count=1,
       usage_type=licensed)                                    -> CLEAN(0)
    2. positive control: a wrong amount                         -> FAIL(1)
       (proves the harness itself can fail, not just always pass)
    3. BILL-22: archived price, amount/interval/currency all
       otherwise correct                                        -> FAIL(1)
    4. BILL-22: the `active` key missing entirely (not merely
       falsy) -- `is not True`, not a truthiness test            -> FAIL(1)
    5. BILL-23: interval_count=3 on a "month" price (bills
       quarterly, not monthly), all else correct                -> FAIL(1)
    6. BILL-23: interval_count key missing entirely              -> FAIL(1)
    7. BILL-23b: usage_type=metered on the per-seat add-on,
       all else correct                                         -> FAIL(1)
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_stripe_price_reconciliation as recon  # noqa: E402

ENV_NAME = "STRIPE_PRICE_FIRM_GROWTH"
EXPECTED = recon.EXPECTED_TIERS[ENV_NAME]  # Growth annual, $299/year


def _good_price(**overrides) -> dict:
    price = {
        "unit_amount": round(EXPECTED["price_usd"] * 100),
        "currency": "usd",
        "recurring": {
            "interval": EXPECTED["interval"],
            "interval_count": recon.EXPECTED_PRICE_INTERVAL_COUNT,
            "usage_type": recon.EXPECTED_PRICE_USAGE_TYPE,
        },
        "active": True,
    }
    price.update(overrides)
    return price


def _run_with_all_prices(monkeypatch: pytest.MonkeyPatch, price_for_env: dict) -> tuple[int, str]:
    """Stubs fetch_price() (the only network seam) so main()'s real
    comparison code executes -- same seam SecurityLab/AuditLab stubbed in
    their own verification runs, not a reimplementation of the check."""
    for env_name in recon.EXPECTED_TIERS:
        monkeypatch.setenv(env_name, f"price_{env_name.lower()}")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.delenv("STRIPE_COUPON_REFERRAL", raising=False)

    def fake_fetch_price(secret_key: str, price_id: str) -> dict:
        for env_name, expected in recon.EXPECTED_TIERS.items():
            if price_id == f"price_{env_name.lower()}":
                if env_name in price_for_env:
                    return price_for_env[env_name]
                p = {
                    "unit_amount": round(expected["price_usd"] * 100),
                    "currency": "usd",
                    "recurring": {
                        "interval": expected["interval"],
                        "interval_count": recon.EXPECTED_PRICE_INTERVAL_COUNT,
                        "usage_type": recon.EXPECTED_PRICE_USAGE_TYPE,
                    },
                    "active": True,
                }
                return p
        raise AssertionError(f"unexpected price_id {price_id!r}")

    monkeypatch.setattr(recon, "fetch_price", fake_fetch_price)

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = recon.main()
    return exit_code, buf.getvalue()


def test_baseline_all_ten_correct_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, output = _run_with_all_prices(monkeypatch, {})
    assert exit_code == 0
    assert "PASS" in output
    assert "MISMATCH" not in output


def test_positive_control_wrong_amount_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the harness itself can fail -- a clean result elsewhere is
    the script's real behavior, not a stub that always passes."""
    exit_code, output = _run_with_all_prices(monkeypatch, {ENV_NAME: _good_price(unit_amount=18800)})
    assert exit_code == 1
    assert "unit_amount=18800" in output


def test_bill22_archived_price_otherwise_correct_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    exit_code, output = _run_with_all_prices(monkeypatch, {ENV_NAME: _good_price(active=False)})
    assert exit_code == 1
    assert "active=False" in output


def test_bill22_missing_active_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    price = _good_price()
    del price["active"]
    exit_code, output = _run_with_all_prices(monkeypatch, {ENV_NAME: price})
    assert exit_code == 1
    assert "active=None" in output


def test_bill23_interval_count_three_bills_quarterly_not_monthly(monkeypatch: pytest.MonkeyPatch) -> None:
    price = _good_price()
    price["recurring"]["interval_count"] = 3
    exit_code, output = _run_with_all_prices(monkeypatch, {ENV_NAME: price})
    assert exit_code == 1
    assert "recurring.interval_count=3" in output


def test_bill23_missing_interval_count_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    price = _good_price()
    del price["recurring"]["interval_count"]
    exit_code, output = _run_with_all_prices(monkeypatch, {ENV_NAME: price})
    assert exit_code == 1
    assert "recurring.interval_count=None" in output


def test_bill23b_metered_usage_type_on_per_seat_addon_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    per_seat_env = "STRIPE_PRICE_PER_SEAT_ADDON_MONTHLY"
    expected = recon.EXPECTED_TIERS[per_seat_env]
    price = {
        "unit_amount": round(expected["price_usd"] * 100),
        "currency": "usd",
        "recurring": {
            "interval": expected["interval"],
            "interval_count": recon.EXPECTED_PRICE_INTERVAL_COUNT,
            "usage_type": "metered",
        },
        "active": True,
    }
    exit_code, output = _run_with_all_prices(monkeypatch, {per_seat_env: price})
    assert exit_code == 1
    assert "recurring.usage_type='metered'" in output
