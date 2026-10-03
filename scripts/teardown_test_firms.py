"""Generic double-scoped test-firm teardown, reusable across one-off cleanups.

Orchestrator/AuditLab directive (2026-10-02, inbox/_AAA_orchestrator_20261002_173923_
teardown_low.md + inbox/auditlab_20261002_CRAWL8_closed_teardown_review_probe_firm.md):
teardown_oct2_legacy_test_firms.py hand-rolled its CHILD_TABLES list and missed 5 of
the 19 tables that actually carry a firm_id column (activity_log, admin_digest_send_log,
compliance_attestations, feature_questionnaire_responses, reminder_log -- all declare
`firm_id TEXT NOT NULL` with no FK to `firms`, so the old script's FK-driven table
discovery silently skipped them). This version NEVER hand-lists tables: it discovers
every firm_id-bearing table at runtime straight from sqlite_master's own CREATE TABLE
text, the same ground truth AuditLab's reconciliation used (19 tables, cross-checked
against worker/migrations/ independently and landed on the same number).

Two distinct, separately-invoked operations, both using the discovered table list:

  sweep_orphans(ids)   -- for firm ids whose `firms` row is ALREADY gone (the 4
                          legacy ids teardown_oct2_legacy_test_firms.py already
                          deleted, which left orphan rows in the 5 missed tables).
                          Deletes `WHERE firm_id IN (<exact known-safe ids>)` directly
                          -- there is no `firms` row left to scope against, so safety
                          here comes from the ids being a fixed, hardcoded, known-test
                          list, same posture the original script used.

  teardown_firm(ids)   -- for a firm whose `firms` row still exists (e.g. AuditLab's
                          IDOR probe firm). Same double-scoping as the original script:
                          every statement is scoped by `is_test_tenant = 1`, either
                          directly on `firms` or via a subquery against it, so a wrong
                          id can never touch a real firm. Nulls the circular FKs
                          (primary_member_id, referred_by_firm_id) before deleting
                          children, then deletes firms last.

Usage:
    python3 scripts/teardown_test_firms.py sweep-orphans
    python3 scripts/teardown_test_firms.py teardown-probe-firm
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "worker"

# The 4 ids teardown_oct2_legacy_test_firms.py already deleted from `firms` --
# their rows in the 5 missed tables are the orphans this sweeps.
LEGACY_TEARDOWN_IDS = [
    "orch-clicktest-firm-01",
    "CPm-Vjjh4hP_1E32bedDiJ_Cs87b905W6ph0ZOjDPpY",
    "Y_HeN_g2zYSkfR1JbDD-DswP1gN7oFL_imyxAPTgIpg",
    "dPR755EkpdMp49nA1DZ4p6WY7ZURu0gyLSRnmi_A748",
]

# AuditLab's IDOR probe firm ("Test Firm B") -- cleared to tear down
# (auditlab_20261002_CRAWL8_closed_teardown_review_probe_firm.md), one
# condition: keep scripts/provision_sec_oct2_test_accounts.py in the repo
# as AuditLab's recreate path.
PROBE_FIRM_ID = "ZRjXZ6kQHCSktY0ccG8AhbrTCccbCifcx3LDvI6sY1A"


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def run_wrangler_sql(statements: list[str], *, label: str) -> None:
    print(f"\n--- SQL to execute ({label}), {len(statements)} statement(s) ---")
    for i, stmt in enumerate(statements, 1):
        print(f"\n[{i}/{len(statements)}] {stmt}")
        result = subprocess.run(
            ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote", "--command", stmt],
            cwd=WORKER_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise SystemExit(
                f"wrangler d1 execute failed on statement {i}/{len(statements)} (exit {result.returncode}) -- "
                f"statements 1..{i - 1} already committed; check state manually before retrying."
            )
    print("--- end SQL ---\n")


def _discover_tables_by_column(rows: list[dict], column: str, exclude: set[str]) -> list[str]:
    import re
    return sorted(
        r["name"] for r in rows
        if r.get("sql") and re.search(rf"\b{column}\b", r["sql"]) and r["name"] not in exclude
    )


def _fetch_schema() -> list[dict]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote",
         "--command", "SELECT name, sql FROM sqlite_master WHERE type='table';", "--json"],
        cwd=WORKER_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"wrangler d1 execute (schema discovery) failed (exit {result.returncode})")
    import json
    return json.loads(result.stdout)[0]["results"]


def discover_firm_id_tables() -> list[str]:
    """Never a hand list -- reads sqlite_master's own CREATE TABLE text for
    every table, at runtime, and keeps the ones declaring a `firm_id` column.
    Excludes `firms` itself (that has `id`, not `firm_id`)."""
    rows = _fetch_schema()
    tables = _discover_tables_by_column(rows, "firm_id", exclude={"firms"})
    if not tables:
        raise SystemExit("schema discovery returned 0 firm_id tables -- refusing to proceed on an empty list")
    print(f"discovered {len(tables)} firm_id-bearing table(s) from sqlite_master: {', '.join(tables)}")
    return tables


def discover_member_referencing_tables() -> list[str]:
    """A real FK-constraint failure during the probe-firm teardown (2026-10-02)
    found that firm_members has its own dependents -- firm_sessions,
    firm_login_tokens, and firm_2fa_pending_tokens all carry a nullable
    `member_id REFERENCES firm_members(id)` alongside their own firm_id, and
    firm_member_backup_codes has ONLY `member_id NOT NULL REFERENCES
    firm_members(id)`, no firm_id at all. Any row left in any of these four
    still pointing at a firm_members id blocks that row's delete below, so
    ALL of them must be cleared before firm_members, regardless of whether
    they're also in the firm_id-scoped list -- discovered the same way as
    the firm_id tables, so this doesn't need its own hand list either."""
    rows = _fetch_schema()
    tables = _discover_tables_by_column(rows, "member_id", exclude={"firm_members"})
    print(f"discovered {len(tables)} table(s) referencing firm_members via member_id: {', '.join(tables) or '(none)'}")
    return tables


