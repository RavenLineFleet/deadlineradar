#!/usr/bin/env python3
"""Deploy the Worker AND update `worker/.last_deploy_commit` in one step.

## Why this exists

`worker/.last_deploy_commit` is how this repo answers "is the deployed
Worker bundle current with the data files?" -- `worker_deploy_staleness_check.py`
reads it, and `preship_gate.py` surfaces the result as a ship advisory. It
is load-bearing: a stale marker means a future session cannot tell whether
a data-correctness fix actually reached production.

But NOTHING wrote it automatically. It was a manual step, documented in
`prevention_register.md` and in the deploy notes, and therefore a step that
gets skipped -- which is exactly what happened across the 2026-07-31 ship
sequence (four production deploys, marker left at a commit from days
earlier). Documenting a manual step harder does not fix a step that gets
forgotten; removing the human from it does.

So: deploy through this, and the marker cannot drift.

## Behaviour worth knowing

  * PRODUCTION deploys update the marker. PREVIEW deploys do NOT -- the
    marker tracks what is live in production, and letting a preview deploy
    stamp it would make it actively lie.
  * A dirty `worker/` tree ABORTS by default. The marker records a commit
    hash as shorthand for "this code is deployed"; deploying uncommitted
    code makes that claim false. `--allow-dirty` overrides for genuine
    emergencies, and deliberately skips the marker update rather than
    writing something untrue.
  * The marker is only written if wrangler actually succeeded.
  * PRODUCTION deploys also REFUSE if any repo migration hasn't been applied
    to the remote D1 database yet -- AuditLab DEPLOY-2 (2026-08-20): the
    dirty-tree guard only answers "is this code committed", never "is the
    database ready for it". A committed-but-not-yet-migrated schema change
    (SMS-2, same day) shipped straight through that gap: the code referenced
    columns migration 0068 was supposed to add first, but nothing checked
    whether it actually had. Two of the three failure modes were loud (SQL
    errors on live writes), but the reminder-send read path degraded
    SILENTLY -- `SELECT *` doesn't error on a missing column, so it just
    read the new field as undefined and "failed closed" for the wrong
    reason, meaning real subscribers quietly stopped getting texted with no
    crash to notice by. `--allow-dirty` and `--skip-migration-check` both
    skip this (same "genuine emergency, and the marker doesn't get written"
    posture as the dirty-tree override).

Usage:
    python3 scripts/deploy_worker.py                     # production
    python3 scripts/deploy_worker.py --preview           # preview (no marker write)
    python3 scripts/deploy_worker.py --allow-dirty       # emergency, no marker write
    python3 scripts/deploy_worker.py --skip-migration-check   # emergency override only
    python3 scripts/deploy_worker.py --skip-tests        # emergency, no marker write

AuditLab TEST-16 (2026-10-23): the Worker's vitest suite had no automated
caller -- job.py's gates and this script both skipped it, and
run_worker_tests.py had zero callers. A production deploy now runs it first
and refuses on anything but a clean or known-flaky result.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = ROOT / "worker"
MARKER = WORKER_DIR / ".last_deploy_commit"


def exe(name: str) -> str:
    """Resolve a command to a real executable path.

    Needed because this repo is developed on Windows, where `npx` is
    `npx.cmd` and `git` may be `git.exe`: subprocess.run() without
    shell=True does NOT consult PATHEXT, so a bare "npx" raises
    FileNotFoundError. That is exactly how this script failed the first
    time it was used for real (2026-07-31) -- which would have pushed
    whoever hit it straight back to running wrangler by hand, i.e. back to
    the forgotten-marker problem this script exists to remove. A deploy
    tool that only works on one platform is a deploy tool people route
    around.
    """
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"Could not find '{name}' on PATH. Is Node/git installed?")
    return found


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [exe(cmd[0]), *cmd[1:]], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )


def worker_tree_dirty() -> bool:
    r = run(["git", "status", "--porcelain", "--", "worker/"], ROOT)
    return bool(r.stdout.strip())


def unapplied_migrations() -> list[str] | None:
    """Returns the names of repo migrations not yet applied to the remote D1
    database, or None if the check itself could not run (e.g. no network,
    no D1 permission) -- distinct from an empty list (checked, genuinely
    none pending). AuditLab DEPLOY-2: `wrangler d1 migrations list --remote`
    exits 0 whether or not migrations are pending, so this parses the
    printed table rather than the exit code -- confirmed live against both
    states (a real pending migration, and a clean tree) before relying on
    it. `--json` is not supported by this wrangler subcommand, so it's a
    plain substring/regex read of the human-readable table, same
    "best-effort, honest about it" posture as this repo's other CLI-output
    parsers."""
    r = run(["npx", "wrangler", "d1", "migrations", "list", "deadlineradar", "--remote"], WORKER_DIR)
    combined = r.stdout + r.stderr
    if "No migrations to apply" in combined:
        return []
    if "Migrations to be applied" not in combined:
        # Neither expected phrase appeared -- an auth/network failure or an
        # unrecognised wrangler output format. Fail OPEN on the check
        # itself (can't prove anything either way) but say so loudly,
        # same as this file's own deploy-failure guidance below: never
        # silently assume success on an inconclusive signal.
        return None
    names = re.findall(r"\b(\d{4}_\S+?\.sql)\b", combined)
    return names


def remote_main_tip() -> str | None:
    """Tip of origin's main from `git ls-remote` (NOT the local tracking ref,
    which survives a failed fetch and so can lie). None if it can't be read."""
    r = run(["git", "ls-remote", "origin", "refs/heads/main"], ROOT)
    if r.returncode != 0:
        return None
    first = r.stdout.split()
    return first[0] if first else None


