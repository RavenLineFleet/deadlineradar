"""SRC-257 / SRC-256(c): renewal_rules.json staleness gate, required verified_date,
and one labeled link per source on compound late-renewal cites.

    python -m pytest scripts/test_renewal_rules_currency.py -q
"""
import copy
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = Path(HERE).parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, HERE)
import generate as g  # noqa: E402
import preship_gate as pg  # noqa: E402

DATA = REPO / "data" / "renewal_rules.json"


def _data():
    return json.loads(DATA.read_text(encoding="utf-8"))


def _repo_with(tmp_path, records):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "renewal_rules.json").write_text(
        json.dumps({"records": records}), encoding="utf-8")
    return tmp_path


def test_real_dataset_passes_gate():
    assert pg.check_renewal_rules_currency(REPO) == []


def test_gate_is_wired_into_main():
    src = (REPO / "scripts" / "preship_gate.py").read_text(encoding="utf-8")
    assert src.count("all_errors += check_renewal_rules_currency(repo_root)") == 1


@pytest.mark.parametrize("vd,tag", [
    ((date.today() - timedelta(days=31)).isoformat(), "31d old"),
    ("2026-13-45", "unparseable"),
    (None, "missing entirely"),
])
def test_gate_fails_stale_unparseable_missing(tmp_path, vd, tag):
    r = copy.deepcopy(_data()["records"][0])
    if vd is None:
        r.pop("verified_date")
    else:
        r["verified_date"] = vd
    errs = pg.check_renewal_rules_currency(_repo_with(tmp_path, [r]))
    assert len(errs) == 1 and "[SRC-257]" in errs[0] and tag in errs[0]


def test_gate_boundary_day_30_still_fresh(tmp_path):
    r = copy.deepcopy(_data()["records"][0])
    r["verified_date"] = (date.today() - timedelta(days=30)).isoformat()
    assert pg.check_renewal_rules_currency(_repo_with(tmp_path, [r])) == []


def test_missing_data_file_is_an_error(tmp_path):
    errs = pg.check_renewal_rules_currency(tmp_path)
    assert len(errs) == 1 and "[GATE-19]" in errs[0]


def test_loader_refuses_missing_verified_date(tmp_path, monkeypatch):
    d = _data()
    d["records"][0].pop("verified_date")
    p = tmp_path / "renewal_rules.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(g, "RENEWAL_RULES_DATA_PATH", p)
    with pytest.raises(ValueError, match="missing verified_date"):
        g.load_renewal_rules_by_slug()


def test_compound_late_cites_link_every_source():
    rules = g.load_renewal_rules_by_slug()
    for slug, r in rules.items():
        links = r.get("late_citation_links")
        assert links, f"{slug}: compound late cite has no per-source links"
        _, _, html = g.build_late_renewal_page(r, [], None, date.today())
        for c in links:
            assert f'{c["label"]} &rarr;</a>' in html.replace("&amp;", "&"), (slug, c["label"])
            assert g.http_href(c["url"]) in html, (slug, c["url"])


def test_colorado_statute_limb_is_linked():
    r = g.load_renewal_rules_by_slug()["colorado"]
    _, _, html = g.build_late_renewal_page(r, [], None, date.today())
    assert "crs2024-title-12.pdf" in html
