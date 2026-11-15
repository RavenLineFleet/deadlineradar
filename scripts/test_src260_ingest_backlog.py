"""SRC-260 (AuditLab, 2026-11-15): /rule-changes/ must disclose an ingest-side stall.

    python -m pytest scripts/test_src260_ingest_backlog.py -q

PA and VA events sat in DiffLab's store 31/34 days, never published, while the page's only
freshness gate read the producer's crawl heartbeat and called the feed current. The lag
advisory that could have said so was skipped in every AssetLab/_wt_* worktree because the
producer dir was resolved at a fixed depth. These pin: the backlog measure (aged, fresh,
retired, published, missing detected_at, no dir), the page sentence, and the walk-up lookup.
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_change_events as bce  # noqa: E402

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def _generate():
    if "generate" in sys.modules:
        return sys.modules["generate"]
    sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location("generate", REPO_ROOT / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generate"] = mod
    spec.loader.exec_module(mod)
    return mod


def _event(event_id: str, detected_at) -> dict:
    rec = {
        "event_id": event_id,
        "jurisdiction_slug": "pennsylvania",
        "jurisdiction": "Pennsylvania",
        "citation": "Act 140 of 1947",
        "citation_url": "https://www.palegis.us/statutes/x",
        "verified_date": "2026-11-01",
        "confidence": "single_source",
        "summary_public": "A real summary.",
        "kind": "rule_change",
        "effective_date": None,
        "status": "ENACTED_DATE_PENDING",
        "source": "difflab_reg_change_engine",
    }
    if detected_at is not None:
        rec["detected_at"] = detected_at
    return rec


def _setup(tmp_path, monkeypatch, producer: list[dict], published_ids: list[str]):
    g = _generate()
    prod = tmp_path / "Orchestrator" / "reg_change_events"
    prod.mkdir(parents=True)
    for i, rec in enumerate(producer):
        (prod / f"event_{i}.json").write_text(json.dumps(rec), encoding="utf-8")
    feed = tmp_path / "reg_change_events.json"
    feed.write_text(json.dumps({"_meta": {}, "events": [{"event_id": e} for e in published_ids]}),
                    encoding="utf-8")
    monkeypatch.setattr(bce, "DIFFLAB_EVENTS_DIR", prod)
    monkeypatch.setattr(g, "REG_CHANGE_EVENTS_PATH", feed)
    return g


def test_backlog_classifies_each_case(tmp_path, monkeypatch):
    old = (NOW - timedelta(days=31)).isoformat()
    fresh = (NOW - timedelta(days=3)).isoformat()
    retired = sorted(bce.RETIRED_EVENT_IDS)[0]
    g = _setup(tmp_path, monkeypatch, [
        _event("pennsylvania-regwatch-aged", old),
        _event("pennsylvania-regwatch-fresh", fresh),
        _event("pennsylvania-regwatch-published", old),
        _event("pennsylvania-regwatch-nodate", None),
        _event(retired, old),
    ], published_ids=["pennsylvania-regwatch-published"])
    assert g._rule_change_ingest_backlog(NOW) == [
        "pennsylvania-regwatch-aged", "pennsylvania-regwatch-nodate",
    ]


def test_backlog_unknown_without_producer_dir(tmp_path, monkeypatch):
    g = _generate()
    monkeypatch.setattr(bce, "DIFFLAB_EVENTS_DIR", tmp_path / "absent")
    assert g._rule_change_ingest_backlog(NOW) is None


def test_page_discloses_backlog_and_is_silent_without_one(monkeypatch):
    g = _generate()
    monkeypatch.setattr(g, "_rule_change_ingest_backlog", lambda now=None: ["a", "b"])
    html = g.build_rule_changes_page()
    assert "2 more changes our monitoring detected over 14 days ago are still waiting" in html
    monkeypatch.setattr(g, "_rule_change_ingest_backlog", lambda now=None: [])
    assert "our monitoring detected over" not in g.build_rule_changes_page()


def test_unmeasured_backlog_warns_at_build_time(monkeypatch, capsys):
    # SRC-262: None (store not found) must not be silent like [] (nothing waiting).
    g = _generate()
    monkeypatch.setattr(g, "_rule_change_ingest_backlog", lambda now=None: None)
    html = g.build_rule_changes_page()
    assert "our monitoring detected over" not in html
    assert "[SRC-262]" in capsys.readouterr().err
    monkeypatch.setattr(g, "_rule_change_ingest_backlog", lambda now=None: [])
    g.build_rule_changes_page()
    assert "[SRC-262]" not in capsys.readouterr().err


def test_producer_dir_found_from_a_shallower_worktree(tmp_path, monkeypatch):
    prod = tmp_path / "Orchestrator" / "reg_change_events"
    prod.mkdir(parents=True)
    fake_root = tmp_path / "AssetLab" / "_wt_x"  # one level shallower than b3_saas/<repo>
    fake_root.mkdir(parents=True)
    monkeypatch.setattr(bce, "ROOT", fake_root)
    assert bce._default_difflab_events_dir() == prod


def test_lag_check_ignores_retired_ids(tmp_path, monkeypatch):
    import rule_change_ingestion_lag_check as rcilc
    retired = sorted(bce.RETIRED_EVENT_IDS)[0]
    prod = tmp_path / "prod"
    prod.mkdir()
    (prod / "event_0.json").write_text(json.dumps(_event(retired, NOW.isoformat())), encoding="utf-8")
    monkeypatch.setattr(bce, "DIFFLAB_EVENTS_DIR", prod)
    result = rcilc.collect_lag(REPO_ROOT)
    assert result["status"] == "ok"
    assert retired not in result["unpublished"]


def test_live_feed_has_no_aged_backlog_today():
    # Positive control on the real data: PA is published, VA/ID a006 are retired.
    g = _generate()
    if not bce.DIFFLAB_EVENTS_DIR.is_dir():
        return
    assert "pennsylvania-regwatch-53f514f989a5" not in (g._rule_change_ingest_backlog() or [])
