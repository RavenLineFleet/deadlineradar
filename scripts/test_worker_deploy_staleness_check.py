"""DRIFT-1 regression: worker_deploy_staleness_check must measure origin/main,
never the local checkout line. Fixture = a diverged checkout whose OWN
worker/src commit precedes its OWN marker (the silent-PASS case) while
origin/main has an undeployed worker/src change."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import worker_deploy_staleness_check as wdsc  # noqa: E402


def _g(cwd, *a):
    r = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *a],
                       cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()


def _commit(cwd, rel, text, msg):
    p = Path(cwd, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, newline="\n")
    _g(cwd, "add", "-A")
    _g(cwd, "commit", "-q", "-m", msg)
    return _g(cwd, "rev-parse", "HEAD")


def _legacy_check(root):
    """Replica of the pre-DRIFT-1 decision logic: marker from working tree,
    last src commit from local HEAD."""
    marker = Path(root, "worker/.last_deploy_commit").read_text().strip()
    last_src = _g(root, "log", "--format=%H", "-1", "--", "worker/src")
    r = subprocess.run(["git", "merge-base", "--is-ancestor", last_src, marker], cwd=root)
    return "PASS" if r.returncode == 0 else "ADVISORY"


def _build(tmp_path, origin_undeployed=True):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _g(seed, "remote", "add", "origin", str(origin))
    c1 = _commit(seed, "worker/src/a.json", "1", "c1 src")
    c2 = _commit(seed, "worker/.last_deploy_commit", c1 + "\n", "c2 marker=c1")
    if origin_undeployed:
        _commit(seed, "worker/src/a.json", "2", "c3 undeployed src change")
    _g(seed, "push", "-q", "origin", "main")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    return origin, local, c1


def test_diverged_checkout_old_code_passes_wrongly_new_code_flags(tmp_path):
    _, local, c1 = _build(tmp_path)
    # diverge: local main branches off c1 with its own src change + own marker
    _g(local, "checkout", "-q", "-B", "main", c1)
    l1 = _commit(local, "worker/src/a.json", "local-line", "L1 local src")
    _commit(local, "worker/.last_deploy_commit", l1 + "\n", "L2 local marker=L1")
    assert _legacy_check(local) == "PASS"           # positive control: old code is wrong here
    out = wdsc.check(local)
    assert out.startswith("ADVISORY"), out          # new code sees origin/main's undeployed change
    assert "origin/main" in out and "1)" in out     # names the ref, 1 undeployed commit
    assert "worker/src/a.json" in out


def test_pass_names_the_ref_when_origin_main_is_current(tmp_path):
    _, local, _ = _build(tmp_path, origin_undeployed=False)
    out = wdsc.check(local)
    assert out.startswith("PASS [measured at origin/main"), out


def test_never_reads_working_tree_marker(tmp_path):
    _, local, _ = _build(tmp_path, origin_undeployed=False)
    Path(local, "worker/.last_deploy_commit").write_text("deadbeef\n")  # uncommitted garbage
    assert wdsc.check(local).startswith("PASS"), "working-tree marker must be ignored"


def test_fetch_failure_is_disclosed_not_silent(tmp_path):
    _, local, _ = _build(tmp_path, origin_undeployed=False)
    _g(local, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    # remote ref still cached locally; fetch fails -> must say so
    out = wdsc.check(local)
    assert "fetch failed" in out, out


def test_missing_marker_at_ref_is_advisory(tmp_path):
    origin = tmp_path / "o.git"
    seed = tmp_path / "s"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _g(seed, "remote", "add", "origin", str(origin))
    _commit(seed, "worker/src/a.json", "1", "c1")
    _g(seed, "push", "-q", "origin", "main")
    out = wdsc.check(seed)
    assert out.startswith("ADVISORY") and "not readable at origin/main" in out, out


def _behind(tmp_path):
    """local is BEHIND origin/main (not diverged): its last worker/src commit is
    old and ancestral to the marker, while origin/main has an undeployed change."""
    origin, seed = tmp_path / "o.git", tmp_path / "s"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _g(seed, "remote", "add", "origin", str(origin))
    c1 = _commit(seed, "worker/src/a.json", "1", "c1 src")
    _commit(seed, "worker/.last_deploy_commit", c1 + "\n", "c2 marker=c1")
    _g(seed, "push", "-q", "origin", "main")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    _commit(seed, "worker/src/a.json", "2", "c3 UNDEPLOYED src change")
    _g(seed, "push", "-q", "origin", "main")
    return local


def test_pass_label_names_origin_mains_own_src_commit(tmp_path):
    """DRIFT-2 (SecurityLab): the label must carry origin/main's last src commit,
    not the local line's -- catches a revert of the last_src_commit half."""
    origin, seed = tmp_path / "o.git", tmp_path / "s"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _g(seed, "remote", "add", "origin", str(origin))
    c1 = _commit(seed, "worker/src/a.json", "1", "c1 src")
    _commit(seed, "worker/.last_deploy_commit", c1 + "\n", "c2 marker=c1")
    _g(seed, "push", "-q", "origin", "main")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    # local adds its OWN later worker/src commit, so HEAD's src commit != origin's
    _commit(local, "worker/src/local_only.json", "x", "L1 local-only src")
    out = wdsc.check(local)
    expected = _g(local, "log", "--format=%h", "-1", "origin/main", "--", "worker/src")
    assert f"origin/main {expected}" in out, f"label must name origin/main's src commit {expected}: {out}"


def test_behind_checkout_must_not_pass(tmp_path):
    """DRIFT-2: a checkout merely BEHIND origin/main -- where reading last_src_commit
    from local HEAD silently PASSes."""
    out = wdsc.check(_behind(tmp_path))
    assert out.startswith("ADVISORY"), f"behind checkout must not report PASS: {out}"
    assert "worker/src/a.json" in out, out
