"""FRESH-5: the sitewide freshness denominator must account for every dated dataset."""
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import preship_gate as g  # noqa: E402


def _copy_repo(tmp_path):
    (tmp_path / "data").mkdir()
    shutil.copy(REPO / "generate.py", tmp_path)
    for f in (REPO / "data").glob("*.json"):
        shutil.copy(f, tmp_path / "data")
    return tmp_path


def test_real_repo_is_accounted_for():
    assert g.check_freshness_denominator_covers_datasets(REPO) == []


def test_new_dated_dataset_fails(tmp_path):
    r = _copy_repo(tmp_path)
    (r / "data" / "new_ds.json").write_text(json.dumps({"records": [{"verified_date": "2026-10-01"}]}), encoding="utf-8")
    errs = g.check_freshness_denominator_covers_datasets(r)
    assert len(errs) == 1 and "new_ds.json" in errs[0]


def test_undated_dataset_passes(tmp_path):
    r = _copy_repo(tmp_path)
    (r / "data" / "other.json").write_text(json.dumps({"records": [{"x": 1}]}), encoding="utf-8")
    assert g.check_freshness_denominator_covers_datasets(r) == []


def test_exclusion_removal_fails(tmp_path, monkeypatch):
    r = _copy_repo(tmp_path)
    monkeypatch.setattr(g, "_FRESHNESS_DENOMINATOR_EXCLUSIONS", {k: v for k, v in g._FRESHNESS_DENOMINATOR_EXCLUSIONS.items() if k != "reg_change_events.json"})
    errs = g.check_freshness_denominator_covers_datasets(r)
    assert len(errs) == 1 and "reg_change_events.json" in errs[0]


def test_assistant_fields_table_shape_is_enforced(monkeypatch, tmp_path):
    """REG-4: a str value silently disabled the assistant-API leak guard."""
    assert g.check_assistant_api_fields_no_internal_notes(REPO / "data") == []
    bad = dict(g._ASSISTANT_API_FIELDS_BY_DATASET)
    bad["reg_change_events.json"] = "prose instead of a list"
    monkeypatch.setattr(g, "_ASSISTANT_API_FIELDS_BY_DATASET", bad)
    errs = g.check_assistant_api_fields_no_internal_notes(REPO / "data")
    assert len(errs) == 1 and "REG-4" in errs[0]


def test_assistant_fields_table_has_reg_change_fields():
    assert g._ASSISTANT_API_FIELDS_BY_DATASET["reg_change_events.json"] == ["summary_public", "topic", "citation"]
    assert set(g._ASSISTANT_API_FIELDS_BY_DATASET) == {k for k, v in g._ASSISTANT_API_FIELDS_BY_DATASET.items() if isinstance(v, list)}


def test_exclusion_reasons_live_in_the_exclusion_map():
    assert "/es/deadline-calculator/" in g._FRESHNESS_DENOMINATOR_EXCLUSIONS["reg_change_events.json"]
    assert "IS a re-verified dataset" in g._FRESHNESS_DENOMINATOR_EXCLUSIONS["competitor_prices.json"]


def test_assistant_leak_guard_fires_on_planted_leak(tmp_path):
    """Positive control: the guard must actually be RED on a real leak, not just green on clean data."""
    for f in (REPO / "data").glob("*.json"):
        shutil.copy(f, tmp_path)
    d = json.loads((tmp_path / "reg_change_events.json").read_text(encoding="utf-8"))
    d["events"][0]["summary_public"] = "Per internal notes: needs_reverification, see data_gap_note TODO."
    (tmp_path / "reg_change_events.json").write_text(json.dumps(d), encoding="utf-8")
    errs = g.check_assistant_api_fields_no_internal_notes(tmp_path)
    assert errs and "needs_reverification" in errs[0]
