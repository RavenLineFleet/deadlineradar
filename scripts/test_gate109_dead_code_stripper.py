"""GATE-109 (SecurityLab, filed as GATE-108, 2026-11-07): the dead-code stripper read
`x > 0` as constant-false and blanked live blocks, which hid handleFirmRuleChangeNotify's
send from the DEMO-EMAIL check. A stripper's failure mode is silence, so both directions
are pinned here: live conditions stay visible, dead ones still strip."""
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import preship_gate as pg  # noqa: E402

REPO = HERE.parent


@pytest.mark.parametrize(
    "cond",
    [
        "x > 0",
        "x >= 0",
        "x !== 0",
        "x === 0 && y",
        "0 < x",
        "targets.length > 0 && env.RESEND_API_KEY",
        "x || 0",
        "a ? b : 0",
        "!0",
        "x || y && 0",
        "arr[0]",
        "x === false",
    ],
)
def test_live_conditions_are_not_dead(cond):
    assert pg._condition_is_constant_false(cond) is False


@pytest.mark.parametrize(
    "cond",
    ["0", "(0)", "0 && x", "x && 0", "(0 && x) && y", "!!0", "0 || 0", "false", "false && x", "x && false", "!true", "!1"],
)
def test_dead_conditions_still_strip(cond):
    assert pg._condition_is_constant_false(cond) is True


def test_send_behind_a_zero_comparison_stays_visible():
    body = "if (xs.length > 0) { sendEmail(a); }"
    assert "sendEmail(" in pg._strip_dead_if_false_blocks(body)


def test_send_behind_literal_false_is_still_stripped():
    assert "sendEmail(" not in pg._strip_dead_if_false_blocks("if (false) { sendEmail(a); }")


def test_guard_buried_in_dead_code_still_does_not_count():
    body = "if (false) { if (firm.demo_locked) return; } sendEmail(a);"
    assert "demo_locked" not in pg._strip_dead_if_false_blocks(body)


def _tree(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "worker" / "src", tmp_path / "worker" / "src")
    return tmp_path / "worker" / "src" / "index.ts"


def _demo_email_errors(root: Path) -> list[str]:
    return [e for e in pg.check_demo_locked_email_coverage(root) if "handleFirmRuleChangeNotify" in e]


def test_real_handler_passes(tmp_path):
    _tree(tmp_path)
    assert _demo_email_errors(tmp_path) == []


@pytest.mark.parametrize(
    "old,new,flag",
    [
        ("} else if (session.firm.is_test_tenant) {\n    // migration 0079: same \"gate the send\" posture as demo_locked above,\n    // synthetic operator test tenant -- never demo_locked itself.\n    skipped = targets.length;\n  } else if (targets.length > 0 && env.RESEND_API_KEY) {",
         "} else if (targets.length > 0 && env.RESEND_API_KEY) {", "is_test_tenant"),
    ],
)
def test_deleting_the_guard_from_the_real_handler_fails_the_gate(tmp_path, old, new, flag):
    index = _tree(tmp_path)
    src = index.read_bytes().decode("utf-8").replace("\r\n", "\n")
    start = src.index("async function handleFirmRuleChangeNotify")
    at = src.index(old, start)
    index.write_bytes((src[:at] + new + src[at + len(old):]).encode("utf-8"))
    errors = _demo_email_errors(tmp_path)
    assert len(errors) == 1 and flag in errors[0]


def test_deleting_the_demo_guard_from_the_real_handler_fails_the_gate(tmp_path):
    index = _tree(tmp_path)
    src = index.read_bytes().decode("utf-8").replace("\r\n", "\n")
    start = src.index("async function handleFirmRuleChangeNotify")
    old = "if (session.firm.demo_locked) {\n    skipped = targets.length;\n  } else if (session.firm.is_test_tenant) {"
    at = src.index(old, start)
    index.write_bytes((src[:at] + "if (session.firm.is_test_tenant) {" + src[at + len(old):]).encode("utf-8"))
    errors = _demo_email_errors(tmp_path)
    assert len(errors) == 1 and "demo_locked" in errors[0]
