"""DATE-21: a record tagged fixed_calendar_recurring_no_anchor but with no usable period_years is
not rolled by generate.py, so check_deadline_currency must not skip it.

    python -m pytest scripts/test_date21_tag_without_roll.py -q
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate  # noqa: E402

REPO = Path(HERE).parent


def _run(tmp_path, computation):
    past = (date.today() - timedelta(days=20)).isoformat()
    rec = {"id": "zz-firm", "state_slug": "zz", "next_deadline_computed": past, "computation": computation}
    d = tmp_path / "data"
    d.mkdir()
    p = d / "cpa_deadlines.json"
    p.write_text(json.dumps({"records": [rec]}), encoding="utf-8")
    # run against the real generate.py
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    return preship_gate.check_deadline_currency(p)


TAG = "fixed_calendar_recurring_no_anchor"


def test_tagged_with_period_is_skipped(tmp_path):
    assert _run(tmp_path, {"type": TAG, "period_years": 1}) == []


def test_tagged_without_period_errors(tmp_path):
    for comp in ({"type": TAG}, {"type": TAG, "period_years": None}, {"type": TAG, "period_years": 0}):
        sub = tmp_path / str(id(comp))
        sub.mkdir()
        errs = _run(sub, comp)
        assert len(errs) == 1 and "zz-firm" in errs[0], comp


def test_untagged_still_errors(tmp_path):
    assert len(_run(tmp_path, None)) == 1


def test_real_data_clean():
    assert preship_gate.check_deadline_currency(REPO / "data" / "cpa_deadlines.json") == []
