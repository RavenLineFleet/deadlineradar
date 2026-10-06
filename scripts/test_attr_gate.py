"""Tests for the ATTR-5/ATTR-6 Claude-attribution gate (preship_gate regex + commit-msg hook).

    python -m pytest scripts/test_attr_gate.py -q

ATTR-6 (Orchestrator, 2026-10-05): the original patterns missed
"Assisted-By: Claude" and an unbracketed "Generated with Claude Code".
Positive cases must match, negative cases (ordinary prose) must not, and the
shell hook must agree with the Python regex on every case.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

HOOK = os.path.join(HERE, "git-hooks", "commit-msg")

POSITIVE = [
    "Co-Authored-By: Claude <noreply@anthropic.com>",
    "Generated with [Claude Code](https://claude.com/claude-code)",
    "Generated with Claude Code",
    "generated with claude code",
    "Assisted-By: Claude",
    "assisted-by:Claude Sonnet",
    "Claude-Session: abc123",
    "see https://claude.ai/code/session_x",
]
NEGATIVE = [
    "Fix Nevada firm fee copy",
    "Assisted-By: a human reviewer",
    "Generated with the build script",
    "Update Claude-free docs",
    "Fix generate.py codepath for Code pages",
]


@pytest.mark.parametrize("msg", POSITIVE)
def test_regex_matches(msg):
    assert gate._CLAUDE_ATTRIBUTION_RE.search("subject\n\n" + msg)


@pytest.mark.parametrize("msg", NEGATIVE)
def test_regex_ignores_prose(msg):
    assert not gate._CLAUDE_ATTRIBUTION_RE.search("subject\n\n" + msg)


def _hook_rc(msg):
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("sh not available")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, newline="\n") as f:
        f.write("subject\n\n" + msg + "\n")
        path = f.name
    try:
        return subprocess.run([sh, HOOK.replace("\\", "/"), path.replace("\\", "/")],
                              capture_output=True, text=True).returncode
    finally:
        os.unlink(path)


@pytest.mark.parametrize("msg", POSITIVE)
def test_hook_rejects(msg):
    assert _hook_rc(msg) == 1


@pytest.mark.parametrize("msg", NEGATIVE)
def test_hook_accepts(msg):
    assert _hook_rc(msg) == 0
