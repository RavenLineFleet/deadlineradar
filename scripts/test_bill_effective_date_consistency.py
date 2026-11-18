"""Regression pins for preship_gate.check_bill_effective_date_consistency (AuditLab SRC-269, 2026-11-17).

    python -m pytest scripts/test_bill_effective_date_consistency.py -q

Mandatory fixture (AuditLab's pass condition): the gate FAILS on the data tree at
74bc30a80^ (Kansas HB 2573 stated eff. 2026-07-01 in 4 files, 2026-04-23 in the
mobility files) and PASSES at d91dd091d (the fix).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import preship_gate  # noqa: E402

PRE_FIX = "74bc30a80468d2a68c6bdd474f78447081cb41ce^"
FIXED = "d91dd091d6f2fac9b00185762fbfab421bd1f45b"


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, check=True).stdout


def _materialize(ref, dst):
    names = _git("ls-tree", "--name-only", ref, "data/", "worker/src/").decode().split()
    n = 0
    for name in names:
        if name.endswith(".json"):
            out = dst / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(_git("show", f"{ref}:{name}"))
            n += 1
    assert n >= 20, f"only {n} data files at {ref}"
    return dst


def _have(ref):
    return subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", ref],
                          capture_output=True).returncode == 0


needs_history = pytest.mark.skipif(not (_have(PRE_FIX) and _have(FIXED)), reason="fixture commits not in this clone")


def test_real_tree_passes():
    assert preship_gate.check_bill_effective_date_consistency(REPO_ROOT) == []


@needs_history
def test_fails_on_pre_fix_kansas_tree(tmp_path):
    errs = preship_gate.check_bill_effective_date_consistency(_materialize(PRE_FIX, tmp_path))
    assert len(errs) == 1, errs
    assert errs[0].startswith("[SRC-269][kansas] HB 2573 ")
    assert "2026-04-23" in errs[0] and "2026-07-01" in errs[0]


@needs_history
def test_passes_on_fixed_tree(tmp_path):
    assert preship_gate.check_bill_effective_date_consistency(_materialize(FIXED, tmp_path)) == []


def test_registered_in_authoritative_error_list():
    src = (REPO_ROOT / "scripts" / "preship_gate.py").read_text(encoding="utf-8")
    main = src[src.index("\ndef main():"):]
    assert main.count("all_errors += check_bill_effective_date_consistency(repo_root)") == 1


def _tree(tmp_path, files):
    for rel, obj in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj), encoding="utf-8")
    return tmp_path


def _rec(state, text):
    return {"records": [{"state_slug": state, "notes": text}]}


def test_cross_file_conflict_fails(tmp_path):
    t = _tree(tmp_path, {"data/a.json": _rec("ohio", "HB 12 eff. 2026-01-01."),
                         "data/b.json": _rec("ohio", "per House Bill 12, effective March 3, 2026")})
    errs = preship_gate.check_bill_effective_date_consistency(t)
    assert len(errs) == 1 and "[ohio] HB 12" in errs[0]


def test_same_file_narrative_not_flagged(tmp_path):
    t = _tree(tmp_path, {"data/a.json": {"records": [
        {"state_slug": "ohio", "notes": "HB 12 eff. 2026-01-01."},
        {"state_slug": "ohio", "notes": "HB 12 effective 2026-03-03 for firms."}]}})
    assert preship_gate.check_bill_effective_date_consistency(t) == []


def test_same_bill_number_other_state_not_flagged(tmp_path):
    t = _tree(tmp_path, {"data/a.json": _rec("ohio", "HB 12 eff. 2026-01-01."),
                         "data/b.json": _rec("iowa", "HB 12 eff. 2026-03-03.")})
    assert preship_gate.check_bill_effective_date_consistency(t) == []


def test_correction_arrow_asserts_right_hand_date(tmp_path):
    t = _tree(tmp_path, {"data/a.json": _rec("kansas", "HB 2573 effective date corrected 2026-07-01 -> 2026-04-23."),
                         "data/b.json": _rec("kansas", "HB 2573, eff. 2026-04-23")})
    assert preship_gate.check_bill_effective_date_consistency(t) == []


def test_from_and_after_means_next_day(tmp_path):
    t = _tree(tmp_path, {"data/a.json": _rec("arizona", 'SB 1181: "effective from and after December 31, 2026"'),
                         "data/b.json": _rec("arizona", "SB 1181 eff. 2027-01-01")})
    assert preship_gate.check_bill_effective_date_consistency(t) == []


def test_jurisdiction_slug_keys_state(tmp_path):
    t = _tree(tmp_path, {"data/a.json": {"events": [{"jurisdiction_slug": "ohio", "s": "HB 12 eff. 2026-01-01"}]},
                         "data/b.json": _rec("ohio", "HB 12 eff. 2026-02-02")})
    assert len(preship_gate.check_bill_effective_date_consistency(t)) == 1


def test_allowlist_suppresses_and_stale_entry_fails(tmp_path, monkeypatch):
    t = _tree(tmp_path, {"data/a.json": _rec("ohio", "HB 12 eff. 2026-01-01."),
                         "data/b.json": _rec("ohio", "HB 12 eff. 2026-02-02.")})
    key = ("ohio", "HB 12", frozenset({"2026-01-01", "2026-02-02"}))
    monkeypatch.setattr(preship_gate, "_BILL_DATE_ALLOWLIST", {key: "test"})
    assert preship_gate.check_bill_effective_date_consistency(t) == []
    (t / "data/b.json").write_text(json.dumps(_rec("ohio", "HB 12 eff. 2026-01-01.")), encoding="utf-8")
    errs = preship_gate.check_bill_effective_date_consistency(t)
    assert len(errs) == 1 and "no longer matches" in errs[0]


def test_no_data_files_fails_closed(tmp_path):
    errs = preship_gate.check_bill_effective_date_consistency(tmp_path)
    assert len(errs) == 1 and "measuring nothing" in errs[0]
