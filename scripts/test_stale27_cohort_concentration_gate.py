"""Tests for STALE-27's cpa_deadlines cohort-concentration gate (Orchestrator
STOP, 23:03 MDT 2026-10-02 -- a flat "fail if any date has >10" blocked every
ship for ~11 days on data that was already over cap BEFORE this gate
existed, which is worse than the problem it prevents).

    python -m pytest scripts/test_stale27_cohort_concentration_gate.py -q

check_cpa_deadlines_verification_date_concentration() hard-fails on exactly
three conditions, never on "over cap" alone:
    (a) RATCHET    -- a date's cohort exceeds its recorded ceiling in
                       scripts/cohort_baseline.json (or the plain cap, if
                       the date has no recorded ceiling yet).
    (b) BASELINE   -- scripts/cohort_baseline.json itself raised a date's
                       ceiling above what was last committed (STALE-37:
                       the baseline may only ever move down).
    (c) IMMINENT   -- a cohort already at its own recorded ceiling (flat,
                       not grown by this change) is now within
                       CPA_DEADLINES_COHORT_IMMINENT_DAYS of its own
                       31-day staleness cliff.
Everything else over cap is advisory-only (loud, with a countdown), proven
separately below.

STALE-37 (AuditLab, MEDIUM, 2026-10-03) replaced the original design, which
used data/cpa_deadlines.json's own HEAD commit as the ratchet's baseline.
That baseline read FLAT on every clean working tree (a post-commit run can
never show its own commit as "grown"), which meant the shrink exemption
stopped working the moment a tranche was actually committed -- and a single
commit that grew a cohort became its own new baseline forever after. The
baseline now lives in its own committed file, scripts/cohort_baseline.json,
which this gate treats as a ceiling that may only ever decrease.
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
# stale_on = date + 30d (guard trips 12:00 UTC, STALE-46). Comfortably past the 14-day imminent window either way.
FAR_DATE = (TODAY + datetime.timedelta(days=60)).isoformat()
# stale_on = date + 30d = today + 10d -- inside the 14-day imminent window.
IMMINENT_DATE = (TODAY - datetime.timedelta(days=20)).isoformat()


def _records(n, verified_date):
    return [{"id": f"r{i}", "last_verified": verified_date} for i in range(n)]


def _git_repo_at(tmp_path, committed_records, committed_baseline=None):
    """A real git repo whose HEAD commit has data/cpa_deadlines.json set to
    `committed_records`, and -- if given -- scripts/cohort_baseline.json
    set to `committed_baseline`. Real git history, not a fixture a mock
    could silently diverge from."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "cpa_deadlines.json").write_text(json.dumps({"records": committed_records}), encoding="utf-8")
    if committed_baseline is not None:
        (tmp_path / "scripts" / "cohort_baseline.json").write_text(json.dumps(committed_baseline), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    return tmp_path


def _set_working_tree(tmp_path, records):
    (tmp_path / "data" / "cpa_deadlines.json").write_text(json.dumps({"records": records}), encoding="utf-8")


def _set_baseline(tmp_path, baseline):
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts" / "cohort_baseline.json").write_text(json.dumps(baseline), encoding="utf-8")


def test_at_cap_exactly_passes(tmp_path):
    repo = _git_repo_at(tmp_path, _records(10, FAR_DATE))
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_ratchet_fires_the_first_time_a_date_goes_over_the_plain_cap(tmp_path):
    """The baseline file exists (this repo has adopted the mechanism) but
    has no entry for this date yet (it has never been over cap before), so
    it ratchets against the plain cap itself."""
    repo = _git_repo_at(tmp_path, _records(10, FAR_DATE), committed_baseline={})  # file exists, empty
    _set_working_tree(repo, _records(11, FAR_DATE))  # one over, grown by this change
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]


def test_ratchet_fails_when_an_already_over_cap_cohort_grows_past_its_recorded_ceiling(tmp_path):
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE), committed_baseline={FAR_DATE: 11})
    _set_working_tree(repo, _records(12, FAR_DATE))  # grew past its recorded ceiling of 11
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]
    assert "ceiling of 11" in errors[0]


