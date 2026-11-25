"""MON-35: the stale-monitoring caveat must state the snapshot date, never claim a pause.

The 48h bar reads the synced repo copy, which lags DiffLab by about a day, so
"monitoring has been paused" was false whenever it fired.

    python -m pytest scripts/test_mon35_stale_caveat.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import generate  # noqa: E402

GENERATE_PY = os.path.join(os.path.dirname(HERE), "generate.py")


def _with_stats(tmp_path, monkeypatch, payload):
    p = tmp_path / "rule_change_coverage_stats.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(generate, "RULE_CHANGE_COVERAGE_STATS_PATH", p)


def test_caveat_names_snapshot_date(tmp_path, monkeypatch):
    _with_stats(tmp_path, monkeypatch, {"last_checked_at": "2026-11-23T12:28:58.737165+00:00"})
    c = generate._rule_change_monitoring_stale_caveat()
    assert "check run November 23, 2026" in c
    assert "last updated" not in c  # COPY-70: the date is the check, not our copy's sync
    assert "paused" not in c.lower()


def test_caveat_fallback_without_date(tmp_path, monkeypatch):
    _with_stats(tmp_path, monkeypatch, "{not json")
    c = generate._rule_change_monitoring_stale_caveat()
    assert "may be out of date" in c
    assert "paused" not in c.lower()


def test_pause_claim_gone_from_source():
    src = open(GENERATE_PY, encoding="utf-8").read()
    assert "monitoring has been paused" not in src
    assert "batch legal research in the meantime" not in src
