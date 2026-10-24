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
    for rel in ("generate.py", "i18n.py"):
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
