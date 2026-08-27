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
Exit 2: vitest itself could not be run, its JSON output could not be read, it
        collected suspiciously few tests, or it exited non-zero with no
        parsed failures to explain why.
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
# "UNEXPECTED" because fullName joins with a plain space instead).
#
# AuditLab TEST-9 (2026-08-27): this list originally also carried
# "sign-out-other-devices ... PER SESSION" and demo-login's "MANY DIFFERENT
# IPs" cap test -- both real sequential-HTTP+D1 tests, but both still on
# vitest's 5s default timeout, never given the explicit timeout the CPE
# tests below already have for the identical reason. Pinning them as
# "known-flaky" would have permanently silenced a real future regression in
# either (a genuine intermittent defect would ALSO read as "a recognized
# flake"). Gave both an explicit 20s timeout instead (see their own inline
# comments in worker.spec.ts / demo-login.spec.ts) and removed them here --
# the repo's own established remedy, not accepted noise. Only re-pin either
# if it still flakes at 20s, which would then be real evidence.
#
# The two CPE tests below are the only entries that have actually earned
# their place here: each already carries an explicit 20s/30s timeout (10x
# and 100x the work of the two removed above) and still flakes under
# full-suite load -- their load-sensitivity is established, not assumed.
KNOWN_FLAKY_TESTS = {
    "GET/POST/DELETE /firm/cpe -- CPE-hours entry CRUD blocks the 101st CPE entry "
    "from the same firm within the daily window (own rate-limit bucket)",
    "GET/POST/DELETE /firm/cpe -- CPE-hours entry CRUD blocks the 101st CPE-entry "
    "DELETE from the same firm within the daily window",
}

# AuditLab TEST-7 (2026-08-27): without a floor, a report that collected ZERO
# tests (a config error, a bad glob, a crash after the report file was
# opened) has `numTotalTests: 0` and an empty `testResults` -- `if not
# failed` reads that as a clean PASS. That is the loudest possible "something
# new" and the one case this script must not wave through. Set well below
# the real suite size (~2,300 as of 2026-08-27) so ordinary growth/removal of
# a handful of tests never trips it, but a near-total collection failure
# always does. Bump this if the suite's real size ever drops near it for a
# legitimate reason (a large deliberate test removal).
MIN_EXPECTED_TESTS = 1000


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

    all_assertions = [a for suite in result.get("testResults", []) for a in suite.get("assertionResults", [])]
    failed = [a for a in all_assertions if a.get("status") not in ("passed", "pending", "skipped", "todo")]

    total = result.get("numTotalTests", 0)
    print(f"vitest: {result.get('numPassedTests', '?')}/{total or '?'} passed, {len(failed)} failed")

    # TEST-7: a suspiciously small total is the loudest possible "something
    # new" and must never read as a clean pass, regardless of `failed`.
    if total < MIN_EXPECTED_TESTS:
        print(
            f"\nREFUSING: only {total} total tests collected, below the expected floor of "
            f"{MIN_EXPECTED_TESTS} -- this looks like a broken test collection (config error, bad "
            f"glob, crash after the report opened), not a clean run. Investigate before trusting "
            f"any pass/fail result from this invocation."
        )
        return 2
    # A non-zero vitest exit with nothing parsed as a failure means something
    # went wrong outside the normal per-test failure path (e.g. a setup/
    # teardown crash) -- don't let that read as success either.
    if proc.returncode != 0 and not failed:
        print(
            f"\nREFUSING: vitest exited {proc.returncode} but the JSON report shows no failed "
            f"tests -- something went wrong outside a normal test failure. vitest stderr tail:"
        )
        print("\n".join(proc.stderr.splitlines()[-40:]))
        return 2

    # TEST-8: a pinned name that matched nothing in this run's full test list
    # (not just "didn't fail" -- a flaky test legitimately passes most runs)
    # means the entry has decayed -- the test was renamed, moved, or deleted
    # and the list stopped being a true record. Advisory only: doesn't affect
    # the exit code, since a rename is a normal refactor, not a build defect.
    seen_names = {a.get("fullName") for a in all_assertions}
    decayed = sorted(KNOWN_FLAKY_TESTS - seen_names)
    if decayed:
        print(f"\nADVISORY: {len(decayed)} KNOWN_FLAKY_TESTS entry(ies) matched no test in this run "
              f"(renamed/moved/deleted?) -- update the pinned list so it stays a true record:")
        for name in decayed:
            print(f"  - {name}")

    if not failed:
        print("\nPASS -- no failures.")
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
