#!/usr/bin/env python3
"""Operator tool for Founding Firms grants (Devin decision 2026-10-09).

Five CPA firms get Growth-tier access free for 365 days: a Stripe trial with
no card collected, which Stripe cancels at day 365 if no card is on file. A
firm gets the trial only if a row exists in D1 `founding_firm_grants` (migration
0090) when it clicks Subscribe on Growth. This script is the ONLY writer of
that table. The 5-row cap is enforced by the schema itself
(slot PRIMARY KEY CHECK (slot BETWEEN 1 AND 5)), not by this script.

## Real-firm check (operator, BEFORE running `grant`)
A real CPA firm (state board lookup URL or firm website), more than 3 staff,
not a duplicate of an existing firm or grant, not a bot or a friend. Denied or
unclear = no row = no trial. Record WHAT you checked in --evidence (e.g.
"board lookup <url>; 6 staff listed on firm site"); no licence numbers, no
personal data, no email addresses (the script refuses an evidence note that
contains '@' or any character outside plain prose; SQL is passed to wrangler
as a temp .sql file, never on the command line).

## Usage (dry-run is the default; nothing is written without --apply)
    python scripts/founding_firm_grant.py status
    python scripts/founding_firm_grant.py grant  --firm-id <id> --verified-by devin --evidence "..."  [--apply]
    python scripts/founding_firm_grant.py revoke --firm-id <id>  [--apply]

Add --local to run against the local D1 emulation instead of production.

`grant` takes the lowest unused slot in ONE atomic INSERT ... SELECT whose WHERE
clause also requires: the firm exists, is active, is not demo_locked or a test
tenant, is on the free plan and has NEVER been a Stripe customer (checkout withholds the
trial from any firm with a stripe_customer_id), and has no grant yet.
Zero rows inserted = refused; the script then re-reads and says why.

`revoke` only removes a grant whose trial has NOT started. A started trial has a
live Stripe subscription: cancel that in the Stripe dashboard first (the
customer.subscription.deleted webhook then drops the firm to free), and the slot
stays consumed on purpose ("exactly 5, ever"). The script refuses and says so.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WORKER_DIR = Path(__file__).resolve().parent.parent / "worker"
FIRM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
# Belt and braces on top of --file: plain prose and URLs only. Admits ? ; < > (board-lookup query strings,
# ordinary prose); still refuses " % & | ^ backtick backslash and newlines, and '@' is refused separately.
EVIDENCE_RE = re.compile(r"^[A-Za-z0-9 .,:;/()'_#+=?<>-]{20,500}$")
VERIFIED_BY = ("devin", "orchestrator")
MAX_SLOTS = 5
GROWTH_SEAT_CAP = 10  # worker/src/tiers.ts firm_growth; the trial is Growth-only
# Same definition as store.countFirmLicenses(): removed-by-admin staff do not count.
ROSTER_COUNT_SQL = (
    "(SELECT COUNT(*) FROM subscribers WHERE firm_id = f.id "
    "AND NOT (status = 'stopped' AND stop_reason = 'removed_by_admin'))"
)

# The lowest slot in 1..5 not already taken, or NULL when all five are used.
LOWEST_FREE_SLOT = (
    "SELECT MIN(v) AS slot FROM (SELECT 1 AS v UNION ALL SELECT 2 UNION ALL SELECT 3 "
    "UNION ALL SELECT 4 UNION ALL SELECT 5) WHERE v NOT IN (SELECT slot FROM founding_firm_grants)"
)


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def exe(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"Could not find '{name}' on PATH. Is Node installed?")
    return found


def d1(sql: str, local: bool) -> list[dict]:
    # SQL goes through a real .sql file (`--file`), never an inline `--command`
    # string. On Windows `npx` is npx.CMD and CreateProcess routes it through
    # cmd.exe regardless of shell=False, so any operator text on the command
    # line is re-parsed by cmd.exe (SecurityLab MEDIUM-3: `&` ran a command,
    # `%VAR%` expanded silently). Same pattern as scripts/manage_blocklist.py.
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        sql_path = Path(f.name)
    try:
        cmd = [exe("npx"), "wrangler", "d1", "execute", "deadlineradar", "--json", "--file", str(sql_path)]
        cmd.append("--local" if local else "--remote")
        proc = subprocess.run(cmd, cwd=WORKER_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    finally:
        sql_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SystemExit(f"wrangler failed ({proc.returncode}):\n{proc.stderr or proc.stdout}")
    start = proc.stdout.find("[")
    try:
        return json.loads(proc.stdout[start:])
    except (ValueError, json.JSONDecodeError):
        raise SystemExit(f"could not parse wrangler output:\n{proc.stdout}")


def rows(sql: str, local: bool) -> list[dict]:
    out = d1(sql, local)
    return out[0].get("results", []) if out else []


# D1's `meta.changes` is not reported by every wrangler mode (absent under
# --local), so every write below is confirmed by reading the row back instead.


def check_firm_id(firm_id: str) -> None:
    if not FIRM_ID_RE.match(firm_id):
        raise SystemExit("--firm-id must be the firm's id (letters, digits, - and _ only)")


def cmd_status(args) -> None:
    grants = rows(
        "SELECT slot, firm_id, granted_at, verified_by, trial_started_at, stripe_subscription_id "
        "FROM founding_firm_grants ORDER BY slot",
        args.local,
    )
    for g in grants:
        state = "trial started " + g["trial_started_at"] if g["trial_started_at"] else "granted, not started"
        print(f"slot {g['slot']}: firm {g['firm_id']}  {state}  (verified_by {g['verified_by']}, {g['granted_at']})")
    print(f"{MAX_SLOTS - len(grants)} of {MAX_SLOTS} slots left")
    problems = rows(
        "SELECT id, received_at, event_type, firm_id FROM stripe_webhook_events "
        "WHERE event_type LIKE 'founding_trial:%' ORDER BY received_at",
        args.local,
    )
    for pr in problems:
        sub = pr["id"].rsplit(":", 1)[-1]
        print(f"PROBLEM {pr['received_at']}: {pr['event_type']} firm {pr['firm_id']} subscription {sub}  (check it in Stripe: cancelled by the webhook, or cancel by hand if the alert says CANCEL FAILED)")
    if not problems:
        print("no founding-trial webhook problems recorded")


def explain_refusal(firm_id: str, local: bool) -> str:
    existing = rows(f"SELECT slot FROM founding_firm_grants WHERE firm_id = {sql_str(firm_id)}", local)
    if existing:
        return f"firm already holds slot {existing[0]['slot']}"
    firm = rows(
        "SELECT status, demo_locked, is_test_tenant, plan_tier, stripe_subscription_id, stripe_customer_id, referred_by_firm_id, referral_discount_pending FROM firms "
        f"WHERE id = {sql_str(firm_id)}",
        local,
    )
    if not firm:
        return "no firm with that id"
    f = firm[0]
    if f["status"] != "active":
        return f"firm status is {f['status']!r}, not 'active'"
    if f["demo_locked"] or f["is_test_tenant"]:
        return "firm is a demo or test tenant"
    if f["plan_tier"] != "free" or f["stripe_subscription_id"] or f["stripe_customer_id"]:
        return f"firm is or was a Stripe customer (plan {f['plan_tier']!r}); a founding firm must never have been one"
    if f["referred_by_firm_id"] or f["referral_discount_pending"]:
        return "firm arrived through a referral: its referral rewards cannot resolve on a $0 trial, so it cannot hold a grant"
    roster = rows("SELECT " + ROSTER_COUNT_SQL.replace("f.id", sql_str(firm_id)) + " AS n", local)[0]["n"]
    if roster > GROWTH_SEAT_CAP:
        return f"roster has {roster} staff; the trial is Growth-only (cap {GROWTH_SEAT_CAP}), so this firm could not use it"
    used = rows("SELECT COUNT(*) AS n FROM founding_firm_grants", local)[0]["n"]
    if used >= MAX_SLOTS:
        return "all 5 founding slots are taken"
    return "unknown (the guarded INSERT matched no row)"


def cmd_grant(args) -> None:
    check_firm_id(args.firm_id)
    if args.verified_by not in VERIFIED_BY:
        raise SystemExit(f"--verified-by must be one of {VERIFIED_BY}")
    note = args.evidence.strip()
    if len(note) < 20:
        raise SystemExit("--evidence must say what was checked (at least 20 characters)")
    if "@" in note:
        raise SystemExit("--evidence must not contain an email address or other personal data")
    if not EVIDENCE_RE.match(note):
        raise SystemExit("--evidence may only use letters, digits, spaces and . , : ; / ( ) ' _ # + = ? < > - (20-500 chars)")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    sql = (
        "INSERT INTO founding_firm_grants (slot, firm_id, granted_at, verified_by, evidence_note) "
        f"SELECT s.slot, f.id, {sql_str(now)}, {sql_str(args.verified_by)}, {sql_str(note)} "
        f"FROM ({LOWEST_FREE_SLOT}) s, firms f "
        f"WHERE f.id = {sql_str(args.firm_id)} AND s.slot IS NOT NULL "
        "AND f.status = 'active' AND f.demo_locked = 0 AND f.is_test_tenant = 0 "
        "AND f.plan_tier = 'free' AND f.stripe_subscription_id IS NULL AND f.stripe_customer_id IS NULL "
        f"AND {ROSTER_COUNT_SQL} <= {GROWTH_SEAT_CAP} "
        "AND f.referred_by_firm_id IS NULL AND f.referral_discount_pending = 0 "
        "AND NOT EXISTS (SELECT 1 FROM founding_firm_grants g WHERE g.firm_id = f.id)"
    )
    print("SQL:\n" + sql + "\n")
    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply after the real-firm check.")
        cmd_status(args)
        return
    d1(sql, args.local)
    landed = rows(
        f"SELECT slot FROM founding_firm_grants WHERE firm_id = {sql_str(args.firm_id)} AND granted_at = {sql_str(now)}",
        args.local,
    )
    if not landed:
        raise SystemExit("REFUSED: " + explain_refusal(args.firm_id, args.local))
    print(f"GRANTED slot {landed[0]['slot']}.")
    cmd_status(args)


def cmd_revoke(args) -> None:
    check_firm_id(args.firm_id)
    row = rows(f"SELECT slot, trial_started_at FROM founding_firm_grants WHERE firm_id = {sql_str(args.firm_id)}", args.local)
    if not row:
        raise SystemExit("no grant for that firm")
    if row[0]["trial_started_at"]:
        raise SystemExit(
            "REFUSED: the trial already started (live Stripe subscription). Cancel that subscription in the Stripe "
            "dashboard instead; the slot stays consumed by design."
        )
    sql = (
        f"DELETE FROM founding_firm_grants WHERE firm_id = {sql_str(args.firm_id)} AND trial_started_at IS NULL"
    )
    print("SQL:\n" + sql + "\n")
    print(
        "NOTE: a Checkout session this firm opened in the last hour (founding sessions expire after 1 h) can still "
        "complete after the revoke. The webhook then cancels that trial subscription, leaves the firm's tier alone "
        "and sends an alert, so it is safe, but expect that alert."
    )
    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply.")
        return
    d1(sql, args.local)
    if rows(f"SELECT 1 AS x FROM founding_firm_grants WHERE firm_id = {sql_str(args.firm_id)}", args.local):
        raise SystemExit("REFUSED: grant still present (trial may have just started); re-run status")
    print("REVOKED (slot freed; no trial had started).")
    cmd_status(args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "grant", "revoke"):
        p = sub.add_parser(name)
        p.add_argument("--local", action="store_true", help="use the local D1 emulation, not production")
        if name != "status":
            p.add_argument("--firm-id", required=True)
            p.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
        if name == "grant":
            p.add_argument("--verified-by", required=True)
            p.add_argument("--evidence", required=True)
    args = ap.parse_args()
    {"status": cmd_status, "grant": cmd_grant, "revoke": cmd_revoke}[args.cmd](args)


if __name__ == "__main__":
    main()
