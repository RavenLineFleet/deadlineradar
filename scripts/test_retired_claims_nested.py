"""GATE-144 (AuditLab, 2026-11-22): the retired-claims guard must see str
values nested in list/dict record fields, not only top-level strings.

    python -m pytest scripts/test_retired_claims_nested.py -q
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

FILES = ("cpa_deadlines.json", "cpe_hours.json", "reinstatement.json", "renewal_fees.json")


def _phrase(rid):
    return next(p for r, p, _ in gate._RETIRED_CLAIMS if r == rid)


def _run(tmp_path, mutate):
    (tmp_path / "data").mkdir()
    for f in FILES:
        d = json.loads((REPO_ROOT / "data" / f).read_text(encoding="utf-8"))
        for r in d["records"]:
            mutate(r)
        (tmp_path / "data" / f).write_text(json.dumps(d), encoding="utf-8")
    return gate.check_retired_claims_absent(tmp_path)


def test_baseline_clean():
    assert gate.check_retired_claims_absent(REPO_ROOT) == []


def test_list_field_caught(tmp_path):
    ph = _phrase("oh-individual")

    def m(r):
        if r.get("id") == "oh-individual":
            r["cohort_groups"][0]["group"] += f" ({ph})"
    errs = _run(tmp_path, m)
    assert len(errs) == 1 and ".cohort_groups[0].group" in errs[0]


def test_dict_field_caught(tmp_path):
    ph = _phrase("co-firm")

    def m(r):
        if r.get("id") == "co-firm":
            r["computation"]["_probe"] = {"x": [ph]}
    errs = _run(tmp_path, m)
    assert len(errs) == 1 and ".computation._probe.x[0]" in errs[0]


def test_skip_field_honored_when_nested(tmp_path):
    ph = _phrase("oh-individual")

    def m(r):
        if r.get("id") == "oh-individual":
            r["cohort_groups"][0]["verification_note"] = ph
    assert _run(tmp_path, m) == []
