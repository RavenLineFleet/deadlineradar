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
