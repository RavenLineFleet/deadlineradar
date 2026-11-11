"""SRC-236 (AuditLab, 2026-11-11): re-derive the SRC-230 age arm of `needs_reverification` in place.

`needs_reverification` on rule-change events is a stored field, computed by build_change_events.py
from `today` at producer run time. Nothing in the daily loop re-runs that producer (a full regen also
pulls unrelated DiffLab drift in, see SRC-232), so an undated event crossing the 30-day bar would keep
serving `false`. The daily reverify job runs this instead: for each event with no effective date, it
ORs in the same `_null_date_record_stale` arm the producer uses.

One-way by design: it only ever turns the flag ON. Clearing it still needs a dated re-read and a
producer run, never the passage of time. Dated events are untouched (their arm needs the producer's
raw inputs, which the served file does not carry).

Writes data/reg_change_events.json and its byte-identical Worker copy. Prints the flipped ids.
Exit 0 always on success (changed or not); non-zero only if the files can't be read or written.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_change_events import _null_date_record_stale  # noqa: E402  (same arm as the producer)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = (ROOT / "data" / "reg_change_events.json", ROOT / "worker" / "src" / "reg_change_events.json")


def _parse(value: object) -> "date | None":
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def refresh(doc: dict, today: date) -> list[str]:
    """Flip needs_reverification to True on undated events past the bar. Returns flipped event ids."""
    flipped = []
    for ev in doc.get("events", []):
        if ev.get("needs_reverification") is True:
            continue
        if _null_date_record_stale(_parse(ev.get("effective_date")), ev.get("verified_date"), today):
            ev["needs_reverification"] = True
            flipped.append(str(ev.get("event_id")))
    return flipped


def run(files=FILES, today: "date | None" = None) -> list[str]:
    today = today or date.today()
    src = files[0]
    doc = json.loads(src.read_text(encoding="utf-8"))
    flipped = refresh(doc, today)
    if flipped:
        out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        for f in files:
            f.write_text(out, encoding="utf-8", newline="\n")
    return flipped


def main() -> int:
    flipped = run()
    print(f"refresh_event_reverification: {len(flipped)} event(s) newly flagged"
          + (": " + ", ".join(flipped) if flipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
