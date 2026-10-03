"""Tests for STALE-27's cpa_deadlines cohort-concentration gate (Orchestrator
STOP, 23:03 MDT 2026-10-02 -- a flat "fail if any date has >10" blocked every
ship for ~11 days on data that was already over cap BEFORE this gate
existed, which is worse than the problem it prevents).

    python -m pytest scripts/test_stale27_cohort_concentration_gate.py -q

check_cpa_deadlines_verification_date_concentration() hard-fails on exactly
two conditions, never on "over cap" alone:
    (a) RATCHET   -- this change grows a date's cohort past the cap (or
                      grows it further if already over), compared against
                      the committed (HEAD) version of the file.
    (b) IMMINENT  -- a cohort already over cap, not grown by this change,
                      is now within CPA_DEADLINES_COHORT_IMMINENT_DAYS of
                      its own 31-day staleness cliff.
Everything else over cap is advisory-only (loud, with a countdown), proven
separately below.
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

TODAY = datetime.datetime.now(datetime.timezone.utc).date()
# stale_on = date + 31d. Comfortably past the 14-day imminent window either way.
FAR_DATE = (TODAY + datetime.timedelta(days=60)).isoformat()
# stale_on = date + 31d = today + 11d -- inside the 14-day imminent window.
IMMINENT_DATE = (TODAY - datetime.timedelta(days=20)).isoformat()


def _records(n, verified_date):
    return [{"id": f"r{i}", "last_verified": verified_date} for i in range(n)]


def _git_repo_at(tmp_path, committed_records):
    """A real git repo whose HEAD commit has data/cpa_deadlines.json set to
    `committed_records` -- the ratchet's baseline needs real git history,
    not a fixture a mock could silently diverge from."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "cpa_deadlines.json").write_text(json.dumps({"records": committed_records}), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    return tmp_path


def _set_working_tree(tmp_path, records):
    (tmp_path / "data" / "cpa_deadlines.json").write_text(json.dumps({"records": records}), encoding="utf-8")


def test_at_cap_exactly_passes(tmp_path):
    repo = _git_repo_at(tmp_path, _records(10, FAR_DATE))
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_ratchet_fails_when_this_change_grows_a_cohort_past_the_cap(tmp_path):
    repo = _git_repo_at(tmp_path, _records(10, FAR_DATE))  # at cap at HEAD
    _set_working_tree(repo, _records(11, FAR_DATE))  # one over, grown by this change
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]


def test_ratchet_fails_when_an_already_over_cap_cohort_grows_further(tmp_path):
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE))  # already over cap at HEAD
    _set_working_tree(repo, _records(12, FAR_DATE))  # grew further
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]


def test_already_over_cap_but_unchanged_and_not_imminent_passes(tmp_path):
    """The exact real-world shape this gate was rewritten for: 81 records
    already shared one date in an earlier commit, 30 days of lead time
    still on the clock -- this must NOT block an unrelated ship."""
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE))
    _set_working_tree(repo, _records(11, FAR_DATE))  # unchanged from HEAD
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_already_over_cap_but_shrinking_passes_even_though_still_over_cap(tmp_path):
    repo = _git_repo_at(tmp_path, _records(12, FAR_DATE))
    _set_working_tree(repo, _records(11, FAR_DATE))  # improved, still over cap
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_imminent_fails_even_when_not_grown_by_this_change(tmp_path):
    repo = _git_repo_at(tmp_path, _records(11, IMMINENT_DATE))
    _set_working_tree(repo, _records(11, IMMINENT_DATE))  # unchanged, but close to its own cliff
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-IMMINENT" in errors[0]


def test_over_cap_not_imminent_boundary_at_exactly_15_days_passes(tmp_path):
    # stale_on = verified_date + 31d. Want days_until_stale == 15 (just
    # outside the <=14 imminent window) -> verified_date = today - 16.
    boundary_date = (TODAY - datetime.timedelta(days=16)).isoformat()
    repo = _git_repo_at(tmp_path, _records(11, boundary_date))
    _set_working_tree(repo, _records(11, boundary_date))
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_over_cap_imminent_boundary_at_exactly_14_days_fails(tmp_path):
    # days_until_stale == 14 (right at the cap) -> verified_date = today - 17.
    boundary_date = (TODAY - datetime.timedelta(days=17)).isoformat()
    repo = _git_repo_at(tmp_path, _records(11, boundary_date))
    _set_working_tree(repo, _records(11, boundary_date))
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-IMMINENT" in errors[0]


def test_ratchet_takes_priority_over_imminent_for_the_same_date_not_both(tmp_path):
    """A date that is BOTH grown-by-this-change AND imminent reports once,
    as RATCHET (the more actionable fact: this specific change is why it's
    red) -- not twice for the same date."""
    repo = _git_repo_at(tmp_path, _records(10, IMMINENT_DATE))
    _set_working_tree(repo, _records(11, IMMINENT_DATE))
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]


def test_no_head_commit_never_blocks_on_the_ratchet_basis_alone(tmp_path):
    """A repo with no commits yet (or git unavailable) has no baseline to
    ratchet against -- must fall through to the imminent check only, never
    silently treat 'no baseline' as 'infinite growth'."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "cpa_deadlines.json").write_text(json.dumps({"records": _records(11, FAR_DATE)}), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # no commit made
    assert gate.check_cpa_deadlines_verification_date_concentration(tmp_path) == []


def test_missing_last_verified_ignored_not_miscounted(tmp_path):
    repo = _git_repo_at(tmp_path, [{"id": f"r{i}"} for i in range(20)])
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_multiple_dates_each_evaluated_independently(tmp_path):
    committed = _records(10, FAR_DATE) + _records(11, IMMINENT_DATE)
    repo = _git_repo_at(tmp_path, committed)
    grown_other_date = _records(11, FAR_DATE) + _records(11, IMMINENT_DATE)  # FAR_DATE grew, IMMINENT_DATE unchanged
    _set_working_tree(repo, grown_other_date)
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 2
    joined = "\n".join(errors)
    assert "STALE27-RATCHET" in joined
    assert "STALE27-IMMINENT" in joined
