"""Tests for the CRAWL-8 noindex-set assertion (AuditLab spec, 2026-10-02).

    python -m pytest scripts/test_noindex_gate.py -q

AuditLab proved the gap by simulation, not argument: in a throwaway docs/
copy, adding noindex,follow to /nevada/ (a real core state page) AND
removing it from sitemap.xml produced 0 gate errors, identical to baseline
-- scope #9's exact failure mode ("no accidentally-noindexed production
pages"), reachable by a one-line template edit or a stray slug added to
SEO_NOINDEX_REINSTATEMENT_SLUGS. Case 2 below is that exact simulation,
reproduced as a real regression test: it must fail red before
check_noindex_set_matches_intent exists (or is skipped), and pass once the
assertion runs, because /nevada/ -- unlike nevada-cpa-license-reinstatement
-- is not in the intended noindex set.

    1. happy path: the real 34+5 intended set, nothing else noindexed -> PASS
    2. AuditLab's /nevada/ simulation: a core page silently noindexed +
       dropped from sitemap                                           -> ERROR
    3. a reinstatement page loses its noindex tag (exemption silently
       dropped)                                                        -> ERROR (missing)
    4. slug-set hash mismatch, count still correct                     -> ERROR (isolated)
    5. slug-set count mismatch, hash still "correct" for its own content -> ERROR (isolated)
    6. zero noindexed pages built at all                               -> ERROR (enablement)
    7. GATE-42 (AuditLab, 2026-10-03): 404.html -- a non-index.html page --
       correctly noindexed produces no error, then losing the tag IS caught
       (the before/after pair is what actually pins the fix, not either
       state alone -- see the test's own docstring)              -> clean, then ERROR
    8. GATE-42: a stray non-index.html page gains an unintended noindex -> ERROR (extra)
"""
import hashlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

REAL_SLUGS = frozenset({
    "alaska", "arizona", "arkansas", "colorado", "connecticut", "dc", "delaware",
    "georgia", "idaho", "indiana", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "nebraska",
    "nevada", "new-hampshire", "new-mexico", "north-carolina", "north-dakota",
    "oklahoma", "oregon", "rhode-island", "utah", "virginia", "washington",
    "west-virginia", "wisconsin", "wyoming",
})


def _write_page(docs_dir, rel_path, noindex):
    """rel_path ending in ".html" (e.g. "/404.html") writes that literal file
    (GATE-42: a non-index.html page); anything else is treated as a clean-URL
    directory and writes <rel_path>/index.html, as before."""
    robots = '<meta name="robots" content="noindex,follow">' if noindex else ""
    html = f"<!doctype html><html><head>{robots}</head><body>x</body></html>"
    if rel_path.endswith(".html"):
        f = docs_dir / rel_path.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(html, encoding="utf-8")
        return
    d = docs_dir / rel_path.strip("/")
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(html, encoding="utf-8")


def _write_sitemap(docs_dir, paths):
    locs = "".join(f"<url><loc>https://deadline-radar.com{p}</loc></url>" for p in paths)
    (docs_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0"?><urlset>{locs}</urlset>', encoding="utf-8"
    )


def _build_intended_set(docs_dir, slugs=REAL_SLUGS):
    """The 34 reinstatement pages + 5 app/auth pages, correctly noindexed,
    plus a couple of ordinary indexable pages for realism."""
    for slug in slugs:
        _write_page(docs_dir, f"/{slug}-cpa-license-reinstatement/", noindex=True)
    for p in gate.NOINDEX_APP_AUTH_ALLOWLIST:
        _write_page(docs_dir, p, noindex=True)
    _write_page(docs_dir, "/nevada/", noindex=False)
    _write_page(docs_dir, "/south-carolina-cpa-license-reinstatement/", noindex=False)


def _html_files(docs_dir):
    """GATE-42: matches production's own html_files collection
    (docs_dir.rglob("*.html")) rather than index.html alone, so these tests
    actually exercise non-index.html pages like 404.html."""
    return list(docs_dir.rglob("*.html"))


