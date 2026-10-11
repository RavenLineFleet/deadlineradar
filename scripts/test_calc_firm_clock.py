"""Regression pins for the /deadline-calculator/ firm hint (AuditLab CALC-1..5, 2026-10-11).

    python -m pytest scripts/test_calc_firm_clock.py -q

_calc_firm_clock() decides which of two factual claims a state's visitors read:
"different" = "your firm's registration renews on a different clock", "separate"
= only "a firm registration is a separate filing". It is derived from
next_deadline_computed on records the daily re-verify job edits, so a data change
can silently move a state between the two. The golden sets below fail loudly
when that happens; if the move is legitimate, re-derive the sets and update them
deliberately (and have the claim re-read) rather than loosening the test.
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, str(REPO_ROOT))
import generate  # noqa: E402

# Firm renews on a provably different date, or the individual answer is
# personal/cohort/gap while the firm has one fixed date.
DIFFERENT = {"colorado", "florida", "georgia", "idaho", "illinois", "kansas", "kentucky", "maryland",
             "missouri", "nebraska", "nevada", "oklahoma", "oregon"}
# Same date as the individual (AuditLab Bucket A) ...
SEPARATE_SAME_DATE = {"alaska", "arkansas", "connecticut", "indiana", "iowa", "louisiana", "minnesota",
                      "mississippi", "north-dakota", "south-carolina", "virginia", "west-virginia",
                      "wisconsin", "wyoming"}
# ... or a firm record whose date is not computed (Bucket C).
SEPARATE_NO_FIRM_DATE = {"arizona", "guam", "massachusetts", "new-jersey", "new-mexico", "vermont", "washington"}


def _states():
    recs = json.load(open(REPO_ROOT / "data" / "cpa_deadlines.json", encoding="utf-8"))["records"]
    by = defaultdict(list)
    for r in recs:
        by[r["state_slug"]].append(r)
    return by


def test_golden_buckets_for_the_current_dataset():
    by = _states()
    got = {slug: generate._calc_firm_clock(rs) for slug, rs in by.items()}
    assert {s for s, v in got.items() if v == "different"} == DIFFERENT
    assert {s for s, v in got.items() if v == "separate"} == SEPARATE_SAME_DATE | SEPARATE_NO_FIRM_DATE
    firm_types = generate._FIRM_ONLY_LICENSE_TYPES
    no_firm = {slug for slug, rs in by.items() if not any(r.get("license_type") in firm_types for r in rs)}
    assert {s for s, v in got.items() if v == ""} == no_firm and len(no_firm) == 21


def _rec(kind, date):
    return {"license_type": kind, "next_deadline_computed": date}


def test_classifier_rules_on_synthetic_records():
    f = generate._calc_firm_clock
    assert f([_rec("individual", "2027-06-30")]) == ""                                   # no firm record
    assert f([_rec("individual", "2027-06-30"), _rec("firm", "2027-06-30")]) == "separate"   # same date
    assert f([_rec("individual", "2027-06-30"), _rec("firm", "2026-12-31")]) == "different"  # differs (firm earlier)
    assert f([_rec("individual", "2027-06-30"), _rec("firm", None)]) == "separate"          # firm date unknown
    assert f([_rec("individual", None), _rec("firm", "2026-12-31")]) == "different"         # personal/gap vs fixed
    assert f([_rec("individual", None), _rec("firm", None)]) == "separate"
    assert f([_rec("individual", "2027-06-30"), _rec("individual", "2027-12-31"),
              _rec("cpa_firm", "2027-06-30")]) == "different"                               # no single individual date


def test_gap_states_never_open_with_the_individual_date_prefix():
    js = generate._DEADLINE_CALC_JS
    assert "entry.kind === 'gap') ? '' : 'This is your individual license date. '" in js
    assert js.count("firmPrefix +") == 2     # both branches go through the prefix guard
    assert "has_firm_deadline" not in js and "has_firm_deadline" not in generate._deadline_calculator_widget_data.__code__.co_names


def test_es_calculator_result_links_do_not_404():
    """The ES page lives at /es/deadline-calculator/ but only 5 pages exist under /es/, so a
    '../<slug>/' result link resolved to /es/<slug>/ (404 for all states) until 2026-10-11."""
    from datetime import date
    by = _states()
    en = generate._deadline_calculator_widget_html(by, date(2026, 10, 11), "en")
    es = generate._deadline_calculator_widget_html(by, date(2026, 10, 11), "es")
    assert 'var DR_CALC_BASE = "../";' in en
    assert 'var DR_CALC_BASE = "../../";' in es
    assert "DR_CALC_BASE + slug" in generate._DEADLINE_CALC_JS and "'<a href=\"../' + slug" not in generate._DEADLINE_CALC_JS
