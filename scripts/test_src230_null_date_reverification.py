"""SRC-230 (AuditLab, 2026-11-11): a record with no effective date can be flagged for re-verification.

    python -m pytest scripts/test_src230_null_date_reverification.py -q

needs_reverification used to come only from "has the effective date passed?", so it could never be
true for a record with effective_date null: PROPOSED, ADOPTED_PENDING_LEGISLATURE and every
SOURCE_CONFLICT card (7 of 22 at 74ef8721a). Those are the records whose status is most likely to
change without us noticing. Producer files for LA/CO also carry an explicit
needs_reverification: false with a verified_date, so an arm that only applied when the producer
gave no claim would still never fire. The age arm is ORed over both branches.
"""
import json
import os
import pathlib
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_change_events as bce  # noqa: E402

TODAY = date(2026, 11, 11)


def _difflab_event(slug: str, verified: str | None, **extra) -> dict:
    rec = {
        "jurisdiction_slug": slug,
        "citation": "Test Admin. Code 1.1",
        "citation_url": "https://example.test/rule",
        "status": "PROPOSED",
        "summary_public": "A proposed rule change.",
        "effective_date": None,
        "needs_reverification": False,
        "confidence": "single_source",
    }
    if verified is not None:
        rec["verified_date"] = verified
    rec.update(extra)
    return rec


def _load(tmp_path, monkeypatch, events: dict[str, dict]) -> dict:
    for name, rec in events.items():
        (tmp_path / f"event_{name}.json").write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(bce, "DIFFLAB_EVENTS_DIR", pathlib.Path(tmp_path))
    out, _rejected = bce.load_difflab_events(TODAY)
    return {e["jurisdiction_slug"]: e for e in out}


def test_null_date_record_past_bar_is_flagged_despite_producer_false(tmp_path, monkeypatch):
    old = (TODAY - timedelta(days=31)).isoformat()
    by = _load(tmp_path, monkeypatch, {"a": _difflab_event("louisiana", old)})
    assert by["louisiana"]["needs_reverification"] is True


def test_null_date_record_inside_bar_is_not_flagged(tmp_path, monkeypatch):
    fresh = (TODAY - timedelta(days=30)).isoformat()
    by = _load(tmp_path, monkeypatch, {"a": _difflab_event("louisiana", fresh)})
    assert by["louisiana"]["needs_reverification"] is False


def test_null_date_record_without_verified_date_is_flagged(tmp_path, monkeypatch):
    by = _load(tmp_path, monkeypatch, {"a": _difflab_event("louisiana", None)})
    assert by["louisiana"]["needs_reverification"] is True


def test_dated_record_unchanged_by_age_arm(tmp_path, monkeypatch):
    # Future effective date, old verification: the age arm is for null-date records only.
    old = (TODAY - timedelta(days=200)).isoformat()
    rec = _difflab_event("arizona", old, status="ENACTED", effective_date="2027-01-01")
    by = _load(tmp_path, monkeypatch, {"a": rec})
    assert by["arizona"]["needs_reverification"] is False


def test_source_conflict_card_flagged_past_bar():
    stale = (TODAY - timedelta(days=31)).isoformat()
    fresh = (TODAY - timedelta(days=5)).isoformat()
    assert bce._null_date_record_stale(None, stale, TODAY) is True
    assert bce._null_date_record_stale(None, fresh, TODAY) is False
    assert bce._null_date_record_stale(None, "not-a-date", TODAY) is True
    assert bce._null_date_record_stale(date(2027, 1, 1), stale, TODAY) is False


def test_build_conflict_branch_uses_age_arm(tmp_path, monkeypatch):
    real = json.loads(bce.RULES.read_text(encoding="utf-8"))
    c1, c2 = [r for r in real["records"] if r.get("source_conflict") is True][:2]
    stale = dict(c1, verified_date=(TODAY - timedelta(days=40)).isoformat())
    fresh = dict(c2, verified_date=(TODAY - timedelta(days=3)).isoformat())
    p = tmp_path / "mobility_rules.json"
    p.write_text(json.dumps({"_meta": {}, "records": [stale, fresh]}), encoding="utf-8")
    monkeypatch.setattr(bce, "RULES", p)
    events, _rej, _amb = bce.build(TODAY)
    by = {e["jurisdiction_slug"]: e for e in events if e.get("kind") == bce.KIND_CONFLICT}
    assert by[c1["state_slug"]]["needs_reverification"] is True
    assert by[c2["state_slug"]]["needs_reverification"] is False
