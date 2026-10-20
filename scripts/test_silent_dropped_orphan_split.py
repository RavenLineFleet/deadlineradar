"""DROP-6 (AuditLab, 2026-10-20): resolveSilentDrop() (worker/src/store.ts) only runs inside
runReminderPass()'s per-subscriber loop, so a silent_drop_log row for a subscriber that's been
deleted outright can never be resolved -- it would otherwise read "open" forever, indistinguishable
from a live, still-broken one. Fix: split orphaned rows (subscriber_id not in subscribers) out of
the "open" count the advisory reports and the exit code it raises."""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import silent_dropped_subscribers_check as sdsc  # noqa: E402


def _fake_run_d1_query(responses):
    def _inner(sql):
        for needle, rows in responses.items():
            if needle in sql:
                return rows
        raise AssertionError(f"unexpected SQL, no fixture matched: {sql}")
    return _inner


def test_orphaned_row_excluded_from_open_count_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(sdsc, "_run_d1_query", _fake_run_d1_query({
        "FROM subscribers WHERE status": [],  # no confirmed subscribers -> dropped == []
        "SELECT id FROM subscribers": [{"id": "alive-1"}],  # the only subscriber that still exists
        "FROM silent_drop_log": [
            {"subscriber_id": "ghost-1", "email": "ghost@example.invalid", "state_slug": "fl",
             "reason": "no_deadline", "first_detected_at": "2026-09-23T00:00:00Z",
             "last_seen_at": "2026-10-02T00:00:00Z", "resolved_at": None},
        ],
    }))
    with pytest.raises(SystemExit) as ei:
        sdsc.main()
    assert ei.value.code == 0, "an orphaned-only row must not fail the advisory"
    out = capsys.readouterr().out
    assert "open in silent_drop_log (persisted, still-live subscriber): 0" in out
    assert "orphaned in silent_drop_log (subscriber deleted, cannot self-resolve): 1" in out
    assert "ghost@example.invalid" in out


def test_still_live_open_row_still_fails_and_is_not_mislabeled_orphaned(monkeypatch, capsys):
    monkeypatch.setattr(sdsc, "_run_d1_query", _fake_run_d1_query({
        "FROM subscribers WHERE status": [],
        "SELECT id FROM subscribers": [{"id": "alive-1"}],
        "FROM silent_drop_log": [
            {"subscriber_id": "alive-1", "email": "alive@example.invalid", "state_slug": "fl",
             "reason": "no_deadline", "first_detected_at": "2026-09-23T00:00:00Z",
             "last_seen_at": "2026-10-02T00:00:00Z", "resolved_at": None},
        ],
    }))
    with pytest.raises(SystemExit) as ei:
        sdsc.main()
    assert ei.value.code == 1, "a still-live open row must still fail the advisory"
    out = capsys.readouterr().out
    assert "open in silent_drop_log (persisted, still-live subscriber): 1" in out
    assert "orphaned in silent_drop_log (subscriber deleted, cannot self-resolve): 0" in out
    assert "alive@example.invalid" in out
