#!/usr/bin/env python3
"""Mint an operator session for AuditLab/SecurityLab's cross-tenant IDOR
probe -- per Orchestrator's ruling (2026-09-23,
_AAA_orchestrator_20260923_second_test_tenant_for_IDOR.md).

Inserts a `firm_sessions` row directly via `wrangler d1 execute --remote`
(the SAME mechanism a real login uses, minus the password/email step --
see worker/src/store.ts's createSession() for the shape this mirrors) and
writes the RAW cookie value to a local, gitignored file. No password is
ever set on either tenant, so there is nothing to intercept and no new
public route -- this is the only way to authenticate as either tenant.

"Tenant A" resolves to the existing shared demo firm (demo_locked=1,
already in production, never a new row) -- the ruling's own "reuse the
demo firm" option, avoiding a second synthetic firm. "Tenant B" resolves
to Test Firm B (scripts/provision_test_tenant.py must have already run
`create`).

## Usage
    python3 scripts/mint_test_session.py --tenant A --ttl 24h
    python3 scripts/mint_test_session.py --tenant B --ttl 24h

Writes scripts/.test_tenant_A_session.txt / .test_tenant_B_session.txt
(gitignored) containing the raw cookie value
(`dr_firm_session=<value>`) and a one-line usage note. Never prints the
raw token to a report -- read it from the file locally.
"""

from __future__ import annotations

import argparse
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
STATE_FILE = REPO_ROOT / "scripts" / ".test_tenant_b_state.json"


def new_token() -> str:
    raw = secrets.token_bytes(32)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def wrangler_query(sql: str) -> list[dict]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote", "--json", "--command", sql],
        cwd=WORKER_DIR,
        capture_output=True,
        text=True,
        shell=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"wrangler d1 execute failed (exit {result.returncode})")
    data = json.loads(result.stdout)
    return data[0]["results"]


def wrangler_exec(sql: str) -> None:
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


def parse_ttl(ttl: str) -> timedelta:
    if ttl.endswith("h"):
        return timedelta(hours=int(ttl[:-1]))
    if ttl.endswith("d"):
        return timedelta(days=int(ttl[:-1]))
    raise SystemExit(f"--ttl must end in 'h' or 'd' (got {ttl!r})")


def resolve_tenant(tenant: str) -> tuple[str, str]:
    """Returns (firm_id, member_id)."""
    if tenant == "A":
        rows = wrangler_query("SELECT id, primary_member_id FROM firms WHERE demo_locked = 1 AND status = 'active' LIMIT 1;")
        if not rows:
            raise SystemExit("No demo_locked=1 firm found -- the shared demo firm doesn't exist right now.")
        firm_id = rows[0]["id"]
        member_id = rows[0]["primary_member_id"]
        if not member_id:
            raise SystemExit(f"Demo firm {firm_id} has no primary_member_id -- can't mint a session.")
        return firm_id, member_id
    if tenant == "B":
        if not STATE_FILE.exists():
            raise SystemExit(f"{STATE_FILE} not found -- run 'python3 scripts/provision_test_tenant.py create' first.")
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return state["firm_id"], state["member_id"]
    raise SystemExit(f"--tenant must be A or B (got {tenant!r})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, choices=["A", "B"])
    parser.add_argument("--ttl", default="24h", help="e.g. 24h or 1d")
    args = parser.parse_args()

    firm_id, member_id = resolve_tenant(args.tenant)
    ttl = parse_ttl(args.ttl)
    now = datetime.now(timezone.utc)
    expires_at = (now + ttl).isoformat().replace("+00:00", "Z")
    now_iso = now.isoformat().replace("+00:00", "Z")

    raw_token = new_token()
    token_hash = hash_token(raw_token)
    session_id = new_token()

    sql = (
        f"INSERT INTO firm_sessions (id, firm_id, member_id, session_token_hash, created_at, expires_at, last_seen_at, password_reset_authorized) "
        f"VALUES ({sql_str(session_id)}, {sql_str(firm_id)}, {sql_str(member_id)}, {sql_str(token_hash)}, "
        f"{sql_str(now_iso)}, {sql_str(expires_at)}, {sql_str(now_iso)}, 0);"
    )
    wrangler_exec(sql)

    out_path = REPO_ROOT / "scripts" / f".test_tenant_{args.tenant}_session.txt"
    out_path.write_text(
        f"# Test Firm {args.tenant} operator session -- minted {now_iso}, expires {expires_at}\n"
        f"# firm_id = {firm_id}\n"
        f"# Set this cookie in the request: dr_firm_session=<value below>\n"
        f"dr_firm_session={raw_token}\n",
        encoding="utf-8",
    )
    print(f"\nMinted session for tenant {args.tenant} (firm_id={firm_id}), expires {expires_at}.")
    print(f"Raw cookie value written to {out_path} (gitignored) -- read it locally, never paste it into a report.")


if __name__ == "__main__":
    main()
