"""SRC-239: an individual-only state page says firms are out of scope, in the record's own words."""
import json
from pathlib import Path

import pytest

import generate

ROOT = Path(__file__).resolve().parent.parent
# SRC-239(c): California left the set when ca-firm shipped; Texas is next.
# SRC-243 (AuditLab 2026-11-12): North Carolina was always in it. nc-all is license_type
# "all" but covers only individual licensees (active + retired), so it is individual-only.
SEVEN = ["michigan", "new-york", "north-carolina", "ohio", "pennsylvania", "puerto-rico", "texas"]
FIRM_ALL_LABEL = "Individual CPA/PA certificate and firm permit"


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
    assert generate._state_firm_scope_html([_rec("all", license_type_label=FIRM_ALL_LABEL)], "X") == ""
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


def test_firm_page_url_is_linked_over_the_board_site():
    html = generate._state_firm_scope_html(
        [_rec("individual", firm_scope_note="N.", source_url="https://board.example/",
              firm_scope_url="https://board.example/firms")], "Ohio")
    assert 'href="https://board.example/firms"' in html and "board's page for firms" in html
    assert 'href="https://board.example/"' not in html


TOPIC_PAGES = [f"{s}-cpa-{t}" for s in SEVEN if s != "puerto-rico"
               for t in ("cpe-requirements", "license-reinstatement")]


@pytest.mark.parametrize("page", TOPIC_PAGES)
def test_topic_pages_of_the_seven_carry_one_scope_line(page):
    html = (ROOT / "docs" / page / "index.html").read_text(encoding="utf-8")
    assert html.count('class="disclosure firm-scope"') == 1, page


def test_scope_line_appears_on_exactly_the_7_state_and_12_topic_pages():
    hits = sorted(p.parent.name for p in (ROOT / "docs").rglob("index.html")
                  if 'class="disclosure firm-scope"' in p.read_text(encoding="utf-8"))
    assert hits == sorted(SEVEN + TOPIC_PAGES)


def test_each_firm_scope_url_is_https_and_differs_from_board_site_except_pa():
    data = json.loads((ROOT / "data/cpa_deadlines.json").read_text(encoding="utf-8"))
    urls = {r["state_slug"]: r.get("firm_scope_url") for r in data["records"] if r.get("firm_scope_note")}
    assert urls["puerto-rico"] is None
    assert all(u.startswith("https://") for s, u in urls.items() if s != "puerto-rico")


COMPUTED = {"north-carolina", "ohio", "pennsylvania", "puerto-rico", "texas"}


def _add_form_groups():
    import re
    html = (ROOT / "docs" / "firm-dashboard" / "index.html").read_text(encoding="utf-8")
    form = html[html.index('id="dr-add-staff-form"'):]
    form = form[:form.index('id="dr-add-fee"')]
    parts = re.split(r'<div class="signup-extra-fields" data-for-state="([a-z-]+)" hidden>', form)
    return dict(zip(parts[1::2], parts[2::2])), html


def test_add_staff_form_hint_on_exactly_the_seven():
    groups, html = _add_form_groups()
    assert html.count('class="field-hint dr-firm-scope-hint"') == len(SEVEN)
    assert sorted(s for s, b in groups.items() if "dr-firm-scope-hint" in b) == SEVEN


@pytest.mark.parametrize("slug", SEVEN)
def test_add_staff_form_hint_wording_follows_computed_vs_typed(slug):
    import re
    groups, _ = _add_form_groups()
    hint = re.search(r'<p class="field-hint dr-firm-scope-hint">(.*?)</p>', groups[slug], re.S).group(1)
    assert "Firm permits for" in hint and "are not tracked here yet." in hint
    if slug in COMPUTED:
        assert "this computes the individual CPA license date" in hint
    else:
        assert "computes" not in hint and "date you enter" in hint


def test_add_form_hint_skips_states_that_cover_firms():
    assert generate._firm_add_form_scope_hint_html("x", [_rec("individual"), _rec("firm")]) == ""
    assert generate._firm_add_form_scope_hint_html(
        "x", [_rec("all", license_type_label=FIRM_ALL_LABEL, firm_scope_note="N.")]) == ""
    assert generate._firm_add_form_scope_hint_html("x", []) == ""


def test_california_page_has_no_firm_scope_line_now_that_ca_firm_ships():
    page = (ROOT / "docs" / "california" / "index.html").read_text(encoding="utf-8")
    assert 'class="disclosure firm-scope"' not in page
    # the scope sentence itself, not the fee caveat ("the firm permit renewal fee isn't tracked here yet"), which is true
    for apos in ("'", "&#x27;", "&#39;"):
        assert f"renewal for California isn{apos}t tracked here yet" not in page
    assert "Firm permits for California are not tracked" not in page


def test_california_add_form_offers_the_firm_permit_and_no_scope_hint():
    groups, _ = _add_form_groups()
    block = groups["california"]
    assert "dr-firm-scope-hint" not in block
    assert 'value="ca-firm"' in block and 'value="ca-individual"' in block
    assert 'name="permit_month"' in block and 'name="permit_year"' in block


def test_an_all_record_covers_firms_only_when_its_label_names_the_firm():
    """SRC-243/245: nc-all's label is "CPA license (all licensees)". The record contains "firm"
    only inside "confirmed", so a substring census over the record would call it firm-covering."""
    assert generate._record_covers_firm(_rec("firm")) and generate._record_covers_firm(_rec("cpa_firm"))
    assert generate._record_covers_firm(_rec("all", license_type_label=FIRM_ALL_LABEL))
    nc_like = _rec("all", license_type_label="CPA license (all licensees)",
                   verification_note="confirmed against the rule; confirm again next cycle")
    assert not generate._record_covers_firm(nc_like)
    with pytest.raises(ValueError, match="SRC-239"):
        generate._state_firm_scope_html([nc_like], "North Carolina")
    assert generate._firm_relevant_record([{**nc_like, "next_deadline_computed": "2027-07-01"}]) is None


def test_no_firm_renewal_page_for_an_individual_only_state():
    """SRC-245: /north-carolina-cpa-firm-renewal/ said "Here's exactly when North Carolina's
    firm-level filing is due" over nc-all's individual July 1 date. The NC board says firm
    registration renewal typically ends December 31."""
    for slug in SEVEN:
        assert not (ROOT / "docs" / f"{slug}-cpa-firm-renewal").exists(), slug
    sitemap = (ROOT / "docs" / "sitemap.xml").read_text(encoding="utf-8")
    for_firms = (ROOT / "docs" / "for-firms" / "index.html").read_text(encoding="utf-8")
    nc = (ROOT / "docs" / "north-carolina" / "index.html").read_text(encoding="utf-8")
    for text in (sitemap, for_firms, nc):
        assert "north-carolina-cpa-firm-renewal" not in text
    # positive control: Alabama's all record names the firm permit, so its page stays
    assert (ROOT / "docs" / "alabama-cpa-firm-renewal" / "index.html").exists()
    assert "alabama-cpa-firm-renewal" in sitemap and "alabama-cpa-firm-renewal" in for_firms
