"""fleet_watchdog check for the monthly re-verification (Orchestrator directive 2026-10-02, item 4).

    python scripts/reverify/watchdog_check.py [status_json]      exit 0 = OK, 1 = ALERT (reason on stdout)

ALERT if:
  - it's the 1st at/after 06:00 (local) or later in the month, and this month's run hasn't started
    (status cycle != current YYYY-MM, or no status file at all);
  - it's the 7th or later and any AUTOMATABLE record is still unconfirmed this cycle.
MANUAL records (recipe says it can't be automated) are reported for information, never alerted.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

DEFAULT = os.path.join(os.environ.get("REVERIFY_STATE_DIR", r"C:\Users\Devin\Orchestrator\state"), "reverify_status.json")


def check(status: dict | None, now: datetime) -> tuple[bool, str]:
    cycle = f"{now:%Y-%m}"
    due = now.day > 1 or now.hour >= 6
    if due and (not status or status.get("cycle") != cycle or status.get("mode") != "apply"):
        return False, f"ALERT reverify: monthly run for {cycle} has not started (last cycle={status and status.get('cycle')})"
    if not status:
        return True, "OK reverify: before 06:00 on the 1st, run not due yet"
    pending = status.get("unconfirmed_automatable", [])
    if now.day >= 7 and pending:
        return False, (f"ALERT reverify: {len(pending)} automatable record(s) still unconfirmed on day {now.day}: "
                       f"{', '.join(pending[:15])}{' ...' if len(pending) > 15 else ''}")
    return True, (f"OK reverify {cycle}: counts={status.get('counts')} pending_automatable={len(pending)} "
                  f"manual={len(status.get('manual_ids', []))}")


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT
    status = None
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            status = json.load(f)
    ok, msg = check(status, datetime.now())
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