# --- 1. happy path ------------------------------------------------------------

def test_1_intended_set_only_passes(tmp_path):
    docs_dir = tmp_path / "docs"
    _build_intended_set(docs_dir)
    sitemap_paths = [
        "/nevada/", "/south-carolina-cpa-license-reinstatement/",
    ]
    _write_sitemap(docs_dir, sitemap_paths)
    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert errors == [], f"expected no errors on the correctly-built intended set, got {errors}"


# --- 2. AuditLab's /nevada/ simulation ---------------------------------------

def test_2_nevada_silently_noindexed_and_dropped_is_caught(tmp_path):
    docs_dir = tmp_path / "docs"
    _build_intended_set(docs_dir)
    # The simulated regression: /nevada/ (a core state page, NOT one of the
    # 34 reinstatement slugs) gets noindex,follow AND is dropped from
    # sitemap.xml, exactly as AuditLab's throwaway repro did.
    _write_page(docs_dir, "/nevada/", noindex=True)
    _write_sitemap(docs_dir, ["/south-carolina-cpa-license-reinstatement/"])  # /nevada/ omitted

    # Baseline fact AuditLab proved: the EXISTING sitemap check alone sees
    # nothing wrong, because noindex is treated as a blanket excuse.
    sitemap_errors = gate.check_sitemap_completeness(_html_files(docs_dir), docs_dir)
    assert sitemap_errors == [], (
        f"sanity check failed: check_sitemap_completeness was expected to stay silent on this "
        f"exact regression (that's the gap CRAWL-8 closes), got {sitemap_errors}"
    )

    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert any("/nevada/" in e and "not in the intended set" in e for e in errors), (
        f"expected the silently-noindexed /nevada/ to be flagged, got {errors}"
    )


# --- 3. a reinstatement page loses its noindex tag ---------------------------

def test_3_reinstatement_page_missing_noindex_is_caught(tmp_path):
    docs_dir = tmp_path / "docs"
    _build_intended_set(docs_dir)
    # Simulate a template regression that drops the exemption off one page.
    _write_page(docs_dir, "/alaska-cpa-license-reinstatement/", noindex=False)
    _write_sitemap(docs_dir, [
        "/nevada/", "/south-carolina-cpa-license-reinstatement/",
        "/alaska-cpa-license-reinstatement/",
    ])
    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert any(
        "/alaska-cpa-license-reinstatement/" in e and "expected to be noindexed but are not" in e
        for e in errors
    ), f"expected the de-exempted Alaska page to be flagged as missing, got {errors}"


# --- 4/5. pinned constant isolation (same shape as BLOG-2's 8a/8b) ----------

def test_4_slug_hash_mismatch_isolated(tmp_path, monkeypatch):
    base_slugs = frozenset({"alpha", "beta"})
    digest = hashlib.sha256(("\n".join(sorted(base_slugs)) + "\n").encode("utf-8")).hexdigest()
    monkeypatch.setattr(gate, "NOINDEX_REINSTATEMENT_SLUG_COUNT", len(base_slugs))
    monkeypatch.setattr(gate, "NOINDEX_REINSTATEMENT_SLUG_SHA256", digest)

    docs_dir = tmp_path / "docs"
    for p in gate.NOINDEX_APP_AUTH_ALLOWLIST:
        _write_page(docs_dir, p, noindex=True)
    for slug in base_slugs:
        _write_page(docs_dir, f"/{slug}-cpa-license-reinstatement/", noindex=True)
    _write_sitemap(docs_dir, [])

    clean_errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=base_slugs
    )
    assert clean_errors == [], f"pinned constants should match this 2-slug fixture: {clean_errors}"

    # Same count, different membership -- count check passes, hash check fails.
    swapped = frozenset({"alpha", "gamma"})
    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=swapped
    )
    hash_errors = [e for e in errors if "content hash" in e and "!= expected" in e]
    assert len(hash_errors) == 1, f"expected exactly one isolated hash error, got {errors}"


