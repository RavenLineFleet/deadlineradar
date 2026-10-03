"""Tests for the /year-end-renewals/ page (AuditLab YE-1 through YE-5,
Orchestrator rulings, 2026-10-02).

    python -m pytest scripts/test_year_end_page.py -q

    1. live build, real data: exactly the 12-state shape AuditLab verified
       (9 both, Nevada individual-only, KS/MD firm-only)              -> PASS
    2. KS/MD individual cells use cycle_description text, never the raw
       renewal_pattern token                                          -> PASS
    3. before the target date: page is indexable, in the sitemap        -> PASS
    4. after the target date: "passed" notice, noindex,follow, dropped
       from the sitemap, and check_noindex_set_matches_intent expects
       exactly that                                                    -> PASS
    5. check_year_end_page_no_raw_enum_tokens catches a real leaked
       token (positive control, not just absence of a finding)         -> PASS
"""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, str(REPO_ROOT))
import preship_gate as gate  # noqa: E402
import generate  # noqa: E402


def _load_by_slug():
    import json
    with open(os.path.join(REPO_ROOT, "data", "cpa_deadlines.json"), encoding="utf-8") as f:
        raw = json.load(f)
    by_slug: dict[str, list[dict]] = {}
    for r in raw["records"]:
        by_slug.setdefault(r["state_slug"], []).append(r)
    return by_slug


# --- 1. live build, real data -------------------------------------------------

def test_1_dec31_cohort_is_the_verified_12_state_shape():
    by_slug = _load_by_slug()
    rows = generate._year_end_dec31_rows(by_slug)
    slugs = {r["state_slug"] for r in rows}
    assert slugs == {
        "alabama", "arkansas", "connecticut", "dc", "kansas", "louisiana", "maryland",
        "minnesota", "montana", "nevada", "utah", "wyoming",
    }, f"state set drifted from AuditLab's verified 12: {sorted(slugs)}"

    by_slug_row = {r["state_slug"]: r for r in rows}
    # 9 "both" states: individual and firm both land on the target date.
    for slug in ("alabama", "arkansas", "connecticut", "dc", "louisiana", "minnesota", "montana", "utah", "wyoming"):
        row = by_slug_row[slug]
        assert row["individual_date"] == "2026-12-31", slug
        assert row["firm_date"] == "2026-12-31", slug

    # Nevada: individual on the target date, firm is a DIFFERENT date (not null).
    nevada = by_slug_row["nevada"]
    assert nevada["individual_date"] == "2026-12-31"
    assert nevada["firm_date"] == "2027-01-31"

    # Kansas/Maryland: firm-only, individual has no computed date at all.
    for slug in ("kansas", "maryland"):
        row = by_slug_row[slug]
        assert row["individual_date"] is None, slug
        assert row["individual_text"], f"{slug} should have a cycle_description fallback"
        assert row["firm_date"] == "2026-12-31", slug


# --- 2. KS/MD show human text, never the raw token ---------------------------

def test_2_ks_md_cells_never_show_the_raw_enum_token():
    by_slug = _load_by_slug()
    rows = generate._year_end_dec31_rows(by_slug)
    by_slug_row = {r["state_slug"]: r for r in rows}

    assert by_slug_row["maryland"]["individual_text"] != "other"
    assert "other" not in by_slug_row["maryland"]["individual_text"].split()
    assert by_slug_row["kansas"]["individual_text"] != "license_number_cohort"
    # Both must actually be human prose, not a bare machine token.
    assert len(by_slug_row["maryland"]["individual_text"]) > 20
    assert len(by_slug_row["kansas"]["individual_text"]) > 20


# --- 3. before the target date -------------------------------------------------

def test_3_before_target_date_is_indexable_with_the_real_table():
    by_slug = _load_by_slug()
    html, expired = generate.build_year_end_renewals_page(by_slug, date(2026, 10, 2))
    assert expired is False
    assert 'name="robots"' not in html
    assert "<table>" in html
    assert "Kansas" in html and "Maryland" in html


# --- 4. after the target date --------------------------------------------------

