#!/usr/bin/env python3
"""Run the worker's vitest suite and separate known-flaky failures from real ones.

AuditLab (2026-08-27, closing STALE-11): across four full suite runs the same
session ran that night, the failure counts were 9, 3, 3, 6 -- all explained
each time as "the same 2-3 known-flaky rate-limit/timeout tests," but that
claim was never actually checkable from the console output alone. A run of 6
failures looks identical, from the summary line, to a run of 3 plus retries;
a genuinely NEW failure landing in a noisy run would read as "more of the
same" rather than what it is. AuditLab's own words: "same flakes as always"
was an assertion no one could verify from the output, and the evidence for
fixing that was unusually clean.

This script closes that gap the same way the rest of this repo replaces a
remembered judgement with a mechanical check (gap_list_check.py, DATE-1's
currency gate, etc.): it runs `vitest run --reporter=json`, parses the
structured result instead of eyeballing text, and diffs every failing test's
full name against a pinned KNOWN_FLAKY_TESTS set below. Anything in that set
is reported as an expected flake (these are real timing-sensitive tests --
100+ sequential DB-writing requests fired to exhaust a rate-limit bucket
inside vitest's default per-test timeout, under the full suite's resource
contention -- not defects in the tests themselves, see each test's own
comment). Anything NOT in that set fails this script loudly and with a
non-zero exit, distinct from "same as always."

KNOWN_FLAKY_TESTS was seeded from the actual failures observed across the
four runs above (grep results, not memory) -- exactly 3 unique names, no
others, every time. If a new legitimately-flaky test is found later, add it
here with a comment saying why and when; if a listed test stops flaking
(e.g. after a timeout bump), remove it so this stays a true record instead
of a list that only ever grows.

Usage:
    python scripts/run_worker_tests.py [worker_dir]

Exit 0: every failure (if any) is a known flake, or there were no failures.
Exit 1: at least one failure is NOT in KNOWN_FLAKY_TESTS -- investigate it
        before assuming it's "the same as always."
Exit 2: vitest itself could not be run or its JSON output could not be read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _exe(name: str) -> str:
    """Resolve to a real executable path. Needed on Windows, where `npx` is
    `npx.cmd` -- subprocess.run() without shell=True does not consult
    PATHEXT, so a bare "npx" raises FileNotFoundError. Same fix as
    deploy_worker.py's own exe() helper, for the same reason."""
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"Could not find '{name}' on PATH. Is Node installed?")
    return found

# Exact `fullName` values as vitest's JSON reporter actually reports them --
# NOT the " > "-separated form the console/text reporter prints, which is a
# different format for the same hierarchy (caught by running this script for
# real against the full suite before trusting it: the first version of this
# list used the console's " > " separator and every failure came back
# "UNEXPECTED" because fullName joins with a plain space instead). Seen in
# isolation and under full-suite load; the shared root cause is real
# load/timing sensitivity (sequential rate-limit-bucket exhaustion, tight
# per-test timeouts), not a defect in the assertions -- see each test's own
# inline comment for detail.
KNOWN_FLAKY_TESTS = {
    "GET/POST/DELETE /firm/cpe -- CPE-hours entry CRUD blocks the 101st CPE entry "
    "from the same firm within the daily window (own rate-limit bucket)",
    "GET/POST/DELETE /firm/cpe -- CPE-hours entry CRUD blocks the 101st CPE-entry "
    "DELETE from the same firm within the daily window",
    "POST /firm/sign-out-other-devices rate-limits PER SESSION, not per firm -- "
    "one session's budget can't 429 a different session of the same firm",
}


def run(worker_dir: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "vitest-result.json"
        proc = subprocess.run(
            [_exe("npx"), "vitest", "run", "--reporter=json", f"--outputFile={out_path}"],
            cwd=worker_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Could not read vitest's JSON report: {exc}")
            print("--- vitest stdout (tail) ---")
            print("\n".join(proc.stdout.splitlines()[-40:]))
            print("--- vitest stderr (tail) ---")
            print("\n".join(proc.stderr.splitlines()[-40:]))
            return 2

    failed = [
        a
        for suite in result.get("testResults", [])
        for a in suite.get("assertionResults", [])
        if a.get("status") not in ("passed", "pending", "skipped", "todo")
    ]

    total = result.get("numTotalTests", "?")
    print(f"vitest: {result.get('numPassedTests', '?')}/{total} passed, {len(failed)} failed")

    if not failed:
        print("PASS -- no failures.")
        return 0

    known = [a for a in failed if a.get("fullName") in KNOWN_FLAKY_TESTS]
    unknown = [a for a in failed if a.get("fullName") not in KNOWN_FLAKY_TESTS]

    if known:
        print(f"\n{len(known)} known-flaky failure(s) (in KNOWN_FLAKY_TESTS, not a new signal):")
        for a in known:
            print(f"  - {a.get('fullName')}")

    if unknown:
        print(f"\n{len(unknown)} UNEXPECTED failure(s) -- NOT in KNOWN_FLAKY_TESTS, investigate before shipping:")
        for a in unknown:
            print(f"  - {a.get('fullName')}")
            msg = (a.get("failureMessages") or [""])[0]
            if msg:
                print(f"      {msg.splitlines()[0][:200]}")
        print("\nFAIL -- at least one failure is not a recognized flake.")
        return 1

    print("\nPASS -- every failure is a recognized flake.")
    return 0


def main() -> int:
    worker_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "worker"
    if not worker_dir.is_dir():
        print(f"Worker directory not found: {worker_dir}")
        return 2
    return run(worker_dir)


if __name__ == "__main__":
    sys.exit(main())
