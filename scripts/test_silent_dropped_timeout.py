"""A wedged `wrangler d1` call must not block the preship gate (ReverifyDaily 2026-10-07: 12+ min hang)."""
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import silent_dropped_subscribers_check as sdsc  # noqa: E402


def test_wedged_child_times_out_with_skip_message():
    t0 = time.time()
    with pytest.raises(SystemExit) as ei:
        sdsc._run_with_timeout([sys.executable, "-c", "import time; time.sleep(60)"], HERE, 2)
    assert isinstance(ei.value.code, str) and "timed out" in ei.value.code  # str -> preship skips the advisory
    assert time.time() - t0 < 30


def test_wedged_grandchild_holding_the_pipe_is_killed_too(tmp_path):
    """The real shape: npx.cmd -> cmd -> node. A grandchild keeps the stdout pipe open; the
    whole tree must die, not just the direct child (grandchild heartbeat must stop)."""
    beat = tmp_path / "beat.txt"
    grandchild = ("import time,pathlib; "
                  f"p=pathlib.Path({str(beat)!r}); "
                  "[(p.write_text(str(time.time())), time.sleep(0.2)) for _ in iter(int, 1)]")
    gc_file = tmp_path / "gc.py"
    gc_file.write_text(grandchild)
    parent = ("import subprocess,sys,time;"
              f"subprocess.Popen([sys.executable,{str(gc_file)!r}]);"
              "time.sleep(60)")
    t0 = time.time()
    with pytest.raises(SystemExit) as ei:
        sdsc._run_with_timeout([sys.executable, "-c", parent], HERE, 3)
    assert "timed out" in ei.value.code
    assert time.time() - t0 < 30
    assert beat.exists(), "fixture must have started the grandchild (positive control)"
    time.sleep(1)
    m1 = beat.stat().st_mtime
    time.sleep(1.5)
    assert beat.stat().st_mtime == m1, "grandchild still running after timeout kill"


def test_normal_child_returns_output():
    r = sdsc._run_with_timeout([sys.executable, "-c", "print('ok')"], HERE, 30)
    assert r.returncode == 0 and r.stdout.strip() == "ok"
