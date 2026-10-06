"""Tests for the DEPLOY-15 origin-tip gate in deploy_worker.py.

    python -m pytest scripts/test_deploy_worker_gate.py -q

AuditLab DEPLOY-15 (2026-10-06): a production deploy must refuse unless HEAD
equals origin/main's tip (per `git ls-remote`). Wrangler and git are mocked, so
nothing deploys. Positive control: the matching case reaches wrangler and
writes the marker.
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deploy_worker as dw  # noqa: E402

HEAD = "a" * 40
OTHER = "b" * 40


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls = {"wrangler": 0}
    monkeypatch.setattr(dw, "worker_tree_dirty", lambda: False)
    monkeypatch.setattr(dw, "unapplied_migrations", lambda: [])
    monkeypatch.setattr(dw, "head_commit", lambda: HEAD)
    monkeypatch.setattr(dw, "MARKER", tmp_path / ".last_deploy_commit")

    def fake_run(cmd, **kw):
        calls["wrangler"] += 1
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(dw.subprocess, "run", fake_run)
    return calls


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["deploy_worker.py", *argv])
    return dw.main()


def test_matching_tip_deploys_and_writes_marker(monkeypatch, env):
    monkeypatch.setattr(dw, "remote_main_tip", lambda: HEAD)
    assert _main(monkeypatch) == 0
    assert env["wrangler"] == 1
    assert dw.MARKER.read_text().strip() == HEAD


def test_diverged_head_refuses_before_wrangler(monkeypatch, env):
    monkeypatch.setattr(dw, "remote_main_tip", lambda: OTHER)
    assert _main(monkeypatch) == 1
    assert env["wrangler"] == 0
    assert not dw.MARKER.exists()


def test_unreadable_remote_refuses(monkeypatch, env):
    monkeypatch.setattr(dw, "remote_main_tip", lambda: None)
    assert _main(monkeypatch) == 1
    assert env["wrangler"] == 0


def test_override_deploys_but_skips_marker(monkeypatch, env):
    monkeypatch.setattr(dw, "remote_main_tip", lambda: OTHER)
    assert _main(monkeypatch, "--allow-non-origin-head") == 0
    assert env["wrangler"] == 1
    assert not dw.MARKER.exists()


def test_preview_not_gated(monkeypatch, env):
    monkeypatch.setattr(dw, "remote_main_tip", lambda: OTHER)
    assert _main(monkeypatch, "--preview") == 0
    assert env["wrangler"] == 1
