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

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

REPO = os.environ.get("REVERIFY_REPO", r"C:\Users\Devin\AssetLab\b3_saas\deadlineradar")
JOB_DIR = os.environ.get("REVERIFY_JOB_DIR", r"C:\Users\Devin\AssetLab\dr_reverify")   # short path (MAX_PATH)
STATE_DIR = os.environ.get("REVERIFY_STATE_DIR", r"C:\Users\Devin\Orchestrator\state")
LOCK = os.path.join(STATE_DIR, "reverify.lock")
STATUS = os.path.join(STATE_DIR, "reverify_status.json")
PENDING = os.path.join(STATE_DIR, "reverify_pending")
DATE_FIELD = {"cpa_deadlines": "last_verified", "cpe_hours": "verified_date",
              "reinstatement": "last_verified", "renewal_fees": "verified_date"}
LOG = os.path.join(STATE_DIR, "reverify_job.log")
ASSETLAB_INBOX = os.environ.get("REVERIFY_ASSETLAB_INBOX", r"C:\Users\Devin\AssetLab\inbox")
DATA_FILES = ["data/cpa_deadlines.json", "data/cpe_hours.json", "data/reinstatement.json", "data/renewal_fees.json"]
WORKER_COPIES = {f: "worker/src/" + os.path.basename(f) for f in DATA_FILES}


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(*cmd, cwd=None, check=True, env=None):
    r = subprocess.run(cmd, cwd=cwd or JOB_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
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
    # generate.py refuses to build without terser (AuditLab LEAK-1); node_modules is untracked, so a
    # fresh job worktree needs one install. Survives later resets (clean only touches data/ and docs/).
    js = os.path.join(JOB_DIR, "scripts", "js_tools")
    if not os.path.exists(os.path.join(js, "node_modules", "terser", "bin", "terser")):
        npm = shutil.which("npm") or "npm"
        sh(npm, "ci", "--no-audit", "--no-fund", cwd=js)
        log("installed scripts/js_tools node_modules (npm ci)")


def _publish_status(deployed: bool, reason: str = ""):
    """Copy the runner's pending status to the real one the watchdog reads. If the run did NOT deploy,
    verified_dates are rebuilt from what is actually on origin/main, so the watchdog never sees a
    verification that isn't live (a reverted or unpushed bump must not read as 'fresh')."""
    src = os.path.join(PENDING, "reverify_status.json")
    if not os.path.exists(src):
        return
    with open(src, encoding="utf-8") as f:
        st = json.load(f)
    st["deployed"] = deployed
    if not deployed:
        st["deploy_blocked_reason"] = reason
        live = {}
        for ds_file in DATA_FILES:
            ds = os.path.basename(ds_file)[:-5]
            recs = json.loads(sh("git", "show", f"origin/main:{ds_file}").stdout)["records"]
            for r in recs:
                live[r["id"]] = str(r.get(DATE_FIELD[ds]) or "")
        st["verified_dates"] = {i: live.get(i, "") for i in st.get("verified_dates", {})}
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATUS)
    log(f"status published (deployed={deployed}{', ' + reason if reason else ''})")