def test_already_over_cap_but_unchanged_and_not_imminent_passes(tmp_path):
    """The exact real-world shape this gate was rewritten for: 81 records
    already shared one date in an earlier commit, 30 days of lead time
    still on the clock -- this must NOT block an unrelated ship."""
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE), committed_baseline={FAR_DATE: 11})
    _set_working_tree(repo, _records(11, FAR_DATE))  # unchanged, at its recorded ceiling
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_already_over_cap_but_shrinking_below_ceiling_passes_even_though_still_over_cap(tmp_path):
    repo = _git_repo_at(tmp_path, _records(12, FAR_DATE), committed_baseline={FAR_DATE: 12})
    _set_working_tree(repo, _records(11, FAR_DATE))  # improved, still over cap, below its ceiling
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_clean_tree_after_a_shrink_commit_still_passes_the_stale37_regression(tmp_path):
    """STALE-37's exact failure mode: the OLD baseline (data/cpa_deadlines.
    json's own HEAD) always equalled a clean tree's own counts, so the
    shrink exemption only worked BEFORE a commit, never after one. Here
    the shrink has already been committed (both the data and a matching
    tighter baseline), the tree is clean, and the cohort is still over
    cap and imminent -- this must pass, not hard-fail on a tree that has
    nothing left to shrink any further this run."""
    repo = _git_repo_at(tmp_path, _records(11, IMMINENT_DATE), committed_baseline={IMMINENT_DATE: 20})
    # no working-tree change at all -- simulating "run the gate again after the commit that shrank 20 -> 11"
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_imminent_fails_even_when_not_grown_by_this_change(tmp_path):
    repo = _git_repo_at(tmp_path, _records(11, IMMINENT_DATE), committed_baseline={IMMINENT_DATE: 11})
    _set_working_tree(repo, _records(11, IMMINENT_DATE))  # unchanged, but close to its own cliff
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-IMMINENT" in errors[0]


def test_over_cap_not_imminent_boundary_at_exactly_15_days_passes(tmp_path):
    # stale_on = verified_date + 30d (STALE-46). Want days_until_stale == 15 (just
    # outside the <=14 imminent window) -> verified_date = today - 15.
    boundary_date = (TODAY - datetime.timedelta(days=15)).isoformat()
    repo = _git_repo_at(tmp_path, _records(11, boundary_date), committed_baseline={boundary_date: 11})
    _set_working_tree(repo, _records(11, boundary_date))
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_over_cap_imminent_boundary_at_exactly_14_days_fails(tmp_path):
    # days_until_stale == 14 (right at the cap) -> verified_date = today - 16.
    boundary_date = (TODAY - datetime.timedelta(days=16)).isoformat()
    repo = _git_repo_at(tmp_path, _records(11, boundary_date), committed_baseline={boundary_date: 11})
    _set_working_tree(repo, _records(11, boundary_date))
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-IMMINENT" in errors[0]


def test_ratchet_takes_priority_over_imminent_for_the_same_date_not_both(tmp_path):
    """A date that is BOTH grown-by-this-change AND imminent reports once,
    as RATCHET (the more actionable fact: this specific change is why it's
    red) -- not twice for the same date."""
    repo = _git_repo_at(tmp_path, _records(10, IMMINENT_DATE), committed_baseline={})  # at cap, no baseline entry
    _set_working_tree(repo, _records(11, IMMINENT_DATE))
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]


def test_no_baseline_file_at_all_never_blocks_on_the_ratchet_basis_alone(tmp_path):
    """A checkout that has never adopted scripts/cohort_baseline.json (or
    lost it) has no basis to ratchet against -- must fall through to the
    imminent check only, never silently treat 'no baseline' as 'every
    over-cap date has a ceiling of exactly the plain cap.'"""
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE))  # no committed_baseline
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


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


def test_imminent_does_not_fire_on_a_cohort_shrinking_below_its_ceiling(tmp_path):
    """AuditLab pre-review (stale27_ratchet_prereview.md): if imminent
    fired on a shrinking cohort too, every one of HomeLab's own daily
    tranche commits (each shrinking an over-cap cohort by ~8) would
    hard-fail once inside the 14-day window, reproducing the exact
    deadlock this gate was rewritten to fix -- just delayed to ~10-19."""
    repo = _git_repo_at(tmp_path, _records(15, IMMINENT_DATE), committed_baseline={IMMINENT_DATE: 15})
    _set_working_tree(repo, _records(12, IMMINENT_DATE))  # shrank below its ceiling, still over cap, still imminent
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_imminent_fires_on_a_flat_cohort_the_real_backstop(tmp_path):
    """A cohort that is neither growing nor shrinking (the remediation has
    stalled) must still eventually hard-block once imminent -- that is
    the actual backstop against a dead/failing reverify job, distinct
    from (and not weakened by) the shrinking exemption above."""
    repo = _git_repo_at(tmp_path, _records(11, IMMINENT_DATE), committed_baseline={IMMINENT_DATE: 11})
    _set_working_tree(repo, _records(11, IMMINENT_DATE))  # flat
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-IMMINENT" in errors[0]


def test_baseline_cannot_be_raised_to_launder_a_grown_and_committed_cohort(tmp_path):
    """STALE-37's laundering hole: a single commit that grows the data AND
    raises the baseline to match must not be able to make the grown state
    permanently legal starting next run."""
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE), committed_baseline={FAR_DATE: 11})
    _set_working_tree(repo, _records(20, FAR_DATE))
    _set_baseline(repo, {FAR_DATE: 20})  # attempting to raise the ceiling to match the grown data
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-BASELINE" in errors[0]
    assert "from 11 to 20" in errors[0]


