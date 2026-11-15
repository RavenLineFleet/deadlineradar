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


def _subdir_launch(tmp_path, with_ini):
    """Mirror the repo's pytest anchors into a scratch tree and launch pytest from INSIDE a
    subdirectory (as `cd reminders && pytest .` would), with GIT_DIR exported at a fake path."""
    root = tmp_path / ("with_ini" if with_ini else "no_ini")
    (root / "scripts").mkdir(parents=True)
    (root / "sub").mkdir()
    repo = Path(HERE).parent
    shutil.copyfile(repo / "conftest.py", root / "conftest.py")
    shutil.copyfile(Path(HERE, "conftest.py"), root / "scripts" / "conftest.py")
    if with_ini:
        shutil.copyfile(repo / "pytest.ini", root / "pytest.ini")
    (root / "sub" / "test_probe.py").write_text(
        "import os\n\ndef test_no_git_dir():\n    assert os.environ.get('GIT_DIR') is None\n",
        encoding="utf-8")
    env = dict(os.environ, GIT_DIR=str(tmp_path / "nonexistent.git"))
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "."],
                          cwd=root / "sub", env=env, capture_output=True, text=True)


def test_proc22_repo_root_pytest_ini_anchors_root_conftest(tmp_path):
    """PROC-22 (AuditLab, 2026-11-03): with no ini at the repo root, a pytest launched inside
    reminders/ or social/ takes that subdir as rootdir/confcutdir and never loads the root
    conftest.py, so GIT_DIR leaks. pytest.ini at the root pins rootdir. Both arms run."""
    assert Path(HERE).parent.joinpath("pytest.ini").read_text(encoding="utf-8").strip() == "[pytest]"
    anchored = _subdir_launch(tmp_path, with_ini=True)
    assert anchored.returncode == 0, anchored.stdout[-2000:] + anchored.stderr[-1000:]
    assert "configfile: pytest.ini" in anchored.stdout
    unanchored = _subdir_launch(tmp_path, with_ini=False)  # negative control: the guard is skipped
    assert unanchored.returncode == 1, unanchored.stdout[-2000:]
    assert "GIT_DIR" in unanchored.stdout


HOOK_V3 = HOOK_V2 + b"# v3\n"
COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m")


def _commit_hook(r, body, msg):
    (r / "scripts" / "git-hooks" / "pre-push").write_bytes(body)
    _git(r, "add", ".")
    _git(r, *COMMIT, msg)


def test_proc29_behind_head_matching_stale_install_fails(repo):
    """SEC-102 ARM C (48/97): HEAD is behind origin/main, installed == HEAD's older hook. The
    HEAD-only compare read this clean; the origin/main compare must not."""
    r, installed = repo
    _commit_hook(r, HOOK_V1, "old")                                # HEAD~0 = V1 (the behind tree)
    _commit_hook(r, HOOK_V2, "new")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")      # origin/main ships V2
    _git(r, "checkout", "-q", "HEAD~1")                            # behind: HEAD has V1
    (installed / "pre-push").write_bytes(HOOK_V1)                  # installed == HEAD (reverted)
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and errs[0].startswith("[PROC-29]") and "behind origin/main" in errs[0], errs


def test_proc29_branch_ahead_with_new_hook_passes(repo):
    """SecurityLab CASE 2: the ancestry clause is load-bearing -- a branch ahead of origin/main
    with a new hook, installed correctly, must not be flagged."""
    r, installed = repo
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")      # origin/main ships V2
    _commit_hook(r, HOOK_V3, "ahead")                              # HEAD ahead with V3
    (installed / "pre-push").write_bytes(HOOK_V3)
    assert gate.check_installed_git_hooks_match_head(r) == []


def test_proc29_hook_untracked_by_head_is_still_examined(repo):
    """SEC-102 widening (10/97): HEAD tracks only commit-msg, origin/main tracks pre-push. The
    HEAD-derived name set never looked at pre-push; the union must."""
    r, installed = repo
    hooks = r / "scripts" / "git-hooks"
    (hooks / "pre-push").unlink()
    (hooks / "commit-msg").write_bytes(b"#!/bin/sh\nexit 0\n")
    _git(r, "add", "-A")
    _git(r, *COMMIT, "old tree: commit-msg only")
    (installed / "commit-msg").write_bytes(b"#!/bin/sh\nexit 0\n")
    _commit_hook(r, HOOK_V2, "pre-push added")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")      # origin/main: commit-msg + pre-push V2
    _git(r, "checkout", "-q", "HEAD~1")                            # HEAD: commit-msg only
    (installed / "pre-push").write_bytes(HOOK_V1)                  # substituted/reverted
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and errs[0].startswith("[PROC-29]") and "does not track" in errs[0], errs
    (installed / "pre-push").unlink()                              # deleted entirely
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and "missing" in errs[0] and "origin/main tracks" in errs[0], errs


def test_proc19_behind_head_never_advises_copying_the_older_hook(repo):
    r, installed = repo
    _commit_hook(r, HOOK_V3, "new")
    _git(r, "update-ref", "refs/remotes/origin/main", "HEAD")      # origin/main ships V3
    _git(r, "checkout", "-q", "HEAD~1")                            # behind: HEAD has V2
    (installed / "pre-push").write_bytes(HOOK_V1)                  # installed matches neither
    errs = gate.check_installed_git_hooks_match_head(r)
    assert len(errs) == 1 and errs[0].startswith("[PROC-19]") and "copy origin/main:" in errs[0], errs


def test_reverify_skip_is_disclosed_not_silent(repo, monkeypatch):
    """SEC-102 limb 2: the skip must leave a trace main() prints, so PASS never covers it."""
    r, _ = repo
    monkeypatch.setattr(gate, "_SKIPPED_CHECKS", [])
    monkeypatch.setenv("DR_REVERIFY_JOB", "1")
    assert gate.check_installed_git_hooks_match_head(r) == []
    assert len(gate._SKIPPED_CHECKS) == 1 and "DR_REVERIFY_JOB" in gate._SKIPPED_CHECKS[0]
    monkeypatch.delenv("DR_REVERIFY_JOB")
    gate.check_installed_git_hooks_match_head(r)
    assert len(gate._SKIPPED_CHECKS) == 1                          # no trace when it actually ran
    body = Path(HERE, "preship_gate.py").read_text(encoding="utf-8")
    body = body[body.index("\ndef main():"):]
    assert body.index('print(f"SKIPPED (not evaluated): {why}")') < body.index(r'print("\nPASS -- no violations found.")')
