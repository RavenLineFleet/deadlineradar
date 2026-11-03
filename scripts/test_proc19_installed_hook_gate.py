"""PROC-19 (AuditLab, 2026-11-03): preship_gate must fail when the hook git actually runs
(core.hooksPath) differs from the tree's own scripts/git-hooks/, and skip inside the reverify job.

    python -m pytest scripts/test_proc19_installed_hook_gate.py -q
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

GIT = shutil.which("git")
HOOK_V2 = b"#!/bin/sh\npython -m pytest scripts -q || exit 1\npython scripts/preship_gate.py\n"
HOOK_V1 = b"#!/bin/sh\npython scripts/preship_gate.py\n"


def _git(repo, *args):
    subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if not GIT:
        pytest.skip("git not on PATH")
    monkeypatch.delenv("DR_REVERIFY_JOB", raising=False)
    r = tmp_path / "repo"
    (r / "scripts" / "git-hooks").mkdir(parents=True)
    (r / "scripts" / "git-hooks" / "pre-push").write_bytes(HOOK_V2)
    _git(r, "init", "-q")
    _git(r, "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init")
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "pre-push").write_bytes(HOOK_V2)
    _git(r, "config", "core.hooksPath", str(installed))
    return r, installed


def test_matching_installed_hook_passes(repo):
    r, _ = repo
    assert gate.check_installed_git_hooks_match_head(r) == []


def test_crlf_copy_of_same_text_passes(repo):
    r, installed = repo
    _git(r, "config", "core.autocrlf", "true")
    (installed / "pre-push").write_bytes(HOOK_V2.replace(b"\n", b"\r\n"))
    assert gate.check_installed_git_hooks_match_head(r) == []


def test_reverted_installed_hook_fails(repo):
    r, installed = repo
    (installed / "pre-push").write_bytes(HOOK_V1)  # the PROC-19 shape: HEAD-restore to the PROC-4-only hook
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and errs[0].startswith("[PROC-19]") and "pre-push" in errs[0]


def test_missing_installed_hook_fails(repo):
    r, installed = repo
    (installed / "pre-push").unlink()
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and "missing" in errs[0]


def test_unset_hooks_path_fails(repo):
    r, _ = repo
    _git(r, "config", "--unset", "core.hooksPath")
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and "not set" in errs[0]


def test_reverify_job_context_skips(repo, monkeypatch):
    r, installed = repo
    (installed / "pre-push").write_bytes(HOOK_V1)
    monkeypatch.setenv("DR_REVERIFY_JOB", "1")
    assert gate.check_installed_git_hooks_match_head(r) == []


def test_hook_env_does_not_leak_into_test_repos(tmp_path):
    """Run this file's fixture-based tests the way the pre-push hook does -- with GIT_DIR (and a
    GIT_CONFIG_PARAMETERS override) exported at a DECOY repo -- and prove conftest.py contains it:
    the tests still pass and the decoy's config is byte-identical afterwards."""
    if not GIT:
        pytest.skip("git not on PATH")
    decoy = tmp_path / "decoy"
    _git(tmp_path, "init", "-q", str(decoy))
    cfg = decoy / ".git" / "config"
    before = cfg.read_bytes()
    env = dict(os.environ, GIT_DIR=str(decoy / ".git"), GIT_PREFIX="",
               GIT_CONFIG_PARAMETERS="'core.hookspath'='/nonexistent'")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", "not leak",
         os.path.join(HERE, "test_proc19_installed_hook_gate.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    assert cfg.read_bytes() == before


def test_wired_into_main():
    src = Path(HERE, "preship_gate.py").read_text(encoding="utf-8")
    body = src[src.index("\ndef main():"):]
    assert "all_errors += check_installed_git_hooks_match_head(repo_root)" in body


def test_reverify_job_sets_skip_flag():
    src = Path(HERE, "reverify", "job.py").read_text(encoding="utf-8")
    assert 'os.environ["DR_REVERIFY_JOB"] = "1"' in src


def test_proc20_branch_predating_installed_hook_says_rebase_not_copy(repo):
    """PROC-20: installed == origin/main's hook, this branch's HEAD carries an older one."""
    r, installed = repo
    (r / "scripts" / "git-hooks" / "pre-push").write_bytes(HOOK_V1)
    _git(r, "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "newer")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")      # origin/main ships V1
    (installed / "pre-push").write_bytes(HOOK_V1)                  # installed == origin/main
    _git(r, "checkout", "-q", "HEAD~1")                            # stale branch: HEAD has V2
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and errs[0].startswith("[PROC-20]")
    assert "Rebase" in errs[0] and "Do NOT copy" in errs[0]


def test_proc20_stale_installed_hook_still_says_copy(repo):
    r, installed = repo
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")      # origin/main ships V2
    (installed / "pre-push").write_bytes(HOOK_V1)                  # installed is the stale side
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and errs[0].startswith("[PROC-19]") and "copy" in errs[0]


def test_proc21_hook_unsets_reverify_job_flag_before_preship():
    src = Path(HERE, "git-hooks", "pre-push").read_text(encoding="utf-8")
    assert "unset DR_REVERIFY_JOB" in src
    assert src.index("unset DR_REVERIFY_JOB") < src.index("scripts/preship_gate.py\n")
