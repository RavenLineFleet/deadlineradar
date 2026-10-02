"""Scheduled wrapper: daily rolling re-verification -> gates -> commit + push (= deploy via GitHub Pages).

    python scripts/reverify/job.py daily             # Task Scheduler: every day 02:00
    python scripts/reverify/job.py daily --no-push   # everything except the push (rehearsal)

Daily rolling design (Orchestrator 2026-10-02 12:43, AuditLab STALE-23): each run re-verifies only
records whose verified date is > 20 days old plus pending FAILED retries, so there are no monthly cliffs.

Runs in its OWN worktree (JOB_DIR), reset to origin/main every run, so it never touches anyone's
working copy. Order: fetch -> reset -> runner --apply -> if data changed: reverify
tests + generate.py + preship_gate.py -> commit -> push to main. Any failing gate = no commit, no
push, and a note in the AssetLab inbox. No LLM, so not throttle-gated. A lock file stops overlap.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

REPO = os.environ.get("REVERIFY_REPO", r"C:\Users\Devin\AssetLab\b3_saas\deadlineradar")
JOB_DIR = os.environ.get("REVERIFY_JOB_DIR", r"C:\Users\Devin\AssetLab\dr_reverify")   # short path (MAX_PATH)
STATE_DIR = os.environ.get("REVERIFY_STATE_DIR", r"C:\Users\Devin\Orchestrator\state")
LOCK = os.path.join(STATE_DIR, "reverify.lock")
LOG = os.path.join(STATE_DIR, "reverify_job.log")
ASSETLAB_INBOX = os.environ.get("REVERIFY_ASSETLAB_INBOX", r"C:\Users\Devin\AssetLab\inbox")
DATA_FILES = ["data/cpa_deadlines.json", "data/cpe_hours.json", "data/reinstatement.json", "data/renewal_fees.json"]


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(*cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd or JOB_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r


def note(title, body):
    os.makedirs(ASSETLAB_INBOX, exist_ok=True)
    p = os.path.join(ASSETLAB_INBOX, f"reverify_{datetime.now():%Y%m%d_%H%M%S}_{title}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"---\nfrom: reverify-job\nkind: alert\nneeds_devin: no\nsummary: {title}\n---\n{body}\n")
    log(f"filed {p}")


def ensure_worktree():
    if not os.path.isdir(os.path.join(JOB_DIR, ".git")) and not os.path.isfile(os.path.join(JOB_DIR, ".git")):
        sh("git", "fetch", "origin", "main", cwd=REPO)
        sh("git", "worktree", "add", "--detach", JOB_DIR, "origin/main", cwd=REPO)
    sh("git", "fetch", "origin", "main")
    sh("git", "checkout", "--detach", "--force", "origin/main")
    sh("git", "clean", "-fd", "data", "docs")


def run(mode, push=True):
    ensure_worktree()
    py = sys.executable
    # --all: check every record, not only the >20-day ones (first live run, Orchestrator 10-02 13:08)
    r = sh(py, "scripts/reverify/runner.py", "--apply", *(["--all"] if "--all" in sys.argv else []), check=False)
    log(f"runner rc={r.returncode}: {r.stdout.strip()[-300:]}")
    if r.returncode != 0:
        note("RUNNER_FAILED", r.stdout[-2000:] + r.stderr[-2000:])
        return 1
    changed = sh("git", "status", "--porcelain", "--", *DATA_FILES).stdout.strip()
    if not changed:
        log("no data changes (nothing newly CONFIRMED); nothing to deploy")
        return 0
    for gate in ([py, "-m", "pytest", "scripts/reverify", "-q"], [py, "generate.py"], [py, "scripts/preship_gate.py"]):
        g = sh(*gate, check=False)
        if g.returncode != 0:
            note("GATE_FAILED_no_deploy", f"gate `{' '.join(gate[1:])}` failed (rc={g.returncode}); nothing committed or pushed.\n"
                                         f"```\n{g.stdout[-2500:]}\n{g.stderr[-1500:]}\n```")
            sh("git", "checkout", "--force", "--", ".")
            return 2
        log(f"gate ok: {' '.join(gate[1:])}")
    sh("git", "add", "--", *DATA_FILES, "docs")
    n = sum(1 for ln in changed.splitlines())
    sh("git", "-c", "user.name=reverify-bot", "-c", "user.email=raven@mooseandraven.com", "commit", "-q", "-m",
       f"reverify: automated {mode} re-verification {datetime.now():%Y-%m-%d} ({n} data file(s) updated)")
    if not push:
        log("--no-push: committed in job worktree only")
        return 0
    for attempt in range(2):
        p = sh("git", "push", "origin", "HEAD:main", check=False)
        if p.returncode == 0:
            log(f"pushed {sh('git', 'rev-parse', '--short', 'HEAD').stdout.strip()} to main")
            return 0
        sh("git", "fetch", "origin", "main")
        rb = sh("git", "rebase", "origin/main", check=False)   # main moved under us: replay our data-only commit once
        if rb.returncode != 0:
            sh("git", "rebase", "--abort", check=False)
            break
    note("PUSH_FAILED", f"commit made in {JOB_DIR} but push to main failed; nothing deployed.\n{p.stderr[-1500:]}")
    return 3


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    push = "--no-push" not in sys.argv
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - os.path.getmtime(LOCK) < 6 * 3600:
            log("another reverify job holds the lock; exiting")
            return 4
        os.utime(LOCK, None)
    try:
        log(f"start {mode} push={push}")
        return run(mode, push)
    except Exception as e:  # noqa: BLE001 - any crash must become a visible alert, not a silent miss
        note("JOB_CRASHED", repr(e)[:3000])
        return 5
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