def sweep_orphans(ids: list[str]) -> None:
    tables = discover_firm_id_tables()
    ids_sql = "(" + ", ".join(sql_str(f) for f in ids) + ")"
    statements = [f"DELETE FROM {table} WHERE firm_id IN {ids_sql};" for table in tables]
    run_wrangler_sql(statements, label="sweep orphan rows for already-deleted legacy test firms")


def teardown_firm(firm_id: str) -> None:
    tables = discover_firm_id_tables()
    member_referencing_tables = discover_member_referencing_tables()
    id_sql = sql_str(firm_id)
    test_tenant_ids = f"(SELECT id FROM firms WHERE id = {id_sql} AND is_test_tenant = 1)"
    member_ids = f"(SELECT id FROM firm_members WHERE firm_id IN {test_tenant_ids})"
    statements = [
        f"UPDATE firms SET primary_member_id = NULL, referred_by_firm_id = NULL "
        f"WHERE id = {id_sql} AND is_test_tenant = 1;",
    ]
    # Every table that references firm_members via member_id must be cleared
    # before firm_members is deleted below (whichever of the two scopings
    # applies to it), or that delete violates the FK -- see
    # discover_member_referencing_tables()'s own docstring.
    for table in member_referencing_tables:
        if table in tables:
            statements.append(f"DELETE FROM {table} WHERE firm_id IN {test_tenant_ids};")
        else:
            statements.append(f"DELETE FROM {table} WHERE member_id IN {member_ids};")
    for table in tables:
        if table in member_referencing_tables:
            continue  # already deleted above, before firm_members
        statements.append(f"DELETE FROM {table} WHERE firm_id IN {test_tenant_ids};")
    statements.append(f"DELETE FROM firms WHERE id = {id_sql} AND is_test_tenant = 1;")
    run_wrangler_sql(statements, label=f"teardown test firm {firm_id}")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("sweep-orphans", "teardown-probe-firm"):
        raise SystemExit("usage: python3 scripts/teardown_test_firms.py {sweep-orphans|teardown-probe-firm}")
    if sys.argv[1] == "sweep-orphans":
        sweep_orphans(LEGACY_TEARDOWN_IDS)
    else:
        teardown_firm(PROBE_FIRM_ID)


if __name__ == "__main__":
    main()
