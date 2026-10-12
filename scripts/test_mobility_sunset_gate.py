"""REG-6 gate: time-limited mobility rules must carry a date-typed rule_expires_on.

    python -m pytest scripts/test_mobility_sunset_gate.py -q
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
TODAY = date(2026, 10, 12)


def _repo(tmp_path, records):
    p = tmp_path / "worker" / "src"
    p.mkdir(parents=True)
    (p / "mobility_rules.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    return tmp_path


def rec(**kw):
    base = {"state_slug": "xx", "citation": "Code 1", "data_gap_note": None, "notes": None, "verified_date": "2026-10-01"}
    base.update(kw)
    return base


def test_shipped_data_is_clean_today():
    assert gate.check_mobility_sunset_field(REPO_ROOT, today=TODAY) == []


def test_prose_only_sunset_is_a_failure(tmp_path):
    r = rec(notes="This rests on EMERGENCY legislation that expires November 8, 2026.")
    errs = gate.check_mobility_sunset_field(_repo(tmp_path, [r]), today=TODAY)
    assert len(errs) == 1 and "no date-typed rule_expires_on" in errs[0]


def test_each_prose_field_and_keyword_is_seen(tmp_path):
    for f in ("citation", "data_gap_note", "notes"):
        for text in ("temporary act expiring Dec 1", "TIME-LIMITED: expires", "emergency amendment that will expire"):
            errs = gate.check_mobility_sunset_field(_repo(tmp_path / f"{f}{abs(hash(text))}", [rec(**{f: text})]), today=TODAY)
            assert len(errs) == 1, (f, text)


def test_dated_sunset_passes_and_prose_alone_about_a_repeal_does_not_trip(tmp_path):
    ok = rec(notes="emergency act expiring Nov 8", rule_expires_on="2026-11-08")
    repealed = rec(state_slug="yy", notes="old statute was repealed effective 2025-10-01")
    assert gate.check_mobility_sunset_field(_repo(tmp_path, [ok, repealed]), today=TODAY) == []


def test_malformed_date_is_a_failure(tmp_path):
    errs = gate.check_mobility_sunset_field(_repo(tmp_path, [rec(rule_expires_on="Nov 8")]), today=TODAY)
    assert len(errs) == 1 and "not an ISO date" in errs[0]


def test_arrived_sunset_needs_a_verification_on_or_after_it(tmp_path):
    r = rec(rule_expires_on="2026-11-08", verified_date="2026-10-12")
    assert gate.check_mobility_sunset_field(_repo(tmp_path / "a", [r]), today=date(2026, 11, 7)) == []
    errs = gate.check_mobility_sunset_field(_repo(tmp_path / "b", [r]), today=date(2026, 11, 8))
    assert len(errs) == 1 and "has arrived" in errs[0]
    r2 = rec(rule_expires_on="2026-11-08", verified_date="2026-11-08")
    assert gate.check_mobility_sunset_field(_repo(tmp_path / "c", [r2]), today=date(2026, 11, 20)) == []
    r3 = rec(rule_expires_on="2026-11-08", verified_date=None)
    assert len(gate.check_mobility_sunset_field(_repo(tmp_path / "d", [r3]), today=date(2026, 11, 9))) == 1


def test_advisory_counts_down(tmp_path, capsys):
    gate.print_mobility_sunset_advisory(_repo(tmp_path, [rec(state_slug="dc", rule_expires_on="2026-11-08")]), today=TODAY)
    assert "dc: rule_expires_on 2026-11-08 in 27 day(s)" in capsys.readouterr().out
