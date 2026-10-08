"""Tests for check_self_serve_plan_change_flag_consistency (self-serve plan
change, 2026-10-07): generate.py's Python SELF_SERVE_PLAN_CHANGE_ENABLED and
its embedded `var DR_SELF_SERVE_PLAN_CHANGE_ENABLED` JS literal must agree.

    python -m pytest scripts/test_self_serve_flag_gate.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402


def _tree(tmp_path, py, js):
    (tmp_path / "generate.py").write_text(
        f"SELF_SERVE_PLAN_CHANGE_ENABLED = {py}\nX = 'var DR_SELF_SERVE_PLAN_CHANGE_ENABLED = {js};'\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize("py,js", [("False", "false"), ("True", "true")])
def test_agreeing_flags_pass(tmp_path, py, js):
    assert gate.check_self_serve_plan_change_flag_consistency(_tree(tmp_path, py, js)) == []


@pytest.mark.parametrize("py,js", [("True", "false"), ("False", "true")])
def test_disagreeing_flags_error(tmp_path, py, js):
    errs = gate.check_self_serve_plan_change_flag_consistency(_tree(tmp_path, py, js))
    assert len(errs) == 1 and "disagree" in errs[0]


def test_missing_literal_is_a_loud_sync_error_not_a_silent_pass(tmp_path):
    (tmp_path / "generate.py").write_text("NOTHING = 1\n", encoding="utf-8")
    errs = gate.check_self_serve_plan_change_flag_consistency(tmp_path)
    assert len(errs) == 1 and errs[0].startswith("[SYNC] Could not find")


def test_real_repo_flags_agree():
    root = os.path.dirname(HERE)
    from pathlib import Path
    assert gate.check_self_serve_plan_change_flag_consistency(Path(root)) == []
