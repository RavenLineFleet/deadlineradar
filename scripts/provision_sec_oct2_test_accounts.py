#!/usr/bin/env python3
"""Provision (or tear down) 3 disposable test accounts for SecurityLab's
approved #5 IDOR + #11 live-legs test plan
(Orchestrator/inbox/securitylab_20261002_test_plan_5_and_11_live.md,
forwarded via AssetLab/inbox/_AAA_orchestrator_20261002_112208_
provision_sec_test_accounts.md).

This is a SEPARATE, independent probe from the existing AuditLab Test
Firm B tenant (scripts/provision_test_tenant.py) -- deliberately does not
reuse or touch it (that tenant's own state file is left alone).

## What this creates

- Firm A: is_test_tenant=1, demo_locked=0, 1 partner member, 1 subscriber
  ("license") row, 1 cpe_entries row tied to that subscriber.
- Firm B: same shape as A, fully independent firm_id.
- Member C: a SECOND firm_members row on Firm A, role='staff' -- no
  subscriber/CPE rows (SecurityLab's plan: "role-gating only, no rows").

All emails on @example.test (IANA-reserved, RFc 2606, never deliverable).
Same create-firm-then-member-then-UPDATE-pointer ordering as
provision_test_tenant.py (firms.primary_member_id / firm_members.firm_id
is a circular FK) and the same "one wrangler invocation per statement,
never a multi-statement batch" rule (D1 is not transactional across
separate wrangler calls -- isolate each statement so a failure stops
immediately instead of leaving a silently-partial tenant behind).

## Usage
    python3 scripts/provision_sec_oct2_test_accounts.py create
    python3 scripts/provision_sec_oct2_test_accounts.py mint-sessions
    python3 scripts/provision_sec_oct2_test_accounts.py verify
    python3 scripts/provision_sec_oct2_test_accounts.py teardown

`create` provisions the rows. `mint-sessions` inserts 3 firm_sessions rows
(A's partner, B's partner, C the staffer) and writes the raw cookie
values to local gitignored files -- read them locally, hand them to
SecurityLab via its own inbox, never paste a raw token into a committed
file or a report. `teardown` requires typing the firm_id of BOTH firms
back to confirm, and gates every DELETE on is_test_tenant=1 so a wrong/
stale state file can never take down a real firm.
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "worker"
STATE_FILE = REPO_ROOT / "scripts" / ".sec_oct2_test_accounts_state.json"  # gitignored


def new_token() -> str:
    raw = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cooldown_key(email: str) -> str:
    normalized = email.strip().lower()
    local, _, domain = normalized.partition("@")
    folded = local.split("+")[0].replace(".", "")
    return f"{folded}@{domain}"


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def run_wrangler_sql(sql: str, *, label: str) -> None:
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    print(f"\n--- SQL to execute ({label}), {len(statements)} statement(s) ---")
    for i, stmt in enumerate(statements, 1):
        print(f"\n[{i}/{len(statements)}] {stmt};")
        result = subprocess.run(
            ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote", "--command", stmt + ";"],
            cwd=WORKER_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise SystemExit(
                f"wrangler d1 execute failed on statement {i}/{len(statements)} (exit {result.returncode}) -- "
                f"statements 1..{i - 1} already committed; check state manually before retrying."
            )
    print("--- end SQL ---\n")


def wrangler_query(sql: str) -> list[dict]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote", "--json", "--command", sql],
        cwd=WORKER_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"wrangler d1 execute failed (exit {result.returncode})")
    data = json.loads(result.stdout)
    return data[0]["results"]


def build_firm(label: str, admin_email: str, now_iso: str) -> tuple[str, str, list[str]]:
    """Returns (firm_id, partner_member_id, statements)."""
    firm_id = new_token()
    member_id = new_token()
    stmts = [
        f"INSERT INTO firms (id, name, admin_email, admin_name, plan_tier, status, created_at, "
        f"is_test_tenant, demo_locked, admin_unsubscribe_token, rule_change_alerts_enabled, admin_digest_enabled) "
        f"VALUES ({sql_str(firm_id)}, {sql_str(f'SecurityLab Probe Firm {label} (synthetic, 2026-10-02)')}, "
        f"{sql_str(admin_email)}, {sql_str(f'SecurityLab Probe {label} Partner')}, 'free', 'active', {sql_str(now_iso)}, "
        f"1, 0, {sql_str(secrets.token_hex(16))}, 1, 1);",
        f"INSERT INTO firm_members (id, firm_id, email, name, role, invited_at, joined_at, created_at) "
        f"VALUES ({sql_str(member_id)}, {sql_str(firm_id)}, {sql_str(admin_email)}, "
        f"{sql_str(f'SecurityLab Probe {label} Partner')}, 'partner', {sql_str(now_iso)}, {sql_str(now_iso)}, {sql_str(now_iso)});",
        f"UPDATE firms SET primary_member_id = {sql_str(member_id)} WHERE id = {sql_str(firm_id)};",
    ]
    return firm_id, member_id, stmts


def build_license_and_cpe(firm_id: str, label: str, now_iso: str) -> tuple[str, str, list[str]]:
    """Returns (subscriber_id, cpe_entry_id, statements)."""
    sub_id = new_token()
    cpe_id = new_token()
    email = f"sec-probe-{label.lower()}-license@example.test"
    stmts = [
        f"INSERT INTO subscribers (id, email, cooldown_key, state_slug, deadline_fields, first_name, "
        f"status, confirm_token, unsubscribe_token, renewed_token, created_at, confirmed_at, firm_id, staff_label) "
        f"VALUES ({sql_str(sub_id)}, {sql_str(email)}, {sql_str(cooldown_key(email))}, 'texas', '{{}}', "
        f"{sql_str(f'Probe {label} Staffer')}, 'confirmed', {sql_str(new_token())}, {sql_str(new_token())}, "
        f"{sql_str(new_token())}, {sql_str(now_iso)}, {sql_str(now_iso)}, {sql_str(firm_id)}, "
        f"{sql_str(f'Probe {label} Staffer')});",
        f"INSERT INTO cpe_entries (id, firm_id, subscriber_id, entry_date, hours, category, description, "
        f"entered_by_actor_type, created_at) "
        f"VALUES ({sql_str(cpe_id)}, {sql_str(firm_id)}, {sql_str(sub_id)}, {sql_str(now_iso[:10])}, 2.0, "
        f"'general', {sql_str('Disposable test entry (SecurityLab #5/#11 probe)')}, 'admin', {sql_str(now_iso)});",
    ]
    return sub_id, cpe_id, stmts


def cmd_create() -> None:
    if STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} already exists -- looks already provisioned. Run 'verify' or 'teardown' first.")

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    firm_a_id, member_a_id, stmts_a = build_firm("A", "sec-probe-a-partner@example.test", now_iso)
    firm_b_id, member_b_id, stmts_b = build_firm("B", "sec-probe-b-partner@example.test", now_iso)
    sub_a_id, cpe_a_id, stmts_lic_a = build_license_and_cpe(firm_a_id, "A", now_iso)
    sub_b_id, cpe_b_id, stmts_lic_b = build_license_and_cpe(firm_b_id, "B", now_iso)

    member_c_id = new_token()
    stmt_c = (
        f"INSERT INTO firm_members (id, firm_id, email, name, role, invited_at, joined_at, created_at) "
        f"VALUES ({sql_str(member_c_id)}, {sql_str(firm_a_id)}, {sql_str('sec-probe-c-staff@example.test')}, "
        f"{sql_str('SecurityLab Probe C Staffer')}, 'staff', {sql_str(now_iso)}, {sql_str(now_iso)}, {sql_str(now_iso)});"
    )

    all_stmts = stmts_a + stmts_b + stmts_lic_a + stmts_lic_b + [stmt_c]
    run_wrangler_sql("\n".join(all_stmts), label="provision SecurityLab A/B/C")

    state = {
        "created_at": now_iso,
        "firm_a_id": firm_a_id,
        "member_a_id": member_a_id,
        "subscriber_a_id": sub_a_id,
        "cpe_a_id": cpe_a_id,
        "firm_b_id": firm_b_id,
        "member_b_id": member_b_id,
        "subscriber_b_id": sub_b_id,
        "cpe_b_id": cpe_b_id,
        "member_c_id": member_c_id,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\nProvisioned. State written to {STATE_FILE} (gitignored).")
    print(f"firm_a_id = {firm_a_id}\nfirm_b_id = {firm_b_id}\nmember_c_id (staff, in firm A) = {member_c_id}")
    print("\nNext: python3 scripts/provision_sec_oct2_test_accounts.py mint-sessions")


def cmd_mint_sessions() -> None:
    if not STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} not found -- run 'create' first.")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    now_iso = now.isoformat().replace("+00:00", "Z")

    sessions = [
        ("A", state["firm_a_id"], state["member_a_id"]),
        ("B", state["firm_b_id"], state["member_b_id"]),
        ("C", state["firm_a_id"], state["member_c_id"]),  # C logs into firm A as staff
    ]
    stmts = []
    outputs = []
    for label, firm_id, member_id in sessions:
        raw_token = new_token()
        token_hash = hash_token(raw_token)
        session_id = new_token()
        stmts.append(
            f"INSERT INTO firm_sessions (id, firm_id, member_id, session_token_hash, created_at, expires_at, "
            f"last_seen_at, password_reset_authorized) VALUES ({sql_str(session_id)}, {sql_str(firm_id)}, "
            f"{sql_str(member_id)}, {sql_str(token_hash)}, {sql_str(now_iso)}, {sql_str(expires_at)}, "
            f"{sql_str(now_iso)}, 0);"
        )
        outputs.append((label, firm_id, raw_token))

    run_wrangler_sql("\n".join(stmts), label="mint 3 sessions (A partner, B partner, C staff)")

    for label, firm_id, raw_token in outputs:
        out_path = REPO_ROOT / "scripts" / f".sec_oct2_tenant_{label}_session.txt"
        role_note = "partner" if label != "C" else "staff (firm A)"
        out_path.write_text(
            f"# SecurityLab probe tenant {label} session ({role_note}) -- minted {now_iso}, expires {expires_at}\n"
            f"# firm_id = {firm_id}\n"
            f"# Set this cookie in the request: dr_firm_session=<value below>\n"
            f"dr_firm_session={raw_token}\n",
            encoding="utf-8",
        )
        print(f"Minted session {label} -> {out_path} (gitignored)")
    print("\nRead these 3 files locally and hand the cookie values to SecurityLab via its own inbox -- never paste a raw token into a committed file or report.")


def cmd_verify() -> None:
    if not STATE_FILE.exists():
        print("No state file found -- not provisioned (or teardown already ran).")
        return
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    sql = (
        f"SELECT id, name, is_test_tenant, demo_locked, status FROM firms "
        f"WHERE id IN ({sql_str(state['firm_a_id'])}, {sql_str(state['firm_b_id'])});"
    )
    run_wrangler_sql(sql, label="verify firms A/B")
    sql2 = (
        f"SELECT id, firm_id, role, email FROM firm_members "
        f"WHERE id IN ({sql_str(state['member_a_id'])}, {sql_str(state['member_b_id'])}, {sql_str(state['member_c_id'])});"
    )
    run_wrangler_sql(sql2, label="verify members A/B/C")


def cmd_teardown() -> None:
    if not STATE_FILE.exists():
        raise SystemExit(f"{STATE_FILE} not found -- nothing to tear down.")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    firm_a_id = state["firm_a_id"]
    firm_b_id = state["firm_b_id"]
    print(f"About to permanently delete SecurityLab probe Firm A ({firm_a_id}) and Firm B ({firm_b_id}) and all their rows.")
    typed = input(f"Type both firm_ids separated by a space to confirm ({firm_a_id} {firm_b_id}): ").strip()
    if typed != f"{firm_a_id} {firm_b_id}":
        raise SystemExit("Confirmation did not match -- aborted, nothing deleted.")

    # Same is_test_tenant gate as provision_test_tenant.py's teardown -- every
    # DELETE checks is_test_tenant=1 directly, not just the state file's
    # firm_id, so a stale/wrong state file can never take down a real firm.
    ids = f"({sql_str(firm_a_id)}, {sql_str(firm_b_id)})"
    test_tenant_ids = f"(SELECT id FROM firms WHERE id IN {ids} AND is_test_tenant = 1)"
    stmts = [
        f"DELETE FROM firm_sessions WHERE firm_id IN {test_tenant_ids};",
        f"DELETE FROM cpe_entries WHERE firm_id IN {test_tenant_ids};",
        f"DELETE FROM subscribers WHERE firm_id IN {test_tenant_ids};",
        # firms.primary_member_id -> firm_members.id is a circular FK (live
        # 2026-10-02: the first real teardown run failed on statement 4/5
        # with SQLITE_CONSTRAINT_FOREIGNKEY precisely here) -- a firm's own
        # row points at its partner's firm_members row, so firm_members
        # cannot be deleted while any firm still points at it. Null it out
        # first, scoped the same is_test_tenant way as every other
        # statement here.
        f"UPDATE firms SET primary_member_id = NULL WHERE id IN {ids} AND is_test_tenant = 1;",
        f"DELETE FROM firm_members WHERE firm_id IN {test_tenant_ids};",
        f"DELETE FROM firms WHERE id IN {ids} AND is_test_tenant = 1;",
    ]
    run_wrangler_sql("\n".join(stmts), label="teardown SecurityLab probe A/B/C")
    STATE_FILE.unlink()
    for label in ("A", "B", "C"):
        p = REPO_ROOT / "scripts" / f".sec_oct2_tenant_{label}_session.txt"
        if p.exists():
            p.unlink()
    print("Torn down. State file and session files removed.")


def main() -> None:
    cmds = {"create": cmd_create, "mint-sessions": cmd_mint_sessions, "verify": cmd_verify, "teardown": cmd_teardown}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        raise SystemExit(1)
    cmds[sys.argv[1]]()


if __name__ == "__main__":
    main()
