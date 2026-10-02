"""Tests for the BLOG-1 two-party approval gate (AuditLab spec, 2026-10-02).

    python -m pytest scripts/test_blog_approval_gate.py -q

9 cases AuditLab required before calling this verified:
    1. happy path: 2 distinct parties, matching digests          -> PASS
    2. one approval only                                         -> ERROR
    3. two approvals, same party twice                           -> ERROR
    4. BLOG-1 replay: approve, then change one byte of body_html -> ERROR
    5. approvals dir deleted while BLOG_ARTICLES non-empty       -> ERROR (enablement)
    6. sha truncated to 16 hex                                    -> ERROR
    7. grandfathered slug, no approvals file                      -> PASS
    8. slug appended to GRANDFATHERED without updating constant   -> ERROR
    9. draft-mode and slug-mode digests agree on a real post      -> PASS
"""
import hashlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import blog_payload_hash as bph  # noqa: E402
import preship_gate as gate  # noqa: E402


def _article(**overrides):
    base = {
        "slug": "test-slug",
        "published": "2026-10-02",
        "title": "Test Title",
        "seo_title": "Test SEO Title",
        "meta_description": "Test meta description.",
        "body_html": "\n<p>Body content.</p>\n",
    }
    base.update(overrides)
    return base


def _digest(article):
    return bph.payload_sha256_from_fields(article)


def _approval_file(tmp_path, slug, approvals):
    d = tmp_path / "content" / "blog_approvals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.json").write_text(
        json.dumps({"slug": slug, "approvals": approvals}), encoding="utf-8"
    )
    return d


def _write_grandfathered(tmp_path, slugs):
    d = tmp_path / "content" / "blog_approvals"
    d.mkdir(parents=True, exist_ok=True)
    text = "\n".join(slugs) + "\n"
    (d / gate.BLOG_GRANDFATHERED_FILENAME).write_bytes(text.encode("utf-8"))
    return d, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _good_entry(party, article):
    return {
        "party": party,
        "payload_sha256": _digest(article),
        "verdict_file": f"{party}_verdict.md",
        "sent": "2026-10-02 12:00 MDT",
    }


# --- 1. happy path -----------------------------------------------------------

def test_1_happy_path_two_distinct_parties_matching_digests(tmp_path):
    article = _article()
    _write_grandfathered(tmp_path, [])
    _approval_file(
        tmp_path, article["slug"],
        [_good_entry("auditlab", article), _good_entry("orchestrator", article)],
    )
    # the grandfathered constants won't match this tmp fixture's empty list --
    # scope the assertion to this slug, not the whole error list.
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[article])
    slug_errors = [e for e in errors if article["slug"] in e]
    assert slug_errors == [], f"expected no errors for the approved slug, got {slug_errors}"


# --- 2. one approval only -----------------------------------------------------

def test_2_one_approval_only_is_error(tmp_path):
    article = _article()
    _write_grandfathered(tmp_path, [])
    _approval_file(tmp_path, article["slug"], [_good_entry("auditlab", article)])
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[article])
    assert any("fewer than 2 distinct-party approvals" in e for e in errors)


# --- 3. two approvals, same party twice --------------------------------------

def test_3_same_party_twice_is_error(tmp_path):
    article = _article()
    _write_grandfathered(tmp_path, [])
    _approval_file(
        tmp_path, article["slug"],
        [_good_entry("auditlab", article), _good_entry("auditlab", article)],
    )
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[article])
    assert any("fewer than 2 distinct-party approvals" in e for e in errors)


# --- 4. BLOG-1 replay: approve, then mutate body_html after -------------------

def test_4_blog1_replay_content_changed_after_approval(tmp_path):
    article = _article()
    _write_grandfathered(tmp_path, [])
    # Approve against the ORIGINAL content...
    _approval_file(
        tmp_path, article["slug"],
        [_good_entry("auditlab", article), _good_entry("orchestrator", article)],
    )
    # ...then the content that would ship today has one byte changed.
    mutated = dict(article, body_html=article["body_html"].replace("Body content.", "Body content!"))
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[mutated])
    assert any("BLOG-1 shape" in e for e in errors), f"mutation did not trigger a digest-mismatch error: {errors}"