def test_4_after_target_date_is_a_passed_notice_and_noindexed():
    by_slug = _load_by_slug()
    html, expired = generate.build_year_end_renewals_page(by_slug, date(2027, 2, 1))
    assert expired is True
    assert 'content="noindex,follow"' in html
    assert "<table>" not in html
    assert "passed" in html.lower()


def test_4b_sitemap_drops_the_page_once_expired():
    sitemap_live = generate.build_sitemap([], date(2026, 10, 2), year_end_expired=False)
    sitemap_expired = generate.build_sitemap([], date(2026, 10, 2), year_end_expired=True)
    assert f"/{generate.YEAR_END_PAGE_SLUG}/" in sitemap_live
    assert f"/{generate.YEAR_END_PAGE_SLUG}/" not in sitemap_expired


def test_4c_noindex_gate_expects_year_end_page_only_once_expired(tmp_path):
    docs_dir = tmp_path / "docs"
    ye_dir = docs_dir / generate.YEAR_END_PAGE_SLUG
    ye_dir.mkdir(parents=True)

    # A distractor noindexed page (one of the real app/auth allowlist
    # entries) so `actual` isn't empty -- an empty actual set trips the
    # function's own "measuring nothing" guard before ever reaching the
    # missing/extra comparison this test is for.
    distractor_dir = docs_dir / "my"
    distractor_dir.mkdir(parents=True)
    (distractor_dir / "index.html").write_text(
        '<html><head><meta name="robots" content="noindex"></head><body>x</body></html>', encoding="utf-8"
    )

    # Before the cutoff: page is indexable (no noindex tag) -- expected set
    # must NOT include it, and the real built page must agree.
    (ye_dir / "index.html").write_text("<html><head></head><body>table</body></html>", encoding="utf-8")
    html_files = list(docs_dir.rglob("index.html"))
    errors_before = gate.check_noindex_set_matches_intent(
        html_files, docs_dir, REPO_ROOT, today=date(2026, 10, 2)
    )
    ye_errors_before = [e for e in errors_before if generate.YEAR_END_PAGE_SLUG in e]
    assert ye_errors_before == [], f"page is correctly indexable before the cutoff: {ye_errors_before}"

    # After the cutoff: page must be noindexed to match the gate's
    # expectation -- write it WITHOUT noindex to prove the gate catches
    # the mismatch (the real build always adds it, per test 4 above).
    errors_after_missing_noindex = gate.check_noindex_set_matches_intent(
        html_files, docs_dir, REPO_ROOT, today=date(2027, 2, 1)
    )
    assert any(
        f"/{generate.YEAR_END_PAGE_SLUG}/" in e and "expected to be noindexed but are not" in e
        for e in errors_after_missing_noindex
    ), errors_after_missing_noindex

    # Now add the noindex tag the real builder would have added -- clean.
    (ye_dir / "index.html").write_text(
        '<html><head><meta name="robots" content="noindex,follow"></head><body>passed</body></html>',
        encoding="utf-8",
    )
    errors_after_fixed = gate.check_noindex_set_matches_intent(
        html_files, docs_dir, REPO_ROOT, today=date(2027, 2, 1)
    )
    ye_errors_after_fixed = [e for e in errors_after_fixed if generate.YEAR_END_PAGE_SLUG in e]
    assert ye_errors_after_fixed == [], ye_errors_after_fixed


# --- 5. raw-enum-token gate, positive control --------------------------------

def test_5_raw_enum_token_gate_catches_a_real_leak(tmp_path):
    docs_dir = tmp_path / "docs"
    ye_dir = docs_dir / generate.YEAR_END_PAGE_SLUG
    ye_dir.mkdir(parents=True)

    clean_html = "<html><body><td>Biennial, see the board's page.</td></body></html>"
    (ye_dir / "index.html").write_text(clean_html, encoding="utf-8")
    clean_errors = gate.check_year_end_page_no_raw_enum_tokens(docs_dir, REPO_ROOT)
    assert clean_errors == [], clean_errors

    leaked_html = "<html><body><td>other</td></body></html>"
    (ye_dir / "index.html").write_text(leaked_html, encoding="utf-8")
    leaked_errors = gate.check_year_end_page_no_raw_enum_tokens(docs_dir, REPO_ROOT)
    assert any("'other'" in e for e in leaked_errors), leaked_errors


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
