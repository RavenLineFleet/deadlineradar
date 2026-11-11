"""SRC-233: the state-page citation chip must reach every document a record's
citation_links names, not just citation_url."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import generate  # noqa: E402

REC = {
    "id": "x",
    "citation": "Rule A (expiry); Rule B (deadline)",
    "citation_url": "https://example.gov/b",
    "citation_links": [
        {"url": "https://example.gov/a", "label": "Rule A text"},
        {"url": "https://example.gov/b", "label": "Rule B text"},
    ],
}


def test_chip_links_every_citation_link():
    html = generate._cite_chip_html(REC)
    assert html.count('href="https://example.gov/a"') == 1
    assert html.count('href="https://example.gov/b"') == 1  # citation_url once, not duplicated
    assert "Rule A text" in html


def test_teaser_keeps_one_chip():
    html = generate._cite_chip_html(REC, max_chars=40)
    assert 'href="https://example.gov/a"' not in html
    assert html.count('class="cite"') == 1


def test_no_links_unchanged():
    rec = {k: v for k, v in REC.items() if k != "citation_links"}
    assert generate._cite_chip_html(rec).count('class="cite"') == 1


def test_src233_records_reach_their_named_documents():
    recs = {r["id"]: r for r in json.loads((ROOT / "data/cpa_deadlines.json").read_text(encoding="utf-8"))["records"]}
    want = {
        "al-all": "api/chapter/30-X-5",
        "ak-individual": "CPARegulations.pdf",
        "ak-firm": "CPARegulations.pdf",
        "guam-individual": "25GAR002.pdf",
        "guam-firm": "25GAR002.pdf",
        "fl-firm": "renewing-and-maintaining-your-license",
        "il-firm": "068014200000800R.html",
    }
    for rid, frag in want.items():
        assert frag in generate._cite_chip_html(recs[rid]), rid
    for rid in ("sc-firm", "sc-individual"):
        assert recs[rid]["citation_url"] == "https://www.scstatehouse.gov/code/t40c002.php", rid