def run(mode, push=True):
    me = os.path.abspath(__file__)
    with open(me, "rb") as f:
        before = f.read()
    ensure_worktree()
    py = sys.executable
    # The scheduled task runs THIS file from the job worktree, which ensure_worktree() just reset to
    # origin/main. If main changed job.py, the code in memory is stale: re-launch the fresh copy once so
    # the job logic always matches the runner it drives (REVERIFY_REEXEC stops a loop).
    if me.lower().startswith(os.path.abspath(JOB_DIR).lower()) and not os.environ.get("REVERIFY_REEXEC"):
        with open(me, "rb") as f:
            if f.read() != before:
                log("job.py changed on origin/main; re-launching the updated copy")
                os.environ["REVERIFY_REEXEC"] = "1"
                try:
                    os.remove(LOCK)               # the fresh process takes the lock itself
                except OSError:
                    pass
                return subprocess.run([py, me, *sys.argv[1:]], env=os.environ).returncode
    # the runner writes to a PENDING state dir; the real status is only updated by _publish_status
    os.makedirs(PENDING, exist_ok=True)
    if os.path.exists(STATUS):
        shutil.copyfile(STATUS, os.path.join(PENDING, "reverify_status.json"))
    env = dict(os.environ, REVERIFY_STATE_DIR=PENDING)
    # --all: check every record, not only the >20-day ones (first live run, Orchestrator 10-02 13:08)
    results = os.path.join(PENDING, "results.json")
    base = sh("git", "rev-parse", "HEAD").stdout.strip()     # the commit this fetch runs against (REPLAY-1)
    r = sh(py, "scripts/reverify/runner.py", "--apply", *(["--all"] if "--all" in sys.argv else []),
           "--out", results, check=False, env=env)
    log(f"runner rc={r.returncode}: {r.stdout.strip()[-300:]}")
    if r.returncode != 0:
        note("RUNNER_FAILED", r.stdout[-2000:] + r.stderr[-2000:])
        return 1
    if not sh("git", "status", "--porcelain", "--", *DATA_FILES).stdout.strip():
        log("no data changes (nothing newly CONFIRMED); nothing to deploy")
        if push:
            _publish_status(True)
        return 0
    # A fetch run takes ~8 minutes, so a busy main often moves before the push. On a rejected push we
    # reset to the new origin/main, REPLAY the saved CONFIRMED results (no re-fetch; records main changed
    # meanwhile are skipped), and re-run every gate on exactly what will be pushed. Up to 3 attempts.
    for attempt in range(1, 4):
        if attempt > 1:
            sh("git", "fetch", "origin", "main")
            sh("git", "checkout", "--detach", "--force", "origin/main")
            sh("git", "clean", "-fd", "data", "docs")
            rp = sh(py, "scripts/reverify/runner.py", "--replay", results, "--base", base, check=False, env=env)
            log(f"attempt {attempt}: replay onto {sh('git', 'rev-parse', '--short', 'HEAD').stdout.strip()}: {rp.stdout.strip()}")
            if rp.returncode != 0:
                note("REPLAY_FAILED", rp.stdout[-2000:] + rp.stderr[-2000:])
                break
        rc = _gate_commit(mode, py, push)
        if rc != "pushed?":
            return rc
        p = sh("git", "push", "origin", "HEAD:main", check=False)
        if p.returncode == 0:
            sha = sh("git", "rev-parse", "--short", "HEAD").stdout.strip()
            log(f"pushed {sha} to main (attempt {attempt})")
            note("WORKER_DEPLOY_NEEDED", f"reverify pushed {sha}: worker/src/*.json data copies changed (verified dates). "
                                         f"The Worker is not auto-deployed by the unattended job; please run "
                                         f"scripts/deploy_worker.py when convenient so the API's data_as_of catches up.")
            sh("git", "fetch", "origin", "main")
            _publish_status(True)
            return 0
        log(f"attempt {attempt}: push rejected ({p.stderr.strip().splitlines()[-1] if p.stderr.strip() else 'no stderr'})")
    note("PUSH_FAILED", f"push to main failed after replay retries; nothing deployed.\n{p.stderr[-1500:]}")
    _publish_status(False, "push failed")
    return 3


def _gate_commit(mode, py, push):
    """Mirror worker copies, run all gates, commit. Returns an exit code, or "pushed?" when ready to push."""
    changed = sh("git", "status", "--porcelain", "--", *DATA_FILES).stdout.strip()
    if not changed:
        log("nothing left to commit after replay (main already carries these dates)")
        if push:
            _publish_status(True)
        return 0
    # preship [C]: data/*.json must be byte-identical to the Worker's bundled copies in worker/src/.
    # Mirror the exact bytes and commit both together. The Worker itself is NOT deployed by this
    # unattended job (AuditLab 2026-10-02): an alert asks AssetLab to run deploy_worker.py.
    for f in DATA_FILES:
        shutil.copyfile(os.path.join(JOB_DIR, f), os.path.join(JOB_DIR, WORKER_COPIES[f]))
    for gate in ([py, "-m", "pytest", "scripts/reverify", "-q"], [py, "generate.py"], [py, "scripts/preship_gate.py"]):
        g = sh(*gate, check=False)
        if g.returncode != 0:
            # head carries the FAIL/violation lines; tail carries the traceback for crashes -- keep both
            out = g.stdout if len(g.stdout) <= 6000 else g.stdout[:4000] + "\n...[snip]...\n" + g.stdout[-1500:]
            note("GATE_FAILED_no_deploy", f"gate `{' '.join(gate[1:])}` failed (rc={g.returncode}); nothing committed or pushed.\n"
                                         f"```\n{out}\n{g.stderr[-1500:]}\n```")
            sh("git", "checkout", "--force", "--", ".")
            if push:
                _publish_status(False, f"gate failed: {' '.join(gate[1:])}")
            return 2
        log(f"gate ok: {' '.join(gate[1:])}")
    sh("git", "add", "--", *DATA_FILES, *WORKER_COPIES.values(), "docs")
    n = sum(1 for ln in changed.splitlines())
    sh("git", "-c", "user.name=reverify-bot", "-c", "user.email=raven@mooseandraven.com", "commit", "-q", "-m",
       f"reverify: automated {mode} re-verification {datetime.now():%Y-%m-%d} ({n} data file(s) updated)")
    # derived artifacts the gates rewrite but we don't commit (e.g. data/gap_list.json) must not block a reset
    sh("git", "checkout", "--force", "--", ".")
    if not push:
        log("--no-push: committed in job worktree only")
        return 0
    return "pushed?"


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
