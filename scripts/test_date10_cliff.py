"""DATE-10: the 2026-12-31 / 2027-01-01 hand-maintained deadlines must self-roll, not trip the build refusal.

    python -m pytest scripts/test_date10_cliff.py -q
"""
import copy
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(HERE).parent
sys.path.insert(0, str(REPO))
import generate as g  # noqa: E402

DATA = REPO / "data" / "cpa_deadlines.json"
WORKER = REPO / "worker" / "src" / "cpa_deadlines.json"

ANNUAL = {
    "mn-individual", "mn-firm", "ct-individual", "ct-firm", "la-individual", "la-firm", "ks-firm",
    "nv-individual", "ar-individual", "ar-firm", "mt-all", "wy-individual", "wy-firm",
    "ms-individual", "ms-firm", "al-all",
}
BIENNIAL = {"dc-all", "ut-all", "md-firm"}
FEB_WALL = {"nv-firm", "sc-firm", "sc-individual"}  # next wall: 2027-02-01 / 02-02


def _records():
    return json.loads(DATA.read_text(encoding="utf-8"))["records"]


def _stale(recs, today):
    out = []
    for r in recs:
        v = r.get("next_deadline_computed")
        if not v:
            continue
        v = g._roll_forward_recurring_deadline(v, r.get("computation"), today)
        if date.fromisoformat(v) < today:
            out.append(r["id"])
    return out


def test_data_and_worker_copies_identical():
    assert DATA.read_bytes() == WORKER.read_bytes()


def test_cliff_records_are_tagged_with_the_right_period():
    by = {r["id"]: r for r in _records()}
    for i in ANNUAL | BIENNIAL | FEB_WALL:
        c = by[i]["computation"]
        assert c["type"] == "fixed_calendar_recurring_no_anchor", i
        assert c["period_years"] == (2 if i in BIENNIAL else 1), i
        mm, dd = c["fixed_date"].split("-")
        assert by[i]["next_deadline_computed"][5:] == f"{mm}-{dd}", i


def test_build_does_not_refuse_across_the_cliff():
    recs = _records()
    for today in ("2026-12-31", "2027-01-01", "2027-01-02", "2027-02-01", "2027-02-02"):
        assert _stale(recs, date.fromisoformat(today)) == [], today


def test_control_untagged_records_do_refuse():
    recs = copy.deepcopy(_records())
    for r in recs:
        if r["id"] in ANNUAL | BIENNIAL | FEB_WALL:
            r.pop("computation")
    assert len(_stale(recs, date(2027, 1, 1))) == 17
    assert len(_stale(recs, date(2027, 1, 2))) == 19
    assert len(_stale(recs, date(2027, 2, 2))) == 22


def test_biennial_records_stay_on_even_years():
    by = {r["id"]: r for r in _records()}
    for i in BIENNIAL:
        for today in ("2027-01-05", "2029-01-05", "2031-01-05", "2033-01-05"):
            rolled = g._roll_forward_recurring_deadline(
                by[i]["next_deadline_computed"], by[i]["computation"], date.fromisoformat(today))
            assert int(rolled[:4]) % 2 == 0, (i, today, rolled)


def test_late_dates_are_not_rolled_into_the_deadline():
    # al-all rolls the Dec 31 filing deadline only; the Sept 30 expiry stays out of the computation
    by = {r["id"]: r for r in _records()}
    assert by["al-all"]["computation"]["fixed_date"] == "12-31"
    assert "NOT the permit expiry" in by["al-all"]["computation"]["note"]


def test_date5_sees_every_tagged_record_on_its_rendered_page(monkeypatch):
    """Positive control: from 2027-02-05 the built docs/ pages (still showing the old year) must be
    flagged for ALL 22 records, with a 'renders X but correct is Y' message -- never 'could not find'
    (al-all's <small> starts with its _DATE_SEMANTICS note, not its cycle_description)."""
    import datetime
    import preship_gate as gate

    class _D(datetime.date):
        @classmethod
        def today(cls):
            return cls(2027, 2, 5)

    monkeypatch.setattr(gate, "date", _D)
    errs = gate.check_self_rolling_dates_rendered_correctly(REPO, DATA)
    assert not [e for e in errs if "could not find" in e], errs
    flagged = {e.split("][")[2].split("]")[0].split("/")[1] for e in errs if "renders" in e}
    assert (ANNUAL | BIENNIAL | FEB_WALL) <= flagged, (ANNUAL | BIENNIAL | FEB_WALL) - flagged
