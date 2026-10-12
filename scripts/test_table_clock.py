"""TABLE-2 (AuditLab, 2026-10-12): month-end table cells track the wall clock, not as_of_date.

    python -m pytest scripts/test_table_clock.py -q

as_of_date is the OLDEST record last_verified (STALE-27) and trails today, so a table built from it
re-rendered an elapsed month-end cell after the month ended. generate.table_clock() is the one place the
render paths take their clock from; these tests pin the five call sites by their rendered output.
"""
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, str(REPO_ROOT))
import generate  # noqa: E402

AS_OF = date(2026, 10, 2)
_REC = {r["id"]: r for r in json.load(open(REPO_ROOT / "data" / "cpa_deadlines.json", encoding="utf-8"))["records"]}


def _ca():
    return generate.render_california(_REC["ca-individual"], AS_OF)


def _tx():
    return generate.render_texas(_REC["tx-individual"], AS_OF)


class _FrozenDT(datetime):
    frozen = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen.astimezone(tz) if tz else cls.frozen


@pytest.fixture
def nov1(monkeypatch):
    monkeypatch.setattr(generate, "datetime", _FrozenDT)


def test_table_clock_never_earlier_than_as_of(monkeypatch):
    monkeypatch.setattr(generate, "datetime", _FrozenDT)
    assert generate.table_clock(date(2027, 1, 1)) == date(2027, 1, 1)
    assert generate.table_clock(AS_OF) == date(2026, 11, 1)


def test_california_and_texas_pages_roll_past_the_elapsed_october_cell(nov1):
    ca = _ca()
    tx = _tx()
    assert "October 31, 2026" not in ca and "October 31, 2026" not in tx
    assert "October 31, 2028" in ca        # even-birth-year parity row, next occurrence after 2026-11-01
    assert "October 31, 2027" in tx        # annual month-end row


def test_generic_pair_and_calculator_tables_use_the_same_clock(nov1):
    assert [r["next_deadline"] for r in generate.build_birth_month_annual_table(AS_OF)][9] == generate.fmt_date(date(2027, 10, 31))
    par = generate.build_birth_month_year_parity_table(AS_OF)[9]
    assert "October 31, 2026" not in json.dumps(par)


def test_before_the_month_end_the_cell_is_unchanged(monkeypatch):
    class Oct(_FrozenDT):
        frozen = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(generate, "datetime", Oct)
    assert "October 31, 2026" in _tx()   # "due today has not passed"


def test_deadline_calculator_widget_tables_use_the_same_clock(nov1):
    by_slug = {}
    for r in _REC.values():
        by_slug.setdefault(r["state_slug"], []).append(r)
    blob = json.dumps(generate._deadline_calculator_widget_data(by_slug, AS_OF))
    assert "October 31, 2026" not in blob and "October 31, 2027" in blob


# ---- TABLE-3 (AuditLab, 2026-10-12): the parity-lookup table (KS/KY/OR/NE) is a 5th clocked site, and TABLE-1
# must be able to SEE its rows (data-parity), which its old data-month-only regex never matched.

class _Jun(_FrozenDT):
    frozen = datetime(2027, 7, 2, 12, 0, tzinfo=timezone.utc)


def test_parity_lookup_table_tracks_the_clock(monkeypatch):
    rec = _REC["or-individual"]
    assert "June 30, 2027" in generate.render_parity_lookup_state("oregon", rec, AS_OF)   # today: odd row is future
    monkeypatch.setattr(generate, "datetime", _Jun)
    html = generate.render_parity_lookup_state("oregon", rec, AS_OF)
    assert "June 30, 2027" not in html and "June 30, 2029" in html


def _gate(monkeypatch, today):
    import preship_gate as pg

    class D(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(pg, "date", D)
    return pg


def test_table1_now_sees_data_parity_rows(monkeypatch, tmp_path):
    sys.path.insert(0, os.path.join(str(REPO_ROOT), "scripts"))
    f = tmp_path / "p.html"
    f.write_text('<tr data-parity="odd"><td>Odd</td><td>June 30, 2027</td></tr>'
                 '<tr data-parity="even"><td>Even</td><td>June 30, 2028</td></tr>', encoding="utf-8")
    pg = _gate(monkeypatch, date(2027, 7, 2))
    errs = pg.check_birth_month_table_currency([f])
    assert len(errs) == 1 and "June 30, 2027" in errs[0] and "parity" in errs[0]
    assert _gate(monkeypatch, date(2027, 6, 30)).check_birth_month_table_currency([f]) == []   # due today has not passed


def test_table1_still_sees_data_month_rows(monkeypatch, tmp_path):
    sys.path.insert(0, os.path.join(str(REPO_ROOT), "scripts"))
    f = tmp_path / "m.html"
    f.write_text('<tr data-month="10"><td>October</td><td>October 31, 2026</td></tr>', encoding="utf-8")
    assert len(_gate(monkeypatch, date(2026, 11, 1)).check_birth_month_table_currency([f])) == 1
