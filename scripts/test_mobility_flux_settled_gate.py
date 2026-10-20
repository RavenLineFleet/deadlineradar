"""GATE-60 gate: a rule_in_flux mobility row past its rule_changes_on without a settling
verified_date or forward_updated_for_change_on must fail the gate, not just print an advisory
nothing reads (AuditLab, 2026-10-19 -- the gap SRC-63/SRC-64 shipped through for 18 days).

    python -m pytest scripts/test_mobility_flux_settled_gate.py -q
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

REPO_ROOT = Path(HERE).parent
TODAY = date(2026, 10, 19)


def _repo(tmp_path, records):
    p = tmp_path / "worker" / "src"
    p.mkdir(parents=True)
    (p / "mobility_rules.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    return tmp_path


def rec(**kw):
    base = {"state": "Testland", "rule_in_flux": True, "rule_changes_on": "2026-10-01", "verified_date": "2026-07-31"}
    base.update(kw)
    return base


def test_shipped_data_is_clean():
    assert gate.check_mobility_flux_settled(REPO_ROOT, today=TODAY) == []


def test_unsettled_flux_row_past_its_change_date_fails(tmp_path):
    errs = gate.check_mobility_flux_settled(_repo(tmp_path, [rec()]), today=TODAY)
    assert len(errs) == 1 and "[GATE-60][Testland]" in errs[0] and "blocked 18d" in errs[0]


def test_sa63_pre_fix_shape_is_the_positive_control(tmp_path):
    # The exact Alabama field shape AuditLab extracted pre-fix: rule_in_flux=True,
    # rule_changes_on='2026-10-01', verified_date='2026-07-31', forward_updated_for_change_on absent.
    alabama = {"state": "Alabama", "rule_in_flux": True, "rule_changes_on": "2026-10-01",
               "verified_date": "2026-07-31"}
    errs = gate.check_mobility_flux_settled(_repo(tmp_path, [alabama]), today=TODAY)
    assert len(errs) == 1 and "[GATE-60][Alabama]" in errs[0]


def test_settled_by_forward_update_passes(tmp_path):
    r = rec(forward_updated_for_change_on="2026-10-01")
    assert gate.check_mobility_flux_settled(_repo(tmp_path, [r]), today=TODAY) == []


def test_settled_by_post_hoc_verification_passes(tmp_path):
    r = rec(verified_date="2026-10-05")
    assert gate.check_mobility_flux_settled(_repo(tmp_path, [r]), today=TODAY) == []


def test_change_date_not_yet_arrived_passes(tmp_path):
    r = rec(rule_changes_on="2026-12-01")
    assert gate.check_mobility_flux_settled(_repo(tmp_path, [r]), today=TODAY) == []


def test_not_in_flux_passes_regardless_of_dates(tmp_path):
    r = rec(rule_in_flux=False)
    assert gate.check_mobility_flux_settled(_repo(tmp_path, [r]), today=TODAY) == []


def test_malformed_change_date_is_skipped_not_crashed(tmp_path):
    r = rec(rule_changes_on="not-a-date")
    assert gate.check_mobility_flux_settled(_repo(tmp_path, [r]), today=TODAY) == []


def test_missing_data_file_passes_empty(tmp_path):
    assert gate.check_mobility_flux_settled(tmp_path, today=TODAY) == []


def test_advisory_and_hard_gate_agree_on_the_same_data(tmp_path, capsys):
    repo = _repo(tmp_path, [rec()])
    errs = gate.check_mobility_flux_settled(repo, today=TODAY)
    assert len(errs) == 1
    # advisory uses date.today() internally (no today= param), so freeze it for this check
    import preship_gate
    orig = preship_gate.date
    class _FrozenDate(date):
        @classmethod
        def today(cls):
            return TODAY
    preship_gate.date = _FrozenDate
    try:
        gate.print_flux_blocked_pending_reverification_advisory(repo)
    finally:
        preship_gate.date = orig
    out = capsys.readouterr().out
    assert "1 row(s) past their change date" in out and "Testland -- changed 2026-10-01, blocked 18d" in out
