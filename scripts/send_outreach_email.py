#!/usr/bin/env python3
"""The one real choke point for sending Deadline-Radar external outreach email.

2026-09-08 incident + follow-up (AuditLab LOW, Orchestrator directive): the hyphen brand
rule ("Deadline-Radar", never "DeadlineRadar") already had a detector
(check_external_copy.py), but nothing called it -- a human had to remember to run it
before sending, the exact failure mode that let 6 drafts through review un-hyphenated.
This script is the actual fix: the send path itself refuses to call SendGrid if the
subject/body fails the check. There is no way to send an outreach email through this
script with a violation in it -- not "you should check first," structurally can't.

If more brand/style rules for external copy come up later, extend
check_external_copy.py's VIOLATION_RE / find_violations_in_text() rather than adding a
second check here -- this script only calls that one detector.

Draft file format (see outreach_drafts/*.md for real examples):
    **Recipient**: <email> (<free-text about how the address was verified>)
    **From**: <email>
    **Subject**: <subject line>

    ---

    <body text -- markdown **bold** is stripped before sending, since these are
    plain-text emails>

Usage:
    python3 scripts/send_outreach_email.py <draft-file.md> [<draft-file.md> ...]
    python3 scripts/send_outreach_email.py <draft-file.md> --check-only   # gate only, no send
    python3 scripts/send_outreach_email.py <draft-file.md> --dry-run      # DryRunSender, no real send

Exit 0 if every file gate-passed and (unless --check-only) sent successfully. Exit 1 if
any file fails the gate (nothing in that file is sent) or any send fails.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.check_external_copy import find_violations_in_text  # noqa: E402
from reminders.sender import DryRunSender, SendGridSender  # noqa: E402

# SEC-5 (AuditLab/SecurityLab, 2026-09-09): must live two dirs up, out of the
# repo entirely -- REPO_ROOT is public (RavenLineFleet/deadlineradar) and
# unignored for a repo-root .sendgrid_key. Matches reminders/run_live_selftest.py's
# own KEY_PATH; do not drop the .parent.parent again.
KEY_PATH = REPO_ROOT.parent.parent / ".sendgrid_key"
FROM_EMAIL = "support@deadline-radar.com"
FROM_NAME = "Deadline-Radar"

_RECIPIENT_RE = re.compile(r"^\*\*Recipient\*\*:\s*(\S+)", re.MULTILINE)
_SUBJECT_RE = re.compile(r"^\*\*Subject\*\*:\s*(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


class DraftError(Exception):
    """A draft file is malformed (missing recipient/subject/body) -- not a brand-gate
    failure, a structural one. Distinguished from a gate failure in the CLI's exit
    reporting so 'this file is broken' and 'this file violates the brand rule' don't
    look the same in the output."""


def parse_draft(path: Path) -> tuple[str, str, str]:
    """Returns (to_email, subject, body). Body has markdown bold stripped and is
    dedented to natural paragraph flow (the source file's hard line-wraps are for the
    .md file's own readability, not semantic -- an email client wraps its own lines)."""
    text = path.read_text(encoding="utf-8")

    rec_match = _RECIPIENT_RE.search(text)
    subj_match = _SUBJECT_RE.search(text)
    if not rec_match or not subj_match:
        raise DraftError(f"{path}: could not find **Recipient**:/**Subject**: header lines")
    to_email = rec_match.group(1).rstrip(",;")
    subject = subj_match.group(1).strip()

    if "---" not in text:
        raise DraftError(f"{path}: no '---' separator between header and body")
    body_raw = text.split("---", 1)[1].strip("\n")

    # Reflow: blank lines separate paragraphs; a single newline inside a paragraph is
    # just the .md file's own wrap point, not an intended line break.
    paragraphs = re.split(r"\n\s*\n", body_raw)
    body = "\n\n".join(" ".join(p.split()) for p in paragraphs).strip()
    body = _BOLD_RE.sub(r"\1", body)

    return to_email, subject, body


def check_and_send(path: Path, sender) -> bool:
    to_email, subject, body = parse_draft(path)

    violations = find_violations_in_text(f"{subject}\n{body}")
    if violations:
        print(f"GATE FAIL: {path} -- {len(violations)} un-hyphenated \"DeadlineRadar\" instance(s):")
        for lineno, line in violations:
            print(f"  line {lineno}: {line}")
        print("  Refusing to send. Fix the draft (use \"Deadline-Radar\") and re-run.")
        return False
    print(f"GATE PASS: {path}")

    if sender is None:
        return True  # --check-only

    ok = sender.send(to_email=to_email, subject=subject, text_body=body)
    status = getattr(sender, "last_status", None)
    msgid = getattr(sender, "last_message_id", None)
    err = getattr(sender, "last_error", None)
    print(f"  -> {to_email!r} sent={ok} status={status} msgid={msgid} err={err}")
    return ok


def main() -> int:
    args = sys.argv[1:]
    check_only = "--check-only" in args
    dry_run = "--dry-run" in args
    files = [a for a in args if not a.startswith("--")]

    if not files:
        print(__doc__)
        return 2

    if check_only:
        sender = None
    elif dry_run:
        sender = DryRunSender()
    else:
        try:
            api_key = KEY_PATH.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            print(f"No SendGrid key at {KEY_PATH} -- this must live OUTSIDE the repo, never inside it.")
            return 2
        sender = SendGridSender(api_key=api_key, from_email=FROM_EMAIL, from_name=FROM_NAME)

    all_ok = True
    for raw in files:
        path = Path(raw)
        try:
            ok = check_and_send(path, sender)
        except DraftError as exc:
            print(f"DRAFT ERROR: {exc}")
            ok = False
        all_ok = all_ok and ok
        if sender is not None and not check_only:
            time.sleep(2)

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
