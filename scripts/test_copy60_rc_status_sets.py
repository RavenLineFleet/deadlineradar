"""COPY-60: every rule_change status is classified explicitly.

ENACTED_DATE_PENDING was missing from _rc_is_enacted, so an enacted PA statute
rendered under "not yet signed into law". The two status sets must partition
build_change_events.VALID_RULE_CHANGE_STATUSES except DIED_WITHDRAWN, which has
no honest bucket and must fail the build instead of landing in "not yet final".

    python -m pytest scripts/test_copy60_rc_status_sets.py -q
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import generate  # noqa: E402
from build_change_events import VALID_RULE_CHANGE_STATUSES  # noqa: E402


def test_sets_disjoint_and_cover_valid_statuses_except_died():
    enacted = set(generate._RC_ENACTED_STATUSES)
    nyf = set(generate._RC_NOT_YET_FINAL_STATUSES)
    assert not (enacted & nyf)
    assert enacted | nyf == set(VALID_RULE_CHANGE_STATUSES) - {"DIED_WITHDRAWN"}


def test_enacted_date_pending_is_enacted():
    assert generate._rc_is_enacted({"status": "ENACTED_DATE_PENDING"})
    assert not generate._rc_is_not_yet_final({"status": "ENACTED_DATE_PENDING"})


@pytest.mark.parametrize("status", ["DIED_WITHDRAWN", None, "enacted"])
def test_unbucketable_status_fails_build(status):
    with pytest.raises(ValueError):
        generate._rc_assert_bucketable([{"kind": "rule_change", "event_id": "x", "status": status}])


def test_source_conflicts_ignored_by_guard():
    generate._rc_assert_bucketable([{"kind": "source_conflict", "status": "SOURCE_CONFLICT"}])


def test_js_mirror_matches():
    src = open(os.path.join(ROOT, "generate.py"), encoding="utf-8").read()
    i = src.index("function drRuleChangeIsEnacted(e)")
    body = src[i:src.index("}", i)]
    for st in generate._RC_ENACTED_STATUSES:
        assert f"'{st}'" in body, st
