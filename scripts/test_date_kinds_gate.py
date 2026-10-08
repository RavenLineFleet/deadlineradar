"""Mutation tests for check_date_kinds (DATA-14 prevention, 2026-10-08).

The gate must PASS on the real tree (positive control: the machinery is intact)
and FAIL on each way the Alabama error could recur. Each mutation runs against a
temp copy of data/ and the rendered Alabama page.

    python -m pytest scripts/test_date_kinds_gate.py -q
"""
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "data").mkdir()
    for f in ("cpa_deadlines.json", "date_kinds.json"):
        shutil.copy(os.path.join(ROOT, "data", f), tmp_path / "data" / f)
    for slug in ("alabama", "colorado"):
        shutil.copytree(os.path.join(ROOT, "docs", slug), tmp_path / "docs" / slug)
    return tmp_path


def _edit_kinds(root, fn):
    p = root / "data" / "date_kinds.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fn(d["records"])
    p.write_text(json.dumps(d), encoding="utf-8")


def test_real_tree_passes(tree):
    assert gate.check_date_kinds(tree) == []


def test_alabama_as_license_expiry_with_its_own_dec_evidence_fails(tree):
    # THE original error: Dec 31 called a renewal/expiry date. Evidence names December, so the month
    # check alone passes it -- the "following expiration" rule is what catches it.
    _edit_kinds(tree, lambda r: r["al-all"].update(kind="license_expiry"))
    errs = gate.check_date_kinds(tree)
    assert any("al-all" in e and "AFTER expiration" in e for e in errs), errs


def test_alabama_as_license_expiry_with_september_evidence_fails_month_check(tree):
    _edit_kinds(tree, lambda r: r["al-all"].update(kind="license_expiry", evidence="expire on the last day of September of each year"))
    errs = gate.check_date_kinds(tree)
    assert any("al-all" in e and "December" in e for e in errs), errs


def test_alabama_page_relabelled_back_to_next_renewal_date_fails(tree):
    page = tree / "docs" / "alabama" / "index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("Renewal fee + CPE statement due", "Next renewal date"), encoding="utf-8")
    errs = gate.check_date_kinds(tree)
    assert any("al-all" in e and "still labels it" in e for e in errs), errs


def test_missing_classification_fails(tree):
    _edit_kinds(tree, lambda r: r.pop("fl-firm"))
    assert any("fl-firm" in e and "no entry" in e for e in gate.check_date_kinds(tree))


def test_stale_entry_fails(tree):
    _edit_kinds(tree, lambda r: r.update({"zz-ghost": {"kind": "license_expiry", "evidence": "x"}}))
    assert any("zz-ghost" in e and "stale" in e for e in gate.check_date_kinds(tree))


def test_unquotable_evidence_fails(tree):
    _edit_kinds(tree, lambda r: r["fl-firm"].update(evidence="expire December 31 of every year"))
    assert any("fl-firm" in e and "verbatim" in e for e in gate.check_date_kinds(tree))


def test_unknown_kind_fails(tree):
    _edit_kinds(tree, lambda r: r["fl-firm"].update(kind="probably_expiry"))
    assert any("fl-firm" in e and "unknown kind" in e for e in gate.check_date_kinds(tree))


def test_verbatim_evidence_naming_the_wrong_month_fails(tree):
    # ga-firm is computed 2028-06-30; its own text also says "before September 30" -- quotable, wrong month.
    _edit_kinds(tree, lambda r: r["ga-firm"].update(kind="renewal_due", evidence="requires renewal before September 30"))
    errs = gate.check_date_kinds(tree)
    assert any("ga-firm" in e and "June" in e for e in errs), errs


def test_colorado_firm_claimed_uniform_fails(tree):
    # POST-8 (the second published error): co-firm IS an expiry (kind is right) but its YEAR depends on the
    # firm's cohort. Classifying it population=uniform - the way the first version of this gate effectively
    # did - must go RED because the record's own text carries the caveat.
    _edit_kinds(tree, lambda r: r["co-firm"].update(population="uniform"))
    errs = gate.check_date_kinds(tree)
    assert any("co-firm" in e and "population is uniform but the record's own text says otherwise" in e for e in errs), errs


def test_non_uniform_record_needs_verbatim_population_evidence(tree):
    _edit_kinds(tree, lambda r: r["co-firm"].update(population_evidence="the board publishes the anchor years"))
    assert any("co-firm" in e and "population_evidence" in e for e in gate.check_date_kinds(tree))


def test_unknown_population_fails(tree):
    _edit_kinds(tree, lambda r: r["fl-firm"].update(population="mostly_uniform"))
    assert any("fl-firm" in e and "population must be one of" in e for e in gate.check_date_kinds(tree))


def test_colorado_page_relabelled_back_to_next_renewal_date_fails(tree):
    page = tree / "docs" / "colorado" / "index.html"
    t = page.read_text(encoding="utf-8").replace("Renewal date (depends on your firm&#x27;s cohort)", "Next renewal date")
    page.write_text(t, encoding="utf-8")
    assert any("co-firm" in e and "still labels it" in e for e in gate.check_date_kinds(tree))
