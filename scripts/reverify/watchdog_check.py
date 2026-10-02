"""fleet_watchdog check for the daily rolling re-verification (Orchestrator 2026-10-02 12:43).

    python scripts/reverify/watchdog_check.py [status_json]      exit 0 = OK, 1 = ALERT (reason on stdout)

ALERT if:
  - the daily job hasn't finished an --apply run in the last 30 hours (or never ran);
  - any AUTOMATABLE record's verified date is more than 25 days old. Ages are computed now from the
    verified_dates snapshot in the status file, so a stalled job still ages its records.
MANUAL records are reported for information only and never alert.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

DEFAULT = os.path.join(os.environ.get("REVERIFY_STATE_DIR", r"C:\Users\Devin\Orchestrator\state"),
                       "reverify_status.json")
STALE_DAYS = 25
MAX_SILENCE_H = 30


def check(status, now: datetime):
    if not status or status.get("mode") != "apply":
        return False, "ALERT reverify: no daily --apply run recorded"
    now = now if now.tzinfo else now.astimezone()
    silent_h = (now - datetime.fromisoformat(status["finished"])).total_seconds() / 3600
    if silent_h > MAX_SILENCE_H:
        return False, f"ALERT reverify: last daily run finished {silent_h:.0f}h ago (> {MAX_SILENCE_H}h)"
    today = now.astimezone().date()
    stale = []
    for rid, v in status.get("verified_dates", {}).items():
        try:
            age = (today - date.fromisoformat(str(v)[:10])).days
        except ValueError:
            age = 10_000
        if age > STALE_DAYS:
            stale.append(f"{rid}({age}d)")
    if stale:
        return False, f"ALERT reverify: {len(stale)} record(s) older than {STALE_DAYS} days: {', '.join(sorted(stale)[:15])}"
    return True, (f"OK reverify: last run {status.get('run_date')} counts={status.get('counts')} "
                  f"manual={len(status.get('manual_ids', []))}")


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT
    status = None
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            status = json.load(f)
    ok, msg = check(status, datetime.now(timezone.utc))
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