def test_baseline_may_be_lowered_freely(tmp_path):
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE), committed_baseline={FAR_DATE: 15})
    _set_working_tree(repo, _records(11, FAR_DATE))  # unchanged data
    _set_baseline(repo, {FAR_DATE: 11})  # tightening the ceiling to match reality
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_baseline_check_does_not_apply_when_nothing_was_ever_committed(tmp_path):
    """The commit that first introduces scripts/cohort_baseline.json has
    nothing to compare against -- must not be flagged as 'raising' a
    ceiling that never existed before."""
    repo = _git_repo_at(tmp_path, _records(11, FAR_DATE))  # no committed_baseline at all
    _set_baseline(repo, {FAR_DATE: 11})  # first-ever introduction of the file, uncommitted
    assert gate.check_cpa_deadlines_verification_date_concentration(repo) == []


def test_tighten_baseline_lowers_to_current_count_and_never_raises(tmp_path):
    repo = _git_repo_at(tmp_path, _records(20, FAR_DATE), committed_baseline={FAR_DATE: 20})
    _set_working_tree(repo, _records(12, FAR_DATE))  # remediation shrank it
    result = gate.tighten_cpa_deadlines_cohort_baseline(repo)
    assert result[FAR_DATE] == 12
    on_disk = json.loads((repo / "scripts" / "cohort_baseline.json").read_text(encoding="utf-8"))
    assert on_disk[FAR_DATE] == 12


def test_tighten_baseline_never_raises_an_existing_entry(tmp_path):
    """If the real count somehow reads HIGHER than the recorded ceiling
    (the ratchet would already be failing in that state), tightening must
    never move the ceiling the wrong direction."""
    repo = _git_repo_at(tmp_path, _records(15, FAR_DATE), committed_baseline={FAR_DATE: 11})
    result = gate.tighten_cpa_deadlines_cohort_baseline(repo)
    assert result[FAR_DATE] == 11


def test_tighten_baseline_seeds_a_new_entry_at_the_lesser_of_cap_and_count(tmp_path):
    repo = _git_repo_at(tmp_path, _records(7, FAR_DATE))  # under cap, no baseline entry yet
    result = gate.tighten_cpa_deadlines_cohort_baseline(repo)
    assert result[FAR_DATE] == 7  # min(cap=10, count=7)


def test_gate35_skip_count_reported_by_the_advisory(tmp_path, capsys):
    records = _records(3, FAR_DATE) + [{"id": "no-date"}, {"id": "bad-date", "last_verified": "not-a-date"}]
    repo = _git_repo_at(tmp_path, records)
    gate.print_cpa_deadlines_cohort_concentration_advisory(repo)
    out = capsys.readouterr().out
    assert "2 record(s) skipped" in out


def test_gate35_skip_count_silent_when_nothing_skipped(tmp_path, capsys):
    repo = _git_repo_at(tmp_path, _records(3, FAR_DATE))
    gate.print_cpa_deadlines_cohort_concentration_advisory(repo)
    out = capsys.readouterr().out
    assert "skipped" not in out


def test_gate36_a_date_and_its_full_timestamp_form_count_as_one_cohort(tmp_path):
    """"2026-12-01" and "2026-12-01T00:00:00Z" are the same real-world
    cohort. Split, 6 + 6 each individually look under cap; merged, 12
    exceeds it -- the old raw-string key silently passed this (GATE-36)."""
    plain = [{"id": f"p{i}", "last_verified": FAR_DATE} for i in range(6)]
    timestamped = [{"id": f"t{i}", "last_verified": f"{FAR_DATE}T00:00:00Z"} for i in range(6)]
    repo = _git_repo_at(tmp_path, plain + timestamped, committed_baseline={FAR_DATE: 12})
    counts, skipped = gate._cpa_deadlines_verification_date_counts({"records": plain + timestamped})
    assert skipped == 0
    assert counts[FAR_DATE] == 12
    _set_working_tree(repo, plain + timestamped + [{"id": "extra", "last_verified": FAR_DATE}])  # grows 12 -> 13
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 1
    assert "STALE27-RATCHET" in errors[0]
    assert "is 13, above its recorded ceiling of 12" in errors[0]


def test_multiple_dates_each_evaluated_independently(tmp_path):
    committed = _records(10, FAR_DATE) + _records(11, IMMINENT_DATE)
    repo = _git_repo_at(tmp_path, committed, committed_baseline={IMMINENT_DATE: 11})
    grown_other_date = _records(11, FAR_DATE) + _records(11, IMMINENT_DATE)  # FAR_DATE grew, IMMINENT_DATE unchanged
    _set_working_tree(repo, grown_other_date)
    errors = gate.check_cpa_deadlines_verification_date_concentration(repo)
    assert len(errors) == 2
    joined = "\n".join(errors)
    assert "STALE27-RATCHET" in joined
    assert "STALE27-IMMINENT" in joined
