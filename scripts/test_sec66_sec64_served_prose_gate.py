"""SEC-66: mobility_rules.json's served prose gets a screen. SEC-64: nested and list-shaped assistant
fields do too. Each arm plants a real leak in a copy of the data and watches the check fire."""
import json
import shutil
from pathlib import Path

import pytest

import preship_gate as g

REPO = Path(__file__).resolve().parent.parent
MARKER = "RESOLVED 2026-08-17"
SNAKE = "internal_review_note"


def _mobility_root(tmp_path, mutate=None):
    data = json.loads((REPO / "worker/src/mobility_rules.json").read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    (tmp_path / "worker/src").mkdir(parents=True)
    (tmp_path / "worker/src/mobility_rules.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def _with_field(data, field):
    return next(r for r in data["records"] if r.get(field))


def test_shipped_dataset_is_clean():
    assert g.check_mobility_verification_marker_leak(REPO) == []
    assert g.check_assistant_api_fields_no_internal_notes(REPO / "data") == []


@pytest.mark.parametrize("field", ["flux_summary", "individual_criteria_ambiguity_note",
                                   "individual_criteria_grandfather_note", "citation"])
def test_marker_in_each_served_string_field_fails_the_hard_gate(tmp_path, field):
    def plant(d):
        r = _with_field(d, field)
        r[field] = f"{r[field]} {MARKER}"
    errs = g.check_mobility_verification_marker_leak(_mobility_root(tmp_path, plant))
    assert len(errs) == 1 and field in errs[0] and MARKER in errs[0]


def test_marker_inside_the_pathways_list_fails_the_hard_gate(tmp_path):
    def plant(d):
        r = _with_field(d, "individual_criteria_pathways")
        r["individual_criteria_pathways"][-1] += f" {MARKER}"
    errs = g.check_mobility_verification_marker_leak(_mobility_root(tmp_path, plant))
    assert len(errs) == 1 and "individual_criteria_pathways[]" in errs[0]


def test_marker_in_an_unserved_field_is_not_flagged(tmp_path):
    def plant(d):
        d["records"][0]["flux_note"] = f"UPDATE {MARKER}"
    assert g.check_mobility_verification_marker_leak(_mobility_root(tmp_path, plant)) == []


def test_other_patterns_are_advisory_not_hard(tmp_path):
    def plant(d):
        r = _with_field(d, "flux_summary")
        r["flux_summary"] += f" {SNAKE}"
    root = _mobility_root(tmp_path, plant)
    assert g.check_mobility_verification_marker_leak(root) == []
    assert any(SNAKE in f for f in g.collect_mobility_internal_note_candidates(root))


def test_renamed_field_fails_rather_than_screening_nothing(tmp_path):
    def rename(d):
        for r in d["records"]:
            if "flux_summary" in r:
                r["flux_summary_v2"] = r.pop("flux_summary")
    errs = g.check_mobility_verification_marker_leak(_mobility_root(tmp_path, rename))
    assert len(errs) == 1 and "[SEC-66][STRUCTURE]" in errs[0] and "flux_summary" in errs[0]


def test_missing_dataset_fails(tmp_path):
    errs = g.check_mobility_verification_marker_leak(tmp_path)
    assert len(errs) == 1 and "not found" in errs[0]


def test_served_strings_walks_lists_and_keeps_a_string_in_a_list_slot():
    assert g._served_strings({"p": "x"}, "p[]") == ["x"]
    assert g._served_strings({"p": ["x", "", 3]}, "p[]") == ["x"]
    assert g._served_strings({"a": [{"b": "x"}, {"c": "y"}, "z"]}, "a[].b") == ["x"]
    assert g._served_strings({}, "a[].b") == []


def _assistant_dir(tmp_path, filename, mutate):
    shutil.copytree(REPO / "data", tmp_path / "data")
    path = tmp_path / "data" / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return tmp_path / "data"


def test_citation_link_label_leak_fails(tmp_path):
    def plant(d):
        r = next(x for x in d["records"] if x.get("citation_links"))
        r["citation_links"][0]["label"] += f" {MARKER}"
    errs = g.check_assistant_api_fields_no_internal_notes(_assistant_dir(tmp_path, "reinstatement.json", plant))
    assert len(errs) == 1 and "citation_links[].label" in errs[0]


def test_citation_link_url_is_not_screened(tmp_path):
    def plant(d):
        r = next(x for x in d["records"] if x.get("citation_links"))
        r["citation_links"][0]["url"] = "https://example.gov/some_snake_case_path.pdf"
    assert g.check_assistant_api_fields_no_internal_notes(_assistant_dir(tmp_path, "reinstatement.json", plant)) == []


def test_applicability_note_leak_fails(tmp_path):
    def plant(d):
        r = next(x for x in d["records"] if x.get("applicability_note"))
        r["applicability_note"] += f" {MARKER}"
    errs = g.check_assistant_api_fields_no_internal_notes(_assistant_dir(tmp_path, "cpe_hours.json", plant))
    assert len(errs) == 1 and "applicability_note" in errs[0]
