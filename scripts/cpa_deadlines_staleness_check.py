#!/usr/bin/env python3
"""CPA-deadlines per-citation staleness check (2026-08-07, roadmap #45).

`data/cpa_deadlines.json` -- the product's single most important dataset -- has a per-record
`last_verified` field on all 89 records, but until now had no script surfacing which INDIVIDUAL
citations are overdue for re-verification, unlike its three sibling datasets
(cpe_hours_staleness_check.py, reinstatement_staleness_check.py,
rule_change_monitoring_staleness_check.py all already exist).

AuditLab STALE-17 (2026-09-12): the claim below that the Worker's runtime guard only checks a
single whole-dataset `as_of_date` was true when this was written but was falsified by STALE-5
(2026-08-13, ef4744eca): `checkDataFreshness()` -> `combinedAgeDays()` now anchors on the WORSE of
`as_of_date`'s own age and `worstRecordAgeDays()` -- the oldest `last_verified` across every
record -- so a single stale citation does trip the runtime guard, regardless of `as_of_date`. What
the runtime guard's refusal message does NOT do is name which specific record is the culprit (it
names only whether "as_of_date" or "its single oldest record's last_verified date" is binding).
That per-record identification -- which citations are stale, by id/state, sorted oldest-first, with
their source_url -- is this script's actual remaining value, the same way the CPE-hours script
provides it for that dataset (see that script's own docstring).

Advisory only: prints a report, never blocks a build or exits non-zero on its own -- same posture as
every other staleness script in this directory. A human/agent re-verifying a flagged citation's
source_url and bumping its last_verified is the fix, not code.

Usage:
    python scripts/cpa_deadlines_staleness_check.py [repo_root]
"""
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Same bar every sibling staleness script in this directory uses (and the
# same number worker/src/deadline.ts's own STALENESS_THRESHOLD_DAYS applies
# to the whole-dataset as_of_date) -- not a hard requirement this dataset
# must match that exactly, but no reason found to pick a different number
# for the SAME board-page-plus-statute verification standard.
STALENESS_THRESHOLD_DAYS = 30

# AuditLab STALE-46 (2026-10-07): the Worker guard (worker/src/deadline.ts
# ageDaysFromAsOf / worstRecordAgeDays) computes Math.round(instant diff in days) > 30,
# i.e. it trips at verified 00:00 UTC + 30d12h, not at a calendar-date +31. generate.py's
# badge/seal use the same age >= 30.5 instant (STALE-14). This lane used to floor on
# calendar dates (trips a day later), so the warning flipped after the runtime pause.
# These helpers are the one Python statement of that boundary; preship_gate's
# check_stale_boundary_parity pins them against the shipped TS expression.
STALE_ROUND_HALF_DAYS = 0.5


def stale_instant(verified: date) -> datetime:
    """First UTC instant at which the Worker guard refuses a record verified on `verified`."""
    midnight = datetime(verified.year, verified.month, verified.day, tzinfo=timezone.utc)
    return midnight + timedelta(days=STALENESS_THRESHOLD_DAYS, hours=24 * STALE_ROUND_HALF_DAYS)


def runtime_age_days(verified: date, now: datetime) -> int:
    """The Worker's age: Math.round((now - verified 00:00 UTC) / 86_400_000)."""
    midnight = datetime(verified.year, verified.month, verified.day, tzinfo=timezone.utc)
    return math.floor((now - midnight).total_seconds() / 86_400 + 0.5)


def main() -> None:
    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    data_path = repo_root / "data" / "cpa_deadlines.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc)
    today = now.date()
    fresh, stale, unparseable, missing = [], [], [], []
    for r in data["records"]:
        lv = r.get("last_verified")
        if not lv:
            missing.append(r)
            continue
        try:
            verified = datetime.strptime(lv, "%Y-%m-%d").date()
        except ValueError:
            unparseable.append(r)
            continue
        age_days = runtime_age_days(verified, now)  # same rounding as the Worker guard (STALE-46)
        (stale if age_days > STALENESS_THRESHOLD_DAYS else fresh).append((r, age_days))

    print(f"CPA-deadlines per-citation staleness check -- {today.isoformat()} (threshold {STALENESS_THRESHOLD_DAYS}d)")
    print(f"  fresh: {len(fresh)}   stale: {len(stale)}   unparseable: {len(unparseable)}   missing last_verified: {len(missing)}")

    if stale:
        print(f"\nSTALE -- past the {STALENESS_THRESHOLD_DAYS}-day bar, re-verify before next ship ({len(stale)}):")
        for r, age_days in sorted(stale, key=lambda pair: -pair[1]):
            print(f"  [{r['id']}] {r['state']} ({r.get('license_type_label')}) -- last_verified={r['last_verified']} ({age_days}d old)")
            print(f"    source: {r.get('source_url')}")
    if unparseable:
        print(f"\nUNPARSEABLE last_verified ({len(unparseable)}) -- treat as stale until fixed:")
        for r in unparseable:
            print(f"  [{r['id']}] {r['state']} -- last_verified={r.get('last_verified')!r}")
    if missing:
        print(f"\nMISSING last_verified entirely ({len(missing)}) -- treat as stale until fixed:")
        for r in missing:
            print(f"  [{r['id']}] {r['state']}")

    if not (stale or unparseable or missing):
        print("\nPASS -- every citation is within the freshness bar.")


if __name__ == "__main__":
    main()
