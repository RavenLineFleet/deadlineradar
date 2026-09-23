#!/usr/bin/env python3
"""Provision (or tear down) "Test Firm B" -- a synthetic, is_test_tenant=1
firm in production D1, per Orchestrator's ruling (2026-09-23,
_AAA_orchestrator_20260923_second_test_tenant_for_IDOR.md) unblocking
AuditLab's cross-tenant IDOR test, escalated 8 cycles as the charter's #1
open security item ("does firm A's session return firm B's rows" had
never been executed live).

## Guardrails this script exists to satisfy

1. `is_test_tenant = 1`, `demo_locked = 0` -- see migration 0079's own
   docstring for why demo_locked is never reused (it's wired into the
   public /firm/demo-login route's non-deterministic lookup and the
   reserved demo-roster reseed logic, both of which assume exactly one
   demo_locked=1 row exists). Excluded from every real send path via
   is_test_tenant instead (worker/src/scheduler.ts + the 5 index.ts
   handlers this ruling's implementation also fixed; proven by
   scripts/preship_gate.py's check_demo_locked_email_coverage(), which
   now requires BOTH flags to be live-guarded).
2. Fake staff/license data, emails on @example.invalid (IANA-reserved,
   RFC 2606, guaranteed never deliverable) -- modelled on the shape of
   the real demo firm's roster (a few subscriber rows with a real
   firm_id, state_slug, deadline_fields), not its exact content.
3. `status = 'active'` (required by requireFirmSession()/verifySession()
   for ANY authenticated route to work -- exclusion is is_test_tenant's
   job, never status's).
4. No public route ever resolves to this firm -- no password is ever
   set, no referral_code is ever minted, and it's never linked from any
   other row's referred_by_firm_id.

## Usage
    python3 scripts/provision_test_tenant.py create   # writes SQL, applies it
    python3 scripts/provision_test_tenant.py teardown  # deletes Test Firm B + its data
    python3 scripts/provision_test_tenant.py verify    # read-only: prints its current state

This runs `wrangler d1 execute deadlineradar --remote` itself (via
subprocess) -- no separate manual step. `create`/`teardown` print the SQL
before running it, and `teardown` requires typing the firm id back to
confirm (protects against running it against the wrong id by accident).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "worker"
STATE_FILE = REPO_ROOT / "scripts" / ".test_tenant_b_state.json"  # gitignored -- see teardown notes below


def new_token() -> str:
    """Same shape as worker/src/store.ts's newToken(): 32 random bytes,
    base64url, no padding."""
    raw = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def hash_token(raw: str) -> str:
    """Same as worker/src/store.ts's hashToken(): SHA-256 hex digest."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cooldown_key(email: str) -> str:
    """Same as worker/src/store.ts's cooldownKey()."""
    normalized = email.strip().lower()
    local, _, domain = normalized.partition("@")
    folded = local.split("+")[0].replace(".", "")
    return f"{folded}@{domain}"


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def run_wrangler_sql(sql: str, *, label: str) -> None:
    print(f"\n--- SQL to execute ({label}) ---")
    print(sql)
    print("--- end SQL ---\n")
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote", "--command", sql],
        cwd=WORKER_DIR,
        capture_output=True,
        text=True,
        shell=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"wrangler d1 execute failed (exit {result.returncode})")


