#!/usr/bin/env python3
"""
Advisory-only detector for the "static site and Worker deploy through separate
pipelines" prevention-register class (instance: 2026-07-09, South Dakota/Hawaii/
Oklahoma silently rejected real signups because the deployed Worker's bundled
cpa_deadlines.json predated their addition, even though the GitHub-Pages-deployed
static site already showed those states' pages correctly).

The static site (docs/) redeploys automatically on every push via GitHub Pages.
The Worker does NOT -- it only picks up worker/src/*.ts (and its bundled JSON
data) when someone explicitly runs `wrangler deploy`. This script does not
live-probe the deployed Worker (that would burn the real per-IP rate limit
budget); it compares local git history instead: has ANYTHING under worker/src/
changed since the commit recorded in worker/.last_deploy_commit?

AuditLab BILL-7 (2026-08-09, restated with a live counterexample 2026-08-13):
the original version of this script compared only worker/src/cpa_deadlines.json
against the deploy marker, then printed a claim about "the Worker bundle" --
the whole directory, not the one file it actually checked. Demonstrated false-
PASS window: between two real commits today, deadline.ts/emails.ts/index.ts/
store.ts were all undeployed (including the staleness guard itself) while the
old check would have reported "Worker bundle should be current", because none
of those changes touched cpa_deadlines.json specifically. Now scoped to the
whole worker/src/ tree, so the claim matches what's actually checked.

DRIFT-1 (SecurityLab MEDIUM, 2026-10-06): both inputs used to resolve from the
LOCAL checkout (marker from the working tree, last worker/src commit from local
HEAD). On a diverged checkout (b3_saas/deadlineradar was 1688 commits off
origin/main) that printed 9 phantom undeployed files -- PR6 billing code that is
not on origin/main -- and told the operator to `wrangler deploy`, while the true
answer was 1 file; it could equally return a silent PASS. Both inputs now come
from REF (origin/main, fetched first), and the PASS/ADVISORY text names the ref
measured so a wrong-line run is visible instead of silent. Never reads the
working tree or HEAD.

Advisory only, same treatment as every other detector in this project: it flags
a candidate for a human to check, it does not gate a build or a push. Update
worker/.last_deploy_commit's contents after every real `wrangler deploy`.

Usage:
    python scripts/worker_deploy_staleness_check.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAST_DEPLOY_PATH = "worker/.last_deploy_commit"
WORKER_SRC_DIR = "worker/src"
REF = "origin/main"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def check(root: Path = ROOT, ref: str = REF, fetch: bool = True) -> str:
    """Return the advisory text, measured entirely against `ref` (never HEAD or
    the working tree)."""
    fetch_note = ""
    if fetch:
        remote, _, branch = ref.partition("/")
        f = _git(root, "fetch", "--quiet", remote, branch)
        if f.returncode != 0:
            fetch_note = f" (fetch failed -- measured against the LAST-FETCHED {ref}, may be behind)"

    # MON-19 (AuditLab): resolve the ref's tip ONCE and read everything below from
    # that SHA, so the label is a reading of what was measured -- not an f-string
    # over the `ref` parameter, which would keep claiming "origin/main" after a
    # wrong-ref regression.
    t = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    tip = t.stdout.strip() if t.returncode == 0 else ""
    if not tip:
        return f"ADVISORY: cannot resolve {ref}{fetch_note} -- cannot check staleness."

    m = _git(root, "show", f"{tip}:{LAST_DEPLOY_PATH}")
    if m.returncode != 0 or not m.stdout.strip():
        return (
            f"ADVISORY: {LAST_DEPLOY_PATH} not readable at {ref} {tip[:7]}{fetch_note} -- cannot check "
            f"staleness. Create it with the commit hash of the last real `wrangler deploy`."
        )
    last_deploy_commit = m.stdout.strip()

    l = _git(root, "log", "--format=%H", "-1", tip, "--", WORKER_SRC_DIR)
    last_src_commit = l.stdout.strip() if l.returncode == 0 else ""
    if not last_src_commit:
        return f"ADVISORY: could not find any commit touching {WORKER_SRC_DIR}/ at {ref} {tip[:7]}{fetch_note}."

    # Is last_src_commit an ancestor of (or equal to) the marker commit? If so,
    # nothing under worker/src/ changed since the deploy the marker records.
    anc = _git(root, "merge-base", "--is-ancestor", last_src_commit, last_deploy_commit)
    if anc.returncode == 0:
        return (
            f"PASS [measured at {ref} tip {tip[:7]}, last worker/src commit {last_src_commit[:7]}{fetch_note}] -- no file under "
            f"{WORKER_SRC_DIR}/ has changed since the last recorded deploy "
            f"({last_deploy_commit[:7]}). Worker bundle should be current."
        )

    undeployed = _git(root, "log", "--format=%h %s", f"{last_deploy_commit}..{tip}", "--", WORKER_SRC_DIR).stdout.strip()
    undeployed_lines = undeployed.splitlines() if undeployed else []
    files = _git(root, "diff", "--name-only", last_deploy_commit, tip, "--", WORKER_SRC_DIR).stdout.strip()
    files_lines = files.splitlines() if files else []
    out = [
        f"ADVISORY [measured at {ref} tip {tip[:7]}, last worker/src commit {last_src_commit[:7]}{fetch_note}]: {WORKER_SRC_DIR}/ changed "
        f"AFTER the last recorded deploy ({last_deploy_commit[:7]}) -- the live Worker may be "
        f"running stale code or stale bundled data (this is the exact class that broke South "
        f"Dakota/Hawaii/Oklahoma signups on 2026-07-09). Deploy ONLY from a fresh "
        f"`git worktree add --detach <dir> {ref}` (scripts/deploy_worker.py enforces HEAD == "
        f"{ref} tip), then push the updated worker/.last_deploy_commit.",
        f"  Undeployed commits touching {WORKER_SRC_DIR}/ ({len(undeployed_lines)}):",
    ]
    out += [f"    {line}" for line in undeployed_lines]
    out.append(f"  Files changed: {', '.join(files_lines) if files_lines else '(none found)'}")
    return chr(10).join(out)


def main() -> None:
    print(check())
    sys.exit(0)


if __name__ == "__main__":
    main()
