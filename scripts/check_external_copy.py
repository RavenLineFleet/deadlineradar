#!/usr/bin/env python3
"""External-copy brand/style gate for DeadlineRadar.

Standing rule: the brand name is always "Deadline-Radar" (hyphenated) in
copy that leaves this repo -- outreach emails, directory-listing
submissions, social posts, support replies. The site's own generated HTML
already enforces this correctly at build time; this script exists because
that build-time enforcement does NOT cover ad-hoc external copy drafted by
hand outside the docs/ build (e.g. `outreach_drafts/*.md`).

2026-09-08 incident: Orchestrator reviewed 6 board-outreach email drafts
for accuracy/tone/citations and approved them, but missed that every one
said "DeadlineRadar" (no hyphen) twice -- a rule already known and
documented, just not mechanically checked during that review. Devin caught
it. A "remember to check" fix failed once already; this script is the
actual fix -- a hard, scriptable gate instead of a mental checklist item,
following the same fleet-wide prevention-loop pattern used for every other
recurring class of miss (fix -> propagate -> detector -> verify -> CLOSED).

Usage:
    python3 scripts/check_external_copy.py <file-or-dir> [<file-or-dir> ...]

Exits 1 and prints every violating line if the un-hyphenated form is found
anywhere in the given file(s)/directory tree (recursive, text files only).
Exits 0 (silently) if clean. Run this on any external-copy draft -- and
get a clean PASS from it -- before filing it for review or sending it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Matches "DeadlineRadar" as a whole word, case-sensitive, but NOT the
# correctly-hyphenated "Deadline-Radar" and NOT the lowercase URL/slug
# form "deadline-radar" (e.g. deadline-radar.com, deadlineradar.json is
# fine as a filename/technical-identifier, only prose brand mentions
# matter here).
VIOLATION_RE = re.compile(r"\bDeadlineRadar\b")

TEXT_SUFFIXES = {".md", ".txt", ".html", ".eml"}


def find_violations_in_text(text: str) -> list[tuple[int, str]]:
    """Shared core: scan a raw string, return (lineno, line) for every violating line.

    Both the file/dir CLI path (find_violations) and any programmatic caller that has
    content in memory before it's written to a file or sent (e.g. an outreach-email
    send path -- see send_outreach_email.py) go through this one function, so there is
    exactly one place the brand-name rule is encoded."""
    return [
        (lineno, line.strip())
        for lineno, line in enumerate(text.splitlines(), start=1)
        if VIOLATION_RE.search(line)
    ]


def find_violations(paths: list[str]) -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(f for f in p.rglob("*") if f.is_file() and f.suffix in TEXT_SUFFIXES)
        elif p.is_file():
            files.append(p)
        else:
            print(f"WARNING: path not found, skipping: {p}", file=sys.stderr)

    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        violations.extend((f, lineno, line) for lineno, line in find_violations_in_text(text))
    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    violations = find_violations(sys.argv[1:])
    if not violations:
        print("PASS: no un-hyphenated \"DeadlineRadar\" found.")
        return 0

    print(f"FAIL: {len(violations)} un-hyphenated \"DeadlineRadar\" instance(s) found "
          "-- must read \"Deadline-Radar\":")
    for f, lineno, line in violations:
        print(f"  {f}:{lineno}: {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