def test_5_slug_count_mismatch_isolated(tmp_path, monkeypatch):
    base_slugs = frozenset({"alpha", "beta"})
    digest = hashlib.sha256(("\n".join(sorted(base_slugs)) + "\n").encode("utf-8")).hexdigest()
    monkeypatch.setattr(gate, "NOINDEX_REINSTATEMENT_SLUG_COUNT", len(base_slugs))
    monkeypatch.setattr(gate, "NOINDEX_REINSTATEMENT_SLUG_SHA256", digest)

    docs_dir = tmp_path / "docs"
    for p in gate.NOINDEX_APP_AUTH_ALLOWLIST:
        _write_page(docs_dir, p, noindex=True)
    extra_slugs = frozenset({"alpha", "beta", "gamma"})
    for slug in extra_slugs:
        _write_page(docs_dir, f"/{slug}-cpa-license-reinstatement/", noindex=True)
    _write_sitemap(docs_dir, [])

    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=extra_slugs
    )
    count_errors = [e for e in errors if "has 3 slug(s), expected 2" in e]
    assert len(count_errors) == 1, f"expected exactly one isolated count error, got {errors}"


# --- 6. zero noindexed pages built at all ------------------------------------

def test_6_zero_noindexed_pages_is_enablement_error(tmp_path):
    docs_dir = tmp_path / "docs"
    _write_page(docs_dir, "/nevada/", noindex=False)
    _write_sitemap(docs_dir, ["/nevada/"])
    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert any("found ZERO noindexed built pages" in e for e in errors), errors


# --- 7/8. GATE-42: non-index.html pages are now asserted too ----------------

def test_7_404_html_noindex_detection_toggles_correctly(tmp_path):
    """AuditLab (2026-10-03): the "missing" assertion alone can't discriminate
    the fix from the pre-fix bug -- under the old index.html-only enumeration,
    /404.html was unconditionally absent from `actual` (noindexed or not), so
    an error "expected to be noindexed but are not" fires either way and
    proves nothing. What actually pins GATE-42 is the first assertion below:
    with 404.html CORRECTLY noindexed, there must be zero /404.html errors --
    the old code could never reach that state since it couldn't see the file
    at all. (test_1 and test_8 also pin the fix; this test just makes the
    404-specific case explicit and named for it, per AuditLab's own review.)"""
    docs_dir = tmp_path / "docs"
    _build_intended_set(docs_dir)
    # /404.html is in NOINDEX_APP_AUTH_ALLOWLIST, so _build_intended_set
    # already wrote it correctly noindexed.
    _write_sitemap(docs_dir, ["/nevada/", "/south-carolina-cpa-license-reinstatement/"])
    clean_errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert not any("/404.html" in e for e in clean_errors), (
        f"404.html is correctly noindexed and must not be flagged, got {clean_errors}"
    )

    # Now simulate losing the tag (the pre-fix blind spot GATE-42 closes).
    _write_page(docs_dir, "/404.html", noindex=False)
    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert any("/404.html" in e and "expected to be noindexed but are not" in e for e in errors), (
        f"expected the de-noindexed 404.html to be flagged as missing, got {errors}"
    )


def test_8_stray_html_page_noindexed_without_intent_is_caught(tmp_path):
    docs_dir = tmp_path / "docs"
    _build_intended_set(docs_dir)
    _write_sitemap(docs_dir, ["/nevada/", "/south-carolina-cpa-license-reinstatement/"])
    # The other direction: a stray non-index.html page gains an unintended
    # noindex -- also unasserted pre-fix since it was outside the enumeration.
    _write_page(docs_dir, "/offline.html", noindex=True)
    errors = gate.check_noindex_set_matches_intent(
        _html_files(docs_dir), docs_dir, tmp_path, reinstatement_slugs=REAL_SLUGS
    )
    assert any("/offline.html" in e and "not in the intended set" in e for e in errors), (
        f"expected the unintended noindex on offline.html to be flagged, got {errors}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
