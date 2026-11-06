"""SRC-177: the Tennessee odd/even cohort is authorized by statute, not shown adopted."""
import json
import re
from pathlib import Path

import generate

ROOT = Path(__file__).resolve().parent.parent
CLAIM = re.compile(r"cohort|license number|odd[- /]or[- ]even|odd/even|odd- or even", re.I)


def _record(rel):
    data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    rows = next(v for v in data.values() if isinstance(v, list))
    return next(r for r in rows if r["id"] == "tn-all")


def test_record_carries_no_cohort_claim_in_any_shipped_field():
    for rel in ("data/cpa_deadlines.json", "worker/src/cpa_deadlines.json"):
        rec = dict(_record(rel))
        rec.pop("verification_history")
        hits = CLAIM.findall(json.dumps(rec, ensure_ascii=False))
        assert hits == [], (rel, hits)


def test_positive_control_claim_pattern_fires_on_the_old_wording():
    assert CLAIM.search("assigned to one of two cohorts by license number (odd or even)")


def test_year_end_row_has_no_cohort_claim():
    assert not CLAIM.search(generate.YEAR_END_ROW_NOTES["tennessee"])


def test_gap_sheet_uses_the_combined_records_own_note():
    rec = _record("data/cpa_deadlines.json")
    html = generate.render_data_gap_records([rec])
    assert "may be set by the board at its discretion" in html
    assert generate._DEFAULT_GAP_NOTE not in html


def test_other_gap_records_keep_the_default_note():
    html = generate.render_data_gap_records([{
        "license_type_label": "X", "cycle_description": "Y", "computation": {"type": "other", "note": "NOPE"}}])
    assert "NOPE" not in html
    assert "Your exact deadline depends" in html


def test_year_end_link_scopes_tennessee_to_firm_permits():
    tn = generate._year_end_reverse_link_html("tennessee", {"tennessee", "wisconsin"})
    other = generate._year_end_reverse_link_html("wisconsin", {"tennessee", "wisconsin"})
    assert "Firm permits here" in tn
    assert "Firm permits here" not in other
