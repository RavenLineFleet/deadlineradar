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


# SRC-270 (AuditLab MEDIUM, 2026-11-18): the 40->200 widening recovered 0
# correct pairs and misattributed 2, because the regex could not parse the
# bill's own abbreviated/numeric date and walked past it. These are AuditLab's
# two exact strings plus the latent trap they named.
SB522_CITATION = ("Tex. Occ. Code §§ 901.461, 901.462 (practice privileges); as amended by Acts 2025, "
                  "89th Leg., R.S., Ch. 40 (S.B. 522), § 2, eff. Sept. 1, 2025; "
                  "22 TAC § 517.2 (as amended eff. 2025-10-09)")
HB548_EVIDENCE = ("Bill-status action history (BillInfo.aspx?s=26rs&b=HB548): 'Effective date: 08/01/2026.' "
                  "Louisiana Board of CPAs public pamphlet: signed June 2, 2026, effective August 1, 2026.")


def test_src270_sb522_takes_its_own_abbreviated_date_not_the_tac_rules():
    assert preship_gate._bill_effective_pairs(SB522_CITATION) == [("SB 522", "2025-09-01")]


def test_src270_hb548_takes_mmddyyyy_not_the_signing_date():
    assert preship_gate._bill_effective_pairs(HB548_EVIDENCE) == [("HB 548", "2026-08-01")]


def test_src270_latent_trap_correct_copies_do_not_conflict(tmp_path):
    t = _tree(tmp_path, {"worker/src/mobility_rules.json": _rec("texas", SB522_CITATION),
                         "data/research.json": _rec("texas", "S.B. 522 (eff. September 1, 2025)")})
    assert preship_gate.check_bill_effective_date_consistency(t) == []


def test_src270_unparseable_first_date_yields_nothing_never_walks_past():
    for first in ("1 July 2026", "July 2026", "7/1/26", "13/01/2026"):
        s = f"HB 9 effective {first}; rule amended 2026-10-09"
        assert preship_gate._bill_effective_pairs(s) == [], (first, preship_gate._bill_effective_pairs(s))


def test_src270_date_window_stops_at_next_bill():
    s = "HB 9 effective on signature; SB 4 eff. 2026-03-01"
    assert preship_gate._bill_effective_pairs(s) == [("SB 4", "2026-03-01")]


@pytest.mark.parametrize("text,iso", [("Apr. 22, 2026", "2026-04-22"), ("Jan 1, 2026", "2026-01-01"),
                                      ("Sept. 1, 2025", "2025-09-01"), ("Sep 1 2025", "2025-09-01"),
                                      ("May 5, 2026", "2026-05-05"), ("08/01/2026", "2026-08-01")])
def test_src270_date_forms_parse(text, iso):
    assert preship_gate._bill_effective_pairs(f"HB 1 eff. {text}") == [("HB 1", iso)]


def test_src270_window_is_40_and_real_tree_pairs_all_sit_inside_it():
    # 40 and 200 yield identical pairs on the real tree once the regex parses
    # abbreviations; the widening only bought room to misattribute.
    assert preship_gate._BILL_DATE_WINDOW == 40


def test_ptin_federal_pair_guarded_byte_identical(tmp_path):
    # SRC-269 LOW-1: ptin_federal.json is a same-basename data/ <-> worker/src/ pair.
    for rel in ("data", "worker/src"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "ptin_federal.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "worker" / "src" / "ptin_federal.json").write_text('{"a": 2}', encoding="utf-8")
    errs = preship_gate.check_json_copies_identical(tmp_path)
    assert any("ptin_federal.json" in e for e in errs), errs
