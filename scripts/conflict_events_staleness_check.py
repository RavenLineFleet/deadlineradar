#!/usr/bin/env python3
"""Source-conflict card staleness check (AuditLab RC-19, 2026-09-23).

The "Sources under active disagreement" cards on /rule-changes/ carried no
verified_date anywhere -- no date shown on the card, no gate, no script --
unlike every other dated claim on this site, which all sit behind a 30-day
re-verification bar (cpa_deadlines_staleness_check.py and its 3 siblings).
Confirmed live: Guam's card was 26 days old, the other 5 were 53 days old,
as of 2026-09-22, with nothing surfacing that.

The underlying event records (kind == "source_conflict" in
data/reg_change_events.json) already carry `verified_date` -- it was just
never checked or rendered. This script is the check; generate.py's
_rule_conflict_card_html() (same commit) is the render.

Advisory only, same posture as every sibling staleness script in this
directory: prints a report, never blocks a build or exits non-zero on its
own. A human/agent re-verifying a flagged conflict against both primary
sources and bumping its verified_date is the fix, not code.

Usage:
    python scripts/conflict_events_staleness_check.py [repo_root]
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Same bar every sibling staleness script in this directory uses. See
# cpa_deadlines_staleness_check.py's own comment for why 30 specifically.
STALENESS_THRESHOLD_DAYS = 30


def main() -> None:
    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    data_path = repo_root / "data" / "reg_change_events.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))

    conflicts = [e for e in data["events"] if e.get("kind") == "source_conflict"]

    today = date.today()
    fresh, stale, unparseable, missing = [], [], [], []
    for e in conflicts:
        vd = e.get("verified_date")
        if not vd:
            missing.append(e)
            continue
        try:
            verified = datetime.strptime(vd, "%Y-%m-%d").date()
        except ValueError:
            unparseable.append(e)
            continue
        age_days = (today - verified).days
        (stale if age_days > STALENESS_THRESHOLD_DAYS else fresh).append((e, age_days))

    print(f"Conflict-card staleness check -- {today.isoformat()} (threshold {STALENESS_THRESHOLD_DAYS}d)")
    print(f"  fresh: {len(fresh)}   stale: {len(stale)}   unparseable: {len(unparseable)}   missing verified_date: {len(missing)}")

    if stale:
        print(f"\nSTALE -- past the {STALENESS_THRESHOLD_DAYS}-day bar, re-verify both sides before next ship ({len(stale)}):")
        for e, age_days in sorted(stale, key=lambda pair: -pair[1]):
            print(f"  [{e['event_id']}] {e.get('jurisdiction')} -- verified_date={e['verified_date']} ({age_days}d old)")
            print(f"    citation: {e.get('citation_url')}")
    if unparseable:
        print(f"\nUNPARSEABLE verified_date ({len(unparseable)}) -- treat as stale until fixed:")
        for e in unparseable:
            print(f"  [{e['event_id']}] {e.get('jurisdiction')} -- verified_date={e.get('verified_date')!r}")
    if missing:
        print(f"\nMISSING verified_date entirely ({len(missing)}) -- treat as stale until fixed:")
        for e in missing:
            print(f"  [{e['event_id']}] {e.get('jurisdiction')}")

    if not (stale or unparseable or missing):
        print("\nPASS -- every conflict card is within the freshness bar.")


if __name__ == "__main__":
    main()
