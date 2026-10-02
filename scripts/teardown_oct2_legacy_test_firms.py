"""One-off teardown of 4 of the 5 leftover `is_test_tenant=1` firms found
during the SecurityLab #5/#11 test-account cleanup (2026-10-02). Orchestrator
directive: "Tear down any not in active use; ask AuditLab before touching its
probe firm." All 4 here are confirmed stale (no `firm_sessions` activity in
5+ weeks, one with zero sessions ever):

    orch-clicktest-firm-01                       last seen 2026-08-08, 7 sessions
    CPm-Vjjh4hP_1E32bedDiJ_Cs87b905W6ph0ZOjDPpY   last seen 2026-08-08, 1 session
    Y_HeN_g2zYSkfR1JbDD-DswP1gN7oFL_imyxAPTgIpg   never logged in, 0 sessions
    dPR755EkpdMp49nA1DZ4p6WY7ZURu0gyLSRnmi_A748   last seen 2026-08-24, 2 sessions

Deliberately EXCLUDES ZRjXZ6kQHCSktY0ccG8AhbrTCccbCifcx3LDvI6sY1A ("Test Firm
B (AuditLab IDOR probe)") -- that one is AuditLab's, not touched here.

Covers all 14 firm_id-referencing tables (confirmed live against
sqlite_master, not assumed from memory of an earlier cleanup), in the same
dependency-safe order discovered the hard way during today's #5/#11
teardown: firms.primary_member_id -> firm_members.id is a circular FK, so it
must be nulled before firm_members rows can be deleted. Every statement is
scoped by `is_test_tenant = 1` directly (never just the hardcoded id list),
so a wrong id here can never take down a real firm.

    python3 scripts/teardown_oct2_legacy_test_firms.py
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = REPO_ROOT / "worker"

FIRM_IDS = [
    "orch-clicktest-firm-01",
    "CPm-Vjjh4hP_1E32bedDiJ_Cs87b905W6ph0ZOjDPpY",
    "Y_HeN_g2zYSkfR1JbDD-DswP1gN7oFL_imyxAPTgIpg",
    "dPR755EkpdMp49nA1DZ4p6WY7ZURu0gyLSRnmi_A748",
]

CHILD_TABLES = [
    "subscribers", "firm_login_tokens", "firm_sessions", "cpe_entries",
    "firm_oauth_identities", "mobility_completions", "stripe_webhook_events",
    "documents", "firm_nps_responses", "firm_testimonials", "firm_members",
    "firm_2fa_pending_tokens", "firm_rule_change_notifications", "checklist_items",
]


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def run_wrangler_sql(sql: str, *, label: str) -> None:
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    print(f"\n--- SQL to execute ({label}), {len(statements)} statement(s) ---")
    for i, stmt in enumerate(statements, 1):
        print(f"\n[{i}/{len(statements)}] {stmt};")
        result = subprocess.run(
            ["npx", "wrangler", "d1", "execute", "deadlineradar", "--remote", "--command", stmt + ";"],
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


def main() -> None:
    ids = "(" + ", ".join(sql_str(f) for f in FIRM_IDS) + ")"
    test_tenant_ids = f"(SELECT id FROM firms WHERE id IN {ids} AND is_test_tenant = 1)"

    statements = [
        # Circular FK (firms.primary_member_id -> firm_members.id) and the
        # self-referential referred_by_firm_id -- null both before deleting
        # firm_members/firms.
        f"UPDATE firms SET primary_member_id = NULL, referred_by_firm_id = NULL "
        f"WHERE id IN {ids} AND is_test_tenant = 1;",
    ]
    for table in CHILD_TABLES:
        statements.append(f"DELETE FROM {table} WHERE firm_id IN {test_tenant_ids};")
    statements.append(f"DELETE FROM firms WHERE id IN {ids} AND is_test_tenant = 1;")

    run_wrangler_sql("\n".join(statements), label="teardown 4 legacy test firms (Oct-2 inventory)")


if __name__ == "__main__":
    main()
