"""fleet_watchdog check for the daily rolling re-verification (Orchestrator 2026-10-02 12:43).

    python scripts/reverify/watchdog_check.py [status_json]      exit 0 = OK, 1 = ALERT (reason on stdout)

ALERT if:
  - the daily job hasn't finished an --apply run in the last 30 hours (or never ran);
  - ANY record's verified date is more than 25 days old -- automated or MANUAL (Devin's bar via
    Orchestrator 2026-10-02 12:48: every record gets checked). Ages are computed now from the
    verified_dates snapshot in the status file, so a stalled job still ages its records. MANUAL records
    are ticketed to AssetLab at 20 days by the daily run, so this alert means that ticket wasn't worked;
  - the LIVE Worker's worst record age (/api/health `worst_record_age_days`) is LIVE_ALERT_DAYS or more
    (Orchestrator 2026-10-02 22:27, STALE-27). The runtime signup/send wall reads the Worker's BUNDLED data,
    which this job commits but never deploys, so git can be fresh while production ages toward its 30-day
    pause. Until AssetLab exposes the field this check reports "not exposed" and does not alert; an
    unreachable /api/health is reported but not alerted on here (site uptime is a separate check).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, datetime, timezone

DEFAULT = os.path.join(os.environ.get("REVERIFY_STATE_DIR", r"C:\Users\Devin\Orchestrator\state"),
                       "reverify_status.json")
STALE_DAYS = 25
MAX_SILENCE_H = 30
LIVE_ALERT_DAYS = 21
HEALTH_URL = os.environ.get("REVERIFY_HEALTH_URL", "https://deadline-radar.com/api/health")


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


def live_check(health: dict | None) -> tuple[bool, str]:
    if health is None:
        return True, "live age: /api/health unreachable"
    age = health.get("worst_record_age_days")
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return True, "live age: not exposed by /api/health yet"
    if age >= LIVE_ALERT_DAYS:
        return False, (f"ALERT reverify: LIVE worker worst record age {age}d >= {LIVE_ALERT_DAYS}d "
                       f"(signups pause at >30d) -- run scripts/deploy_worker.py")
    return True, f"live age {age}d"


def fetch_health(url: str = HEALTH_URL, opener=None) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "deadlineradar-watchdog/1.0"})  # bare urllib UA: 403
        with (opener or urllib.request.urlopen)(req, timeout=20) as r:
            out = json.loads(r.read().decode("utf-8"))
        return out if isinstance(out, dict) else None
    except Exception:
        return None


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT
    status = None
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            status = json.load(f)
    ok, msg = check(status, datetime.now(timezone.utc))
    live_ok, live_msg = live_check(fetch_health())
    # one line: fleet_watchdog keeps only the first line, and alerts at most once a day for this check
    alerts = [m for good, m in ((ok, msg), (live_ok, live_msg)) if not good]
    print(" | ".join(alerts) if alerts else f"{msg} | {live_msg}")
    return 0 if ok and live_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
