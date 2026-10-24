"""Tests for SEC-39 (SecurityLab, AuditLab-extended, 2026-10-24): the seat-cap
arms of check_pricing_matches_tiers named 4 of 11 top/mid-tier copies, and
never read NEW_SIGNUP_FREE_SEAT_CAP at all, so a free-cap drift was silent.

    python -m pytest scripts/test_seat_cap_parity_gate.py -q
"""
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

REPO = Path(HERE).parent


def _fixture(tmp_path, ts_fn=lambda t: t, py_fn=lambda t: t, i18n_fn=lambda t: t):
    (tmp_path / "worker" / "src").mkdir(parents=True)
    for rel, fn in (("generate.py", py_fn), ("i18n.py", i18n_fn), ("worker/src/tiers.ts", ts_fn)):
        (tmp_path / rel).write_text(fn((REPO / rel).read_text(encoding="utf-8")), encoding="utf-8")
    return gate.check_pricing_matches_tiers(tmp_path)


def _sub(*pairs):
    def fn(text):
        for old, new in pairs:
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        return text
    return fn


def _resub(*pairs):
    # (pattern, replacement, expected match count) -- asserts each landed.
    def fn(text):
        for pat, repl, n in pairs:
            text, got = re.subn(pat, repl, text)
            assert got == n, (pat, got)
        return text
    return fn


_STAFF_WORDED_PY = (
    (r"DR_FREE_STAFF_CAP = 3;", "DR_FREE_STAFF_CAP = 4;", 1),
    (r"Free plan \(up to 3 staff\)", "Free plan (up to 4 staff)", 1),
    (r"free up to 3 staff\.", "free up to 4 staff.", 1),
    (r"up to 3 staff</strong>, no card required", "up to 4 staff</strong>, no card required", 2),
    (r"above 3 staff then, you choose which 3 stay", "above 4 staff then, you choose which 4 stay", 1),
)
_PEOPLE_WORDED_PY = ((r"free for up to 3 people", "free for up to 4 people", 3),)
_STAFF_WORDED_I18N = (
    (r"up to 3 staff</strong>, no card required", "up to 4 staff</strong>, no card required", 1),
    (r"above 3 staff, you choose which 3 stay", "above 4 staff, you choose which 4 stay", 1),
    (r'"pricing\.row_roster_free": "Up to 3 staff"', '"pricing.row_roster_free": "Up to 4 staff"', 1),
    (r"up to 3 staff, no card and no time limit", "up to 4 staff, no card and no time limit", 1),
)


def test_clean_as_committed(tmp_path):
    assert _fixture(tmp_path) == []


def test_top_cap_drift_with_previously_named_surfaces_patched_is_not_green(tmp_path):
    # SecurityLab's Round 2: fixing exactly the 4 surfaces the old gate named.
    errors = _fixture(
        tmp_path,
        ts_fn=_sub(("seatCap: 35 }", "seatCap: 40 }")),
        py_fn=_sub(("seatCap: 35", "seatCap: 40"), ('"seat_cap": 35', '"seat_cap": 40'),
                   ("(up to 35 staff)", "(up to 40 staff)"), ("up to 35 staff</div>", "up to 40 staff</div>")),
    )
    joined = "\n".join(errors)
    assert "More than (\\d+) staff" in joined
    assert "From (\\d+) to (\\d+) staff" in joined
    assert "includes the first" in joined
    assert "Enterprise" in joined  # faq.firm.4_a


def test_lowest_cap_drift_hits_faq_and_roster_row(tmp_path):
    errors = _fixture(
        tmp_path,
        ts_fn=_sub(("seatCap: 5 }", "seatCap: 6 }")),
        py_fn=_sub(("seatCap: 5,", "seatCap: 6,"), ('"seat_cap": 5,', '"seat_cap": 6,'),
                   ("(up to 5 staff)", "(up to 6 staff)"), ("up to 5 staff</div>", "up to 6 staff</div>")),
    )
    joined = "\n".join(errors)
    assert "Essentials" in joined
    assert "From (\\d+) to (\\d+) staff" in joined


def test_free_cap_drift_is_not_silent(tmp_path):
    errors = _fixture(tmp_path, ts_fn=_sub(("NEW_SIGNUP_FREE_SEAT_CAP = 3;", "NEW_SIGNUP_FREE_SEAT_CAP = 4;")))
    # 12 surfaces; generate.py's trial-end paragraph carries 2 phrasings.
    assert len(errors) == 13, errors


def test_consistent_free_cap_change_is_clean(tmp_path):
    # Positive control: all 12 free-cap copies moved with tiers.ts -> GREEN.
    errors = _fixture(
        tmp_path,
        ts_fn=_sub(("NEW_SIGNUP_FREE_SEAT_CAP = 3;", "NEW_SIGNUP_FREE_SEAT_CAP = 4;")),
        py_fn=_resub(*(_STAFF_WORDED_PY + _PEOPLE_WORDED_PY)),
        i18n_fn=_resub(*_STAFF_WORDED_I18N),
    )
    assert errors == []


def test_free_cap_drift_with_only_staff_worded_copy_patched_is_not_green(tmp_path):
    # SecurityLab's ROUND2 on SEC-39: the 9 staff-worded copies moved, the
    # 3 "up to N people" copies left stale.
    errors = _fixture(
        tmp_path,
        ts_fn=_sub(("NEW_SIGNUP_FREE_SEAT_CAP = 3;", "NEW_SIGNUP_FREE_SEAT_CAP = 4;")),
        py_fn=_resub(*_STAFF_WORDED_PY),
        i18n_fn=_resub(*_STAFF_WORDED_I18N),
    )
    assert len(errors) == 3, errors
    assert all("free for up to" in e for e in errors)


def test_rephrased_copy_is_not_silent(tmp_path):
    errors = _fixture(tmp_path, i18n_fn=_sub(('"pricing.card_more_title": "More than 35 staff?"',
                                              '"pricing.card_more_title": "Over 35 staff?"')))
    assert any("Could not find the seat-cap copy" in e and "i18n.py" in e for e in errors)


def test_free_cap_constant_missing_is_not_silent(tmp_path):
    errors = _fixture(tmp_path, ts_fn=_sub(("NEW_SIGNUP_FREE_SEAT_CAP = 3;", "NEW_SIGNUP_FREE_SEAT_CAP: number = 3;")))
    assert any("NEW_SIGNUP_FREE_SEAT_CAP numeric literal" in e for e in errors)
