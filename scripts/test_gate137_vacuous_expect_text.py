"""GATE-137 (AuditLab LOW, 2026-11-20): a reverify text check whose expect_text occurs inside its
own anchor matches whenever the anchor is found and verifies nothing. The build must refuse that
shape in the recipe file and in hand_spec.json, and must still accept the legitimate shape where
the expect_text straddles the anchor boundary.

    python -m pytest scripts/test_gate137_vacuous_expect_text.py -q
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "reverify"))
import preship_gate as gate  # noqa: E402
import runner  # noqa: E402

SOURCE = ("Firms must renew in odd-numbered years. Renewal applications are due by the last day "
          "of the month. Firms must renew in odd-numbered years.")
MUTATED = SOURCE.replace("due by the last day", "due by the fifteenth day")


def _vacuous():
    return {"url": "https://example.test/x", "method": "http", "pattern": None,
            "anchor": "Renewal applications are due by the last day of the month",
            "expect_text": "due by the last day", "window": 60}


def _straddle():
    # bootstrap.py's shape: anchor = quote[:28], expect_text = quote. Legitimate, NOT vacuous.
    quote = "Renewal applications are due by the last day of the month"
    return {"url": "https://example.test/x", "method": "http", "pattern": None,
            "anchor": quote[:28], "expect_text": quote, "window": len(quote) + 40}


def _real_text_checks(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [c for r in doc.values() if isinstance(r, dict)
            for c in (r.get("checks") or []) if isinstance(c, dict) and c.get("expect_text")]


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "scripts" / "reverify").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "data", "reverify_recipes.json"), tmp_path / "data" / "reverify_recipes.json")
    shutil.copy(os.path.join(HERE, "reverify", "hand_spec.json"), tmp_path / "scripts" / "reverify" / "hand_spec.json")
    return tmp_path


def _inject(path, check):
    doc = json.loads(path.read_text(encoding="utf-8"))
    rid = sorted(k for k, v in doc.items() if isinstance(v, dict) and v.get("checks"))[0]
    doc[rid]["checks"].append(check)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return rid


def test_the_shape_really_is_vacuous():
    # why the gate exists: with a unique anchor and the mutation only AFTER it, the vacuous check still
    # passes; the same text against a non-vacuous check on the same instrument flips (positive control)
    vac = dict(_vacuous(), anchor="Renewal applications are due by the last day", expect_text="the last day")
    good = dict(_vacuous(), anchor="Renewal applications are", expect_text="due by the last day of the month")
    mutant = SOURCE.replace("last day of the month", "last day of the following month")
    assert runner.expect_text_inside_anchor(vac) and not runner.expect_text_inside_anchor(good)
    assert runner.judge_check(vac, SOURCE)[0] == "MATCH" and runner.judge_check(vac, mutant)[0] == "MATCH"
    assert runner.judge_check(good, SOURCE)[0] == "MATCH" and runner.judge_check(good, mutant)[0] == "DIFFERENT"


def test_predicate():
    assert runner.expect_text_inside_anchor(_vacuous())
    assert not runner.expect_text_inside_anchor(_straddle())
    # normalised + case-folded like judge_check: whitespace / curly quotes / case cannot hide it
    assert runner.expect_text_inside_anchor(dict(_vacuous(), expect_text="DUE  by the last day"))
    assert not runner.expect_text_inside_anchor({"anchor": "x", "pattern": r"(\d+)"})


def test_real_tree_passes_and_is_not_empty():
    assert gate.check_reverify_expect_text_not_inside_anchor(Path(ROOT)) == []
    # the pass must not be vacuous itself: both files carry text checks the gate walked
    assert len(_real_text_checks(os.path.join(ROOT, "data", "reverify_recipes.json"))) >= 100
    assert len(_real_text_checks(os.path.join(HERE, "reverify", "hand_spec.json"))) >= 1


def test_fixture_passes(tree):
    assert gate.check_reverify_expect_text_not_inside_anchor(tree) == []


@pytest.mark.parametrize("rel", ["data/reverify_recipes.json", "scripts/reverify/hand_spec.json"])
def test_vacuous_check_fails(tree, rel):
    rid = _inject(tree / rel, _vacuous())
    errs = gate.check_reverify_expect_text_not_inside_anchor(tree)
    assert len(errs) == 1 and "GATE-137" in errs[0] and rid in errs[0] and rel in errs[0]


def test_vacuous_duplicated_source_fails(tree):
    _inject(tree / "data" / "reverify_recipes.json", dict(_vacuous(), duplicated_source=True))
    assert len(gate.check_reverify_expect_text_not_inside_anchor(tree)) == 1


def test_straddle_shape_passes(tree):
    _inject(tree / "data" / "reverify_recipes.json", _straddle())
    assert gate.check_reverify_expect_text_not_inside_anchor(tree) == []


def test_wired_into_main():
    src = Path(HERE, "preship_gate.py").read_text(encoding="utf-8")
    assert "all_errors += check_reverify_expect_text_not_inside_anchor(repo_root)" in src