def cmd_create() -> None:
    if STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} already exists -- Test Firm B looks already provisioned. Run 'verify' or 'teardown' first.")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")

    firm_id = new_token()
    member_id = new_token()
    admin_email = "test-firm-b-admin@example.invalid"
    admin_unsub_token = secrets.token_hex(16)

    staff = [
        {
            "email": "test-firm-b-staffer-1@example.invalid",
            "first_name": "Test",
            "state_slug": "texas",
            "staff_label": "Test Staffer One",
        },
        {
            "email": "test-firm-b-staffer-2@example.invalid",
            "first_name": "Probe",
            "state_slug": "california",
            "staff_label": "Test Staffer Two",
        },
    ]

    statements = []
    statements.append(
        f"INSERT INTO firms (id, name, admin_email, admin_name, plan_tier, status, created_at, "
        f"is_test_tenant, demo_locked, primary_member_id, admin_unsubscribe_token, rule_change_alerts_enabled, admin_digest_enabled) "
        f"VALUES ({sql_str(firm_id)}, {sql_str('Test Firm B (AuditLab IDOR probe -- synthetic)')}, "
        f"{sql_str(admin_email)}, {sql_str('Test Firm B Admin')}, 'free', 'active', {sql_str(now_iso)}, "
        f"1, 0, {sql_str(member_id)}, {sql_str(admin_unsub_token)}, 1, 1);"
    )
    statements.append(
        f"INSERT INTO firm_members (id, firm_id, email, name, role, invited_at, joined_at, created_at) "
        f"VALUES ({sql_str(member_id)}, {sql_str(firm_id)}, {sql_str(admin_email)}, {sql_str('Test Firm B Admin')}, "
        f"'partner', {sql_str(now_iso)}, {sql_str(now_iso)}, {sql_str(now_iso)});"
    )

    subscriber_ids = []
    for s in staff:
        sub_id = new_token()
        subscriber_ids.append(sub_id)
        statements.append(
            f"INSERT INTO subscribers (id, email, cooldown_key, state_slug, deadline_fields, first_name, "
            f"status, confirm_token, unsubscribe_token, renewed_token, created_at, confirmed_at, firm_id, staff_label) "
            f"VALUES ({sql_str(sub_id)}, {sql_str(s['email'])}, {sql_str(cooldown_key(s['email']))}, "
            f"{sql_str(s['state_slug'])}, '{{}}', {sql_str(s['first_name'])}, 'confirmed', "
            f"{sql_str(new_token())}, {sql_str(new_token())}, {sql_str(new_token())}, {sql_str(now_iso)}, "
            f"{sql_str(now_iso)}, {sql_str(firm_id)}, {sql_str(s['staff_label'])});"
        )

    sql = "\n".join(statements)
    run_wrangler_sql(sql, label="provision Test Firm B")

    STATE_FILE.write_text(
        json.dumps(
            {
                "firm_id": firm_id,
                "member_id": member_id,
                "admin_email": admin_email,
                "subscriber_ids": subscriber_ids,
                "created_at": now_iso,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nProvisioned. State written to {STATE_FILE} (gitignored -- add it to .gitignore if not already).")
    print(f"firm_id = {firm_id}")
    print("\nNext: python3 scripts/mint_test_session.py --tenant B --ttl 24h")


def cmd_verify() -> None:
    if not STATE_FILE.exists():
        print("No Test Firm B state file found -- not provisioned (or teardown already ran).")
        return
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    firm_id = state["firm_id"]
    sql = (
        f"SELECT id, name, is_test_tenant, demo_locked, status FROM firms WHERE id = {sql_str(firm_id)};"
    )
    run_wrangler_sql(sql, label="verify Test Firm B")


def cmd_teardown() -> None:
    if not STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} not found -- nothing to tear down (or it was already removed).")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    firm_id = state["firm_id"]
    print(f"About to permanently delete Test Firm B (firm_id={firm_id}) and all its rows.")
    typed = input(f"Type the firm_id to confirm ({firm_id}): ").strip()
    if typed != firm_id:
        raise SystemExit("Confirmation did not match -- aborted, nothing deleted.")

    statements = [
        f"DELETE FROM firm_sessions WHERE firm_id = {sql_str(firm_id)};",
        f"DELETE FROM subscribers WHERE firm_id = {sql_str(firm_id)};",
        f"DELETE FROM firm_members WHERE firm_id = {sql_str(firm_id)};",
        f"DELETE FROM firms WHERE id = {sql_str(firm_id)};",
    ]
    run_wrangler_sql("\n".join(statements), label="teardown Test Firm B")
    STATE_FILE.unlink()
    print("Torn down. State file removed.")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("create", "teardown", "verify"):
        print(__doc__)
        raise SystemExit(1)
    {"create": cmd_create, "teardown": cmd_teardown, "verify": cmd_verify}[sys.argv[1]]()


if __name__ == "__main__":
    main()
