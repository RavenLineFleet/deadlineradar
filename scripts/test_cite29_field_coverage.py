"""SRC-179: check_partial_citation_flag_set must read every prose field, and may
only be cleared by something that dataset's pages actually read.

    python -m pytest scripts/test_cite29_field_coverage.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(HERE).parent
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

ADMISSION = "The rule does not itself state the amount."
DATASETS = ("cpa_deadlines", "cpe_hours", "reinstatement", "renewal_fees")


def _run(tmp_path, name, record):
    (tmp_path / "data").mkdir(exist_ok=True)
    for ds in DATASETS:
        recs = [record] if ds == name else []
        (tmp_path / "data" / f"{ds}.json").write_text(json.dumps({"records": recs}), encoding="utf-8")
    return gate.check_partial_citation_flag_set(tmp_path)


@pytest.mark.parametrize("name,field", [
    ("renewal_fees", "fee_notes"),
    ("cpe_hours", "notes"),
    ("cpe_hours", "applicability_note"),
    ("reinstatement", "lapse_trigger"),
    ("reinstatement", "penalty_cpe_notes"),
    ("reinstatement", "reinstatement_fee_notes"),
    ("cpa_deadlines", "cycle_description"),
    ("cpa_deadlines", "data_gap_note"),
    ("renewal_fees", "a_field_added_next_year"),
])
def test_admission_in_any_prose_field_is_caught(tmp_path, name, field):
    errors = _run(tmp_path, name, {"id": "x", field: ADMISSION})
    assert len(errors) == 1 and f"] {field} admits" in errors[0]


def test_nested_prose_is_caught(tmp_path):
    errors = _run(tmp_path, "cpa_deadlines", {"id": "x", "computation": {"note": ADMISSION}})
    assert len(errors) == 1 and "computation.note" in errors[0]


@pytest.mark.parametrize("field", sorted(gate._CITE29_NOT_PROSE_FIELDS))
def test_non_prose_fields_are_not_scanned(tmp_path, field):
    assert _run(tmp_path, "cpa_deadlines", {"id": "x", field: ADMISSION}) == []


def test_ordinary_prose_passes(tmp_path):
    assert _run(tmp_path, "renewal_fees", {"id": "x", "fee_notes": "A perfectly ordinary sentence."}) == []


@pytest.mark.parametrize("name", ["cpa_deadlines", "reinstatement"])
def test_flag_clears_datasets_that_read_it(tmp_path, name):
    record = {"id": "x", "data_gap_note": ADMISSION, "citation_covers_full_claim": False}
    assert _run(tmp_path, name, record) == []


def test_cpe_cleared_by_hours_basis_note_not_flag(tmp_path):
    assert _run(tmp_path, "cpe_hours", {"id": "x", "notes": ADMISSION, "hours_basis_note": "Unit conversion."}) == []
    assert "Set hours_basis_note" in _run(tmp_path, "cpe_hours", {"id": "x", "notes": ADMISSION})[0]


@pytest.mark.parametrize("name", ["cpe_hours", "renewal_fees"])
@pytest.mark.parametrize("value", [True, False])
def test_flag_is_an_error_where_no_page_reads_it(tmp_path, name, value):
    errors = _run(tmp_path, name, {"id": "x", "citation_covers_full_claim": value})
    assert len(errors) == 1 and "no " + name + " page reads it" in errors[0]


def test_flag_does_not_hide_a_fee_admission(tmp_path):
    record = {"id": "x", "fee_notes": ADMISSION, "citation_covers_full_claim": False}
    errors = _run(tmp_path, "renewal_fees", record)
    assert len(errors) == 2 and any("fee_notes admits" in e for e in errors)


def test_operational_record_stays_exempt(tmp_path):
    record = {"id": "x", "cycle_description": ADMISSION, "citation_class": "operational_record"}
    assert _run(tmp_path, "cpa_deadlines", record) == []


def test_real_tree_is_clean():
    assert gate.check_partial_citation_flag_set(REPO_ROOT) == []


def test_real_nevada_fee_note_trips_when_edited(tmp_path):
    data = json.loads((REPO_ROOT / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    record = next(r for r in data["records"] if r["id"] == "nevada-renewal-fee")
    assert "citation_covers_full_claim" not in record
    record["fee_notes"] += " " + ADMISSION
    assert any("fee_notes admits" in e for e in _run(tmp_path, "renewal_fees", record))


def test_every_dataset_declares_how_it_clears():
    assert set(gate._CITE29_CLEARED_BY) == set(DATASETS)


# GATE-112 (AuditLab 2026-11-07): live SRC-183 wording one synonym off the list.
@pytest.mark.parametrize("phrase", [
    "The statute does not itself say they're paid together.",
    "We have not independently verified the grace period.",
])
def test_gate112_synonyms_are_caught(tmp_path, phrase):
    errors = _run(tmp_path, "cpa_deadlines", {"id": "x", "cycle_description": phrase})
    assert len(errors) == 1 and "cycle_description admits" in errors[0]


# GATE-112 residual (AuditLab 2026-11-07): live wordings the synonym list missed.
@pytest.mark.parametrize("phrase", [
    "The birth-month timing comes from the FAQ, not from statute or regulation text we have verified.",
    "Neither the Board's rules chapter nor its renewal page says how a given certificate's year is set.",
    "Nebraska's statute authorizes this as ten days within the preceding two calendar years and does not state hours.",
])
def test_gate112_residual_wordings_are_caught(tmp_path, phrase):
    errors = _run(tmp_path, "cpa_deadlines", {"id": "x", "cycle_description": phrase})
    assert len(errors) == 1 and "cycle_description admits" in errors[0]


# The traps: a source other than the law text, or a strength statement.
@pytest.mark.parametrize("phrase", [
    "The Board's page does NOT state how parity is originally assigned.",
    "The renewal page does not state a grace period, and the statute sets the cycle.",
])
def test_gate112_residual_traps_stay_clean(tmp_path, phrase):
    assert _run(tmp_path, "cpa_deadlines", {"id": "x", "cycle_description": phrase}) == []


# GATE-119: a named side-limb hedge doesn't demote the whole record.
LIMB = {"field": "penalty_cpe_notes", "sentence": ADMISSION}


def test_named_limb_sentence_clears_only_itself(tmp_path):
    record = {"id": "x", "penalty_cpe_notes": f"Figures are codified. {ADMISSION}", "citation_gap_limbs": [LIMB]}
    assert _run(tmp_path, "reinstatement", record) == []


def test_second_admission_in_the_limb_field_still_trips(tmp_path):
    other = "The rule does not itself say the rest."
    record = {"id": "x", "penalty_cpe_notes": f"{ADMISSION} {other}", "citation_gap_limbs": [LIMB]}
    assert len(_run(tmp_path, "reinstatement", record)) == 1


def test_same_sentence_in_another_field_still_trips(tmp_path):
    record = {"id": "x", "penalty_cpe_notes": ADMISSION, "data_gap_note": ADMISSION, "citation_gap_limbs": [LIMB]}
    assert len(_run(tmp_path, "reinstatement", record)) == 1


def test_limb_cannot_name_a_headline_field(tmp_path):
    record = {"id": "x", "data_gap_note": ADMISSION, "citation_gap_limbs": [{"field": "data_gap_note", "sentence": ADMISSION}]}
    assert len(_run(tmp_path, "reinstatement", record)) == 2


def test_stale_limb_sentence_is_an_error(tmp_path):
    record = {"id": "x", "penalty_cpe_notes": "Reworded.", "citation_gap_limbs": [LIMB]}
    assert len(_run(tmp_path, "reinstatement", record)) == 1


def test_non_admission_limb_is_an_error(tmp_path):
    record = {"id": "x", "penalty_cpe_notes": "Plain.", "citation_gap_limbs": [{"field": "penalty_cpe_notes", "sentence": "Plain."}]}
    assert len(_run(tmp_path, "reinstatement", record)) == 1


def test_limb_is_an_error_on_a_dataset_without_the_flag(tmp_path):
    record = {"id": "x", "penalty_cpe_notes": ADMISSION, "citation_gap_limbs": [LIMB]}
    assert len(_run(tmp_path, "cpe_hours", record)) == 2
