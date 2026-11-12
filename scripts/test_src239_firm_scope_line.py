"""SRC-239: an individual-only state page says firms are out of scope, in the record's own words."""
import json
from pathlib import Path

import pytest

import generate

ROOT = Path(__file__).resolve().parent.parent
SEVEN = ["california", "michigan", "new-york", "ohio", "pennsylvania", "puerto-rico", "texas"]


def _rec(license_type, **kw):
    return {"id": "x", "license_type": license_type, **kw}


def test_individual_only_page_renders_the_records_note_and_board_link():
    html = generate._state_firm_scope_html(
        [_rec("individual", firm_scope_note="A & B.", source_url="https://board.example/")], "Ohio")
    assert "Firms: A &amp; B." in html
    assert 'href="https://board.example/"' in html


def test_non_http_board_url_is_not_linked():
    html = generate._state_firm_scope_html(
        [_rec("individual", firm_scope_note="N.", source_url="javascript:alert(1)")], "Ohio")
    assert "javascript:" not in html


def test_individual_only_state_without_a_note_fails_the_build():
    with pytest.raises(ValueError, match="SRC-239"):
        generate._state_firm_scope_html([_rec("individual")], "Ohio")


def test_page_with_any_firm_or_all_record_gets_none():
    assert generate._state_firm_scope_html([_rec("individual"), _rec("firm")], "X") == ""
    assert generate._state_firm_scope_html([_rec("all")], "X") == ""
    # Connecticut's non-standard pair covers firms, so it must not get the line.
    assert generate._state_firm_scope_html([_rec("individual_cpa"), _rec("cpa_firm")], "X") == ""
    assert generate._state_firm_scope_html([], "X") == ""


@pytest.mark.parametrize("rel", ["data/cpa_deadlines.json", "worker/src/cpa_deadlines.json"])
def test_shipped_population_is_the_seven_and_each_page_carries_one_line(rel):
    data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    by = {}
    for r in data["records"]:
        by.setdefault(r["state_slug"], []).append(r)
    hit = sorted(s for s, rs in by.items() if generate._state_firm_scope_html(rs, s))
    assert hit == SEVEN
    assert sorted(r["state_slug"] for r in data["records"] if r.get("firm_scope_note")) == SEVEN
    for slug in hit:
        note = by[slug][0]["firm_scope_note"]
        assert "not tracked" in note or "isn't tracked" in note
        assert "coming soon" not in note.lower()
        page = (ROOT / "docs" / slug / "index.html").read_text(encoding="utf-8")
        assert page.count('class="disclosure firm-scope"') == 1, slug


@pytest.mark.parametrize("slug", SEVEN)
def test_faq_jsonld_cycle_answer_carries_the_scope_line(slug):
    import re
    data = json.loads((ROOT / "data/cpa_deadlines.json").read_text(encoding="utf-8"))
    note = next(r["firm_scope_note"] for r in data["records"] if r["state_slug"] == slug)
    page = (ROOT / "docs" / slug / "index.html").read_text(encoding="utf-8")
    answers = []
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        d = json.loads(block)
        for q in d.get("mainEntity", []) if isinstance(d, dict) else []:
            if q["name"].startswith("How does"):
                answers.append(q["acceptedAnswer"]["text"])
    assert len(answers) == 1, slug
    assert answers[0].endswith(note), slug


def test_faq_control_state_with_a_firm_record_has_no_scope_line():
    page = (ROOT / "docs" / "west-virginia" / "index.html").read_text(encoding="utf-8")
    assert "isn't tracked here yet" not in page and "isn&#x27;t tracked here yet" not in page