# --- 5. approvals dir deleted while BLOG_ARTICLES is non-empty ---------------

def test_5_approvals_dir_missing_is_enablement_error(tmp_path):
    article = _article()
    # Deliberately do NOT create content/blog_approvals/ at all.
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[article])
    assert len(errors) == 1
    assert "is missing entirely" in errors[0]
    assert "rule 2, enablement" in errors[0]


# --- 6. sha truncated to 16 hex ------------------------------------------------

def test_6_truncated_sha_is_error(tmp_path):
    article = _article()
    _write_grandfathered(tmp_path, [])
    entry_a = _good_entry("auditlab", article)
    entry_a["payload_sha256"] = entry_a["payload_sha256"][:16]
    _approval_file(tmp_path, article["slug"], [entry_a, _good_entry("orchestrator", article)])
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[article])
    assert any("is not 64 lowercase hex chars" in e for e in errors)


# --- 7. grandfathered slug, no approvals file ---------------------------------

def test_7_grandfathered_slug_no_approval_file_passes(tmp_path):
    article = _article()
    _write_grandfathered(tmp_path, [article["slug"]])
    # No <slug>.json written at all.
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[article])
    slug_errors = [e for e in errors if article["slug"] in e]
    assert slug_errors == [], f"grandfathered slug with no approval file should pass: {slug_errors}"


# --- 8. slug appended to GRANDFATHERED without updating the constant --------

def test_8_grandfathered_edit_without_constant_update_is_error(tmp_path, monkeypatch):
    # Pin the constants to a known-good 2-slug fixture first...
    base_slugs = ["alpha-post", "beta-post"]
    _, digest = _write_grandfathered(tmp_path, base_slugs)
    monkeypatch.setattr(gate, "BLOG_GRANDFATHERED_COUNT", len(base_slugs))
    monkeypatch.setattr(gate, "BLOG_GRANDFATHERED_SHA256", digest)
    clean_errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[])
    assert clean_errors == [], f"pinned constants should match the 2-slug fixture: {clean_errors}"

    # ...then append a 3rd slug to the file WITHOUT touching the constants.
    _write_grandfathered(tmp_path, base_slugs + ["gamma-post"])
    errors = gate.check_blog_approval_gate(tmp_path, blog_articles=[])
    assert any("expected 2" in e for e in errors) or any("!= expected" in e for e in errors), errors


# --- 9. draft-mode and slug-mode digests agree on a real post ----------------

def test_9_draft_and_slug_mode_agree_on_a_real_post(tmp_path):
    """Builds a draft-format file FROM a real, currently-shipped BLOG_ARTICLES
    entry (so this needs no external fixture) and asserts extract_from_draft's
    digest equals extract_from_slug's digest for the same post -- the
    parser-drift risk the spec calls out explicitly."""
    sys.path.insert(0, REPO_ROOT)
    import generate  # noqa: E402

    real = generate.BLOG_ARTICLES[0]
    slug = real["slug"]

    draft_path = tmp_path / "draft.md"
    draft_text = (
        f"slug: {real['slug']}\n"
        f"published: {real['published']}\n"
        f"title: {real['title']}\n"
        f"seo_title: {real.get('seo_title', '')}\n"
        f"meta_description: {real['meta_description']}\n"
        f"\n"
        f"SOURCING (for reviewers, not published):\n"
        f"- irrelevant to the hash, parser must ignore this block entirely\n"
        f"\n"
        f"BODY_HTML:\n"
        f"{real['body_html'].strip(chr(10))}\n"
    )
    draft_path.write_text(draft_text, encoding="utf-8")

    draft_digest = bph.payload_sha256_from_fields(bph.extract_from_draft(draft_path))
    slug_digest = bph.payload_sha256_from_fields(bph.extract_from_slug(slug, REPO_ROOT))
    assert draft_digest == slug_digest, (
        f"draft-mode and slug-mode disagree for {slug!r}: {draft_digest} != {slug_digest}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