def worker_tests_status() -> int:
    """run_worker_tests.py's exit code: 0 clean or known flakes only, 1 a real
    failure, 2 vitest could not be run or its result could not be read."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_worker_tests
    return run_worker_tests.run(WORKER_DIR)


def head_commit() -> str:
    return run(["git", "rev-parse", "HEAD"], ROOT).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the Worker and keep .last_deploy_commit honest.")
    ap.add_argument("--preview", action="store_true", help="deploy to preview (marker NOT updated)")
    ap.add_argument("--allow-dirty", action="store_true", help="deploy with uncommitted worker changes (marker NOT updated)")
    ap.add_argument("--allow-non-origin-head", action="store_true", help="emergency override: deploy even if HEAD is not origin/main's tip (marker NOT updated)")
    ap.add_argument("--skip-migration-check", action="store_true", help="emergency override for the pending-migration guard")
    ap.add_argument("--skip-tests", action="store_true", help="emergency override: deploy without the worker test gate (marker NOT updated)")
    args = ap.parse_args()

    dirty = worker_tree_dirty()
    if dirty and not args.allow_dirty:
        print("REFUSING: worker/ has uncommitted changes.", file=sys.stderr)
        print("The deploy marker records a commit hash as shorthand for 'this code is deployed'.", file=sys.stderr)
        print("Deploying uncommitted code would make that claim false. Commit first,", file=sys.stderr)
        print("or use --allow-dirty (which will NOT update the marker).", file=sys.stderr)
        return 1

    # AuditLab DEPLOY-2 (2026-08-20): production only -- a preview deploy
    # doesn't touch the real database, and the emergency override exists
    # for exactly the case where this check itself can't be trusted (no
    # network, no D1 permission on the current token, wrangler output
    # format changed).
    if not args.preview and not args.skip_migration_check:
        pending = unapplied_migrations()
        if pending is None:
            print(
                "WARNING: could not determine whether all migrations are applied to the remote "
                "database (wrangler d1 migrations list --remote gave an unrecognised result -- "
                "no network, no D1 permission on this token, or its output format changed).",
                file=sys.stderr,
            )
            print(
                "Proceeding is your call, not a verified-safe one. Re-run with --skip-migration-check "
                "to acknowledge and continue, or check manually first.",
                file=sys.stderr,
            )
            return 1
        if pending:
            print("REFUSING: the remote database is missing migration(s) this deploy's code may depend on:", file=sys.stderr)
            for name in pending:
                print(f"  - {name}", file=sys.stderr)
            print(
                "\nAuditLab DEPLOY-2 (2026-08-20): shipping code before its migration is applied can fail "
                "LOUDLY (a SQL error on a live write) or SILENTLY (a SELECT * read path that degrades "
                "without crashing) -- the silent case is the dangerous one, since nothing signals it until "
                "a person notices the feature quietly stopped working. Apply the migration first "
                "(wrangler d1 migrations apply deadlineradar --remote), or if this deploy genuinely does "
                "not depend on the pending migration, re-run with --skip-migration-check.",
                file=sys.stderr,
            )
            return 1

    # AuditLab DEPLOY-15 (2026-10-06): production only. This script deploys the
    # checked-out TREE; nothing checked that the tree is what was reviewed. A
    # clone on diverged/pre-rewrite history would silently ship superseded
    # code and delete live files. Equality with origin's tip (not
    # --is-ancestor): deploying an older ancestor is a rollback, which should
    # be an explicit choice, not a silent one.
    skip_marker = False
    if not args.preview:
        tip = remote_main_tip()
        head = head_commit()
        if tip is None or tip != head:
            where = "could not read origin's main tip (git ls-remote failed)" if tip is None else f"HEAD {head[:9]} != origin/main tip {tip[:9]}"
            if not args.allow_non_origin_head:
                print(f"REFUSING: {where}.", file=sys.stderr)
                print("A production deploy ships this working tree. Deploy from a checkout whose HEAD is exactly", file=sys.stderr)
                print("origin/main (e.g. a fresh `git worktree add --detach <dir> origin/main`), or re-run with", file=sys.stderr)
                print("--allow-non-origin-head (deploys, but will NOT update the marker).", file=sys.stderr)
                return 1
            print(f"WARNING: {where}; deploying anyway (--allow-non-origin-head). Marker will NOT be updated.", file=sys.stderr)
            skip_marker = True

    # AuditLab TEST-16 (2026-10-23): production only, last gate before wrangler.
    if not args.preview:
        if args.skip_tests:
            print("WARNING: --skip-tests; deploying without the worker test gate. Marker will NOT be updated.", file=sys.stderr)
            skip_marker = True
        else:
            status = worker_tests_status()
            if status != 0:
                why = "a test failure that is not a known flake" if status == 1 else "the test run itself failed or was unreadable"
                print(f"REFUSING: worker test gate exit {status} ({why}). See run_worker_tests.py output above.", file=sys.stderr)
                print("Fix it, or re-run with --skip-tests (deploys, but will NOT update the marker).", file=sys.stderr)
                return 1

    cmd = ["npx", "wrangler", "deploy"]
    if args.preview:
        cmd += ["--config", "wrangler.preview.toml"]

    print(f"$ {' '.join(cmd)}  (cwd=worker)")
    proc = subprocess.run([exe(cmd[0]), *cmd[1:]], cwd=WORKER_DIR)
    if proc.returncode != 0:
        print("\nDeploy FAILED -- marker left unchanged.", file=sys.stderr)
        print(
            "\nIF THE ERROR MENTIONED /zones/.../workers/routes WITH 'Authentication error"
            " [code: 10000]',\n"
            "the SCRIPT almost certainly uploaded fine and only the ROUTE binding call failed.\n"
            "That happened on 2026-07-31: the API token lacks Zone > Workers Routes: Edit, but the\n"
            "routes for deadline-radar.com/api/* already existed, so nothing actually needed\n"
            "changing and the new code went live anyway.\n"
            "\n"
            "DO NOT assume either outcome. Fetch the deployed bundle and grep it -- that is the\n"
            "only ground truth (see HANDOFF's verification traps):\n"
            "  curl -H \"Authorization: Bearer $CLOUDFLARE_API_TOKEN\" \\\n"
            "    https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/scripts/deadlineradar-api\n"
            "If your change IS in that bundle, the deploy landed: write HEAD into\n"
            "worker/.last_deploy_commit by hand, or the marker silently lies in the other\n"
            "direction (code deployed, marker stale) -- the exact drift this script exists to stop.",
            file=sys.stderr,
        )
        return proc.returncode

    if args.preview:
        print("\nPreview deploy done. Marker deliberately NOT updated (it tracks production).")
        return 0
    if dirty:
        print("\nDeploy done, but marker NOT updated because the tree was dirty (--allow-dirty).")
        return 0
    if skip_marker:
        print("\nDeploy done, but marker NOT updated because HEAD is not origin/main's tip (--allow-non-origin-head).")
        return 0

    commit = head_commit()
    # newline="\n": Path.write_text()'s default newline translation writes
    # \r\n on Windows, which silently reintroduced CRLF into this LF-only
    # repo (.gitattributes: `* text=auto eol=lf`) every time this script ran
    # on a Windows machine -- caught the same day it first happened, already
    # live on origin/main once as of this fix.
    MARKER.write_text(commit + "\n", encoding="utf-8", newline="\n")
    print(f"\nDeploy done. Updated worker/.last_deploy_commit -> {commit[:7]}")
    print("Remember to COMMIT the marker change so the repo reflects reality.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
