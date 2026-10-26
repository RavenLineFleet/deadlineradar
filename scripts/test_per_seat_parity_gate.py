"""Tests for GATE-71 (a tiers.ts shape change made check_pricing_matches_tiers
drop all four per-seat price-parity legs with 0 errors). AuditLab, 2026-10-24.

    python -m pytest scripts/test_per_seat_parity_gate.py -q
"""
import os
import shutil
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

REPO = Path(HERE).parent
ANNUAL = "export const PER_SEAT_ADDON_ANNUAL_USD = "
MONTHLY = "export const PER_SEAT_ADDON_MONTHLY_USD = "


def _fixture(tmp_path, transform):
    (tmp_path / "worker" / "src").mkdir(parents=True)
    for rel in ("generate.py", "i18n.py", "worker/src/emails.ts", "worker/src/scheduler.ts"):
        shutil.copy(REPO / rel, tmp_path / rel)
    ts = (REPO / "worker" / "src" / "tiers.ts").read_text(encoding="utf-8")
    assert ts.count(ANNUAL) == 1 and ts.count(MONTHLY) == 1
    (tmp_path / "worker" / "src" / "tiers.ts").write_text(transform(ts), encoding="utf-8")
    return gate.check_pricing_matches_tiers(tmp_path)


def _annotate(ts):
    return (ts.replace(ANNUAL, "export const PER_SEAT_ADDON_ANNUAL_USD: number = ")
              .replace(MONTHLY, "export const PER_SEAT_ADDON_MONTHLY_USD: number = "))


def test_clean_as_committed(tmp_path):
    assert _fixture(tmp_path, lambda ts: ts) == []


def test_type_annotation_is_not_silent(tmp_path):
    errors = _fixture(tmp_path, _annotate)
    assert any("PER_SEAT_ADDON_ANNUAL_USD/PER_SEAT_ADDON_MONTHLY_USD numeric literals" in e for e in errors)


def test_one_constant_missing_is_not_silent(tmp_path):
    errors = _fixture(tmp_path, lambda ts: ts.replace(MONTHLY, "export const PER_SEAT_ADDON_MONTHLY_USD = PER_SEAT_ADDON_ANNUAL_USD / 10; // "))
    assert any("numeric literals" in e for e in errors)


# SEC-38 (SecurityLab, 2026-10-24): int-only price captures truncated or
# dropped decimal prices. Each mutation asserts its anchor exists once.
def _fixture3(tmp_path, ts_fn=lambda t: t, py_fn=lambda t: t, i18n_fn=lambda t: t):
    (tmp_path / "worker" / "src").mkdir(parents=True)
    for rel, fn in (("generate.py", py_fn), ("i18n.py", i18n_fn), ("worker/src/tiers.ts", ts_fn),
                    ("worker/src/emails.ts", lambda t: t), ("worker/src/scheduler.ts", lambda t: t)):
        (tmp_path / rel).write_text(fn((REPO / rel).read_text(encoding="utf-8")), encoding="utf-8")
    return gate.check_pricing_matches_tiers(tmp_path)


def _sub(old, new):
    def fn(text):
        assert text.count(old) == 1, old
        return text.replace(old, new)
    return fn


def test_decimal_annual_drift_in_tiers_is_caught(tmp_path):
    errors = _fixture3(tmp_path, ts_fn=_sub(ANNUAL + "15;", ANNUAL + "15.50;"))
    assert any("PER_SEAT_ADDON_ANNUAL_USD_JS" in e for e in errors)


def test_consistent_decimal_annual_is_clean(tmp_path):
    errors = _fixture3(
        tmp_path,
        ts_fn=_sub(ANNUAL + "15;", ANNUAL + "15.50;"),
        py_fn=_sub("var PER_SEAT_ADDON_ANNUAL_USD_JS = 15;", "var PER_SEAT_ADDON_ANNUAL_USD_JS = 15.50;"),
    )
    assert errors == []


def test_integer_message_has_no_trailing_zero(tmp_path):
    # BILL-52: /pricing/ carries no per-seat prose now; re-adding a wrong
    # rate must still be caught, and the message must say $15, not $15.0.
    # PROC-10: re-anchored on the BILL-55 copy (the old anchor went with it).
    errors = _fixture3(tmp_path, i18n_fn=_sub("and we'll work out what fits.", "and we'll work out what fits at $16/seat/year."))
    assert any("PER_SEAT_ADDON_ANNUAL_USD is $15 --" in e for e in errors), errors


def test_absent_per_seat_prose_is_clean(tmp_path):
    # BILL-52 positive control: the committed copy states no per-seat rate.
    assert "/seat/year" not in (REPO / "i18n.py").read_text(encoding="utf-8")
    assert _fixture3(tmp_path) == []


def test_decimal_segment_card_drift_is_caught(tmp_path):
    errors = _fixture3(tmp_path, py_fn=_sub("Growth &mdash; $299/year", "Growth &mdash; $250.50/year"))
    assert any('segment card for "Growth" shows $250.50/year' in e for e in errors), errors


def test_segment_cards_missing_is_not_silent(tmp_path):
    errors = _fixture3(tmp_path, py_fn=lambda t: t.replace('class="dr-segment-detail">', 'class="dr-segment-detail x">'))
    assert any("segment_pat" in e for e in errors)
    assert any("segment_seatcap_pat" in e for e in errors)


def test_decimal_founding_and_i18n_bounds_drift_is_caught(tmp_path):
    errors = _fixture3(
        tmp_path,
        py_fn=_sub("normally $299 a year", "normally $299.50 a year"),
        i18n_fn=_sub("firm plans from $199 to", "firm plans from $199.50 to"),
    )
    assert any("normally $299.50 a year" in e for e in errors), errors
    assert any("firm plans from $199.5 to" in e for e in errors), errors
