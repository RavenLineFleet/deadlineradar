"""Regression pins for preship_gate.check_date_bound_parity (AuditLab PROC-12, 2026-10-26).

    python -m pytest scripts/test_date_bound_parity.py -q

The client date-picker bounds and the worker's 400 bounds are duplicated code. Each
mutant below re-creates one way they drifted (DATE-14, DATE-15) or could drift, on a
copy of the real files, and must be caught; the real tree must pass.
"""
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import preship_gate  # noqa: E402

PAGE = "florida/index.html"


@pytest.fixture
def repo(tmp_path):
    for rel in ("generate.py", "worker/src/index.ts", "worker/src/deadline.ts", "docs/" + PAGE):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, dst)
    return tmp_path


def _mutate(path, old, new):
    s = path.read_text(encoding="utf-8")
    assert s.count(old) >= 1, f"mutant anchor {old!r} not in {path.name}"
    path.write_text(s.replace(old, new), encoding="utf-8")


def test_real_tree_passes():
    assert preship_gate.check_date_bound_parity(REPO_ROOT) == []


def test_copied_tree_passes(repo):
    assert preship_gate.check_date_bound_parity(repo) == []


def test_both_anchor_400_strings_are_pinned():
    ts = (REPO_ROOT / "worker/src/index.ts").read_text(encoding="utf-8")
    assert "needs a date that already happened -- your last issuance or renewal, not a future date." in ts
    assert "That date looks too far in the past -- please double-check your license." in ts


@pytest.mark.parametrize("rel,old,new", [
    # DATE-15: client keyed to local clock instead of UTC
    ("docs/" + PAGE, "el.max=utc", "el.max=local"),
    ("docs/" + PAGE, "var utc=iso(now)", "var utc=iso(new Date(now-now.getTimezoneOffset()*6e4))"),
    # future window bumped on one side only
    ("worker/src/deadline.ts", "USER_DEADLINE_MAX_DAYS = 1280", "USER_DEADLINE_MAX_DAYS = 1300"),
    ("generate.py", "_USER_DEADLINE_MAX_DAYS = 1280", "_USER_DEADLINE_MAX_DAYS = 1300"),
    ("docs/" + PAGE, "addDays(utc,1280)", "addDays(utc,1300)"),
    # anchor floor changed on one side only
    ("worker/src/index.ts", "366 * 10 * 86_400_000", "366 * 12 * 86_400_000"),
    ("docs/" + PAGE, "addDays(utc,-3660)", "addDays(utc,-3650)"),
    # worker check loosened / 400 reworded
    ("worker/src/index.ts", "parsedAnchor.getTime() > todayUtcForAnchor.getTime()",
     "parsedAnchor.getTime() > todayUtcForAnchor.getTime() + 86_400_000"),
    ("worker/src/index.ts", "That date looks too far in the past", "That date is too old"),
    # past10 floor clause dropped from the client
    ("docs/" + PAGE, 'if(kind==="past10")el.min=addDays(utc,-3660)', ""),
])
def test_mutant_caught(repo, rel, old, new):
    _mutate(repo / rel, old, new)
    errs = preship_gate.check_date_bound_parity(repo)
    assert errs and all(e.startswith("[PROC-12]") for e in errs), errs


def test_no_bound_pages_fails_closed(repo):
    (repo / "docs" / PAGE).unlink()
    assert any("NO built page" in e for e in preship_gate.check_date_bound_parity(repo))
