"""BCC-1 (AssetLab 2026-11-20, AuditLab AGREE 20:33:43Z with 2 amendments): a
fee_basis='board_confirmed_correspondence' row must name its correspondence date
(amendment 2: the label was assertable for free), and an optional basis_valid_through
(the Board-named period) hard-fails once passed. Absent basis_valid_through is legal
(amendment 1: northern-mariana-islands has this basis and no Board-named period).

    python -m pytest scripts/test_bcc1_fee_basis_correspondence.py -q
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).parent
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

TODAY = date(2026, 11, 20)


def _bcc(**kw):
    r = {"id": "x-renewal-fee", "fee_basis": "board_confirmed_correspondence", "fee_usd": 66,
         "citation_url": "https://example.test/law.pdf", "basis_correspondence_date": "2026-09-09"}
    r.update(kw)
    return {k: v for k, v in r.items() if v is not None}


def _run(tmp_path, *records, today=TODAY):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "renewal_fees.json").write_text(json.dumps({"records": list(records)}), encoding="utf-8")
    return gate.check_fee_basis_supported(tmp_path, today=today)


def test_real_tree_is_clean_and_both_bcc_rows_name_their_correspondence():
    assert gate.check_fee_basis_supported(ROOT, today=TODAY) == []
    rows = [r for r in json.loads((ROOT / "data" / "renewal_fees.json").read_text(encoding="utf-8"))["records"]
            if r.get("fee_basis") == "board_confirmed_correspondence"]
    assert {r["id"] for r in rows} == {"colorado-renewal-fee", "northern-mariana-islands-renewal-fee"}
    assert all(r.get("basis_correspondence_date") for r in rows)


def test_real_colorado_row_walls_the_day_after_the_board_named_period_ends():
    assert gate.check_fee_basis_supported(ROOT, today=date(2027, 11, 30)) == []
    errs = gate.check_fee_basis_supported(ROOT, today=date(2027, 12, 1))
    assert len(errs) == 1 and "colorado-renewal-fee" in errs[0] and "ended 2027-11-30" in errs[0], errs


def test_amendment2_bcc_label_without_a_correspondence_date_fails(tmp_path):
    errs = _run(tmp_path, _bcc(basis_correspondence_date=None))
    assert len(errs) == 1 and "basis_correspondence_date is missing" in errs[0], errs


def test_amendment1_bcc_row_without_valid_through_is_legal(tmp_path):
    assert _run(tmp_path, _bcc()) == []


def test_valid_through_passed_fails_and_on_the_day_passes(tmp_path):
    assert _run(tmp_path, _bcc(basis_valid_through="2026-11-20")) == []
    errs = _run(tmp_path, _bcc(basis_valid_through="2026-11-19"))
    assert len(errs) == 1 and "ended 2026-11-19" in errs[0], errs


def test_malformed_and_inconsistent_dates_fail(tmp_path):
    assert any("not YYYY-MM-DD" in e for e in _run(tmp_path, _bcc(basis_correspondence_date="09/09/2026")))
    assert any("not YYYY-MM-DD" in e for e in _run(tmp_path, _bcc(basis_valid_through="2027-13-01")))
    assert any("in the future" in e for e in _run(tmp_path, _bcc(basis_correspondence_date="2026-11-21")))


def test_bcc_fields_on_another_basis_fail(tmp_path):
    row = {"id": "y-renewal-fee", "fee_basis": "codified", "citation_url": "https://example.test/r", "basis_valid_through": "2027-11-30"}
    errs = _run(tmp_path, row)
    assert len(errs) == 1 and "only meaningful" in errs[0], errs
