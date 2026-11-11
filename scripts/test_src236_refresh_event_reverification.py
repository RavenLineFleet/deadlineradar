"""SRC-236: the daily job must flip an undated event's needs_reverification once it crosses 30 days,
without a producer regen, and must never clear a flag or touch a dated event."""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refresh_event_reverification as rer  # noqa: E402

TODAY = date(2026, 11, 28)


def _doc():
    return {"_meta": {"x": "é"}, "events": [
        # undated conflict card verified 2026-10-29: 30 days on 11-28, not yet OVER the bar
        {"event_id": "edge30", "effective_date": None, "verified_date": "2026-10-29", "needs_reverification": False},
        # 31 days: must flip
        {"event_id": "old31", "effective_date": None, "verified_date": "2026-10-28", "needs_reverification": False},
        # missing verified_date: stale by definition (same as the producer)
        {"event_id": "nodate", "effective_date": None, "needs_reverification": False},
        # dated event, old verified_date: untouched (its arm needs producer inputs)
        {"event_id": "dated", "effective_date": "2027-01-01", "verified_date": "2026-01-01", "needs_reverification": False},
        # already flagged and fresh: never cleared
        {"event_id": "keep", "effective_date": None, "verified_date": "2026-11-27", "needs_reverification": True},
    ]}


def test_flips_only_undated_past_the_bar():
    doc = _doc()
    assert rer.refresh(doc, TODAY) == ["old31", "nodate"]
    got = {e["event_id"]: e["needs_reverification"] for e in doc["events"]}
    assert got == {"edge30": False, "old31": True, "nodate": True, "dated": False, "keep": True}


def test_bar_matches_producer_constant():
    # one day past the 30-day bar flips; the bar is the producer's, not a copy
    from build_change_events import NULL_DATE_REVERIFY_DAYS
    assert NULL_DATE_REVERIFY_DAYS == 30
    doc = {"events": [{"event_id": "e", "effective_date": None, "verified_date": "2026-10-29", "needs_reverification": False}]}
    assert rer.refresh(doc, date(2026, 11, 29)) == ["e"]


def test_run_writes_both_copies_identically_and_noop_leaves_bytes(tmp_path: pathlib.Path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    raw = json.dumps(_doc(), indent=2, ensure_ascii=False) + "\n"
    a.write_text(raw, encoding="utf-8", newline="\n")
    b.write_text(raw, encoding="utf-8", newline="\n")
    assert rer.run((a, b), TODAY) == ["old31", "nodate"]
    assert a.read_bytes() == b.read_bytes()
    assert b"\r\n" not in a.read_bytes()
    assert "é" in a.read_text(encoding="utf-8")
    before = a.read_bytes()
    assert rer.run((a, b), TODAY) == []       # idempotent
    assert a.read_bytes() == before


def test_live_file_round_trips_byte_identical():
    # the writer's format must equal the producer's, or a no-flip day would still churn the file
    f = rer.FILES[0]
    raw = f.read_bytes()
    assert (json.dumps(json.loads(raw), indent=2, ensure_ascii=False) + "\n").encode("utf-8") == raw
