"""Tests for the CRAWL-12 body-change gate (AuditLab, 2026-10-23).

    python -m pytest scripts/test_blog_body_gate.py -q

A published post's body_html may not change unless its declared dateModified
advances with it (check_blog_body_change_advances_modified in preship_gate.py).
Case 3 is the Alabama replay: pin updated to the new body, date left alone.
"""
import hashlib
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

OLD = "<p>Due December 31.</p>"
NEW = "<p>Due October 1; December 31 ends the grace period.</p>"


def _sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _article(body=OLD, **extra):
    return {"slug": "al", "published": "2026-10-02", "body_html": body, **extra}


def _row(body=OLD, last_reviewed="2026-10-16", body_modified="2026-10-16"):
    return {"last_reviewed": last_reviewed, "body_sha256": _sha(body), "body_modified": body_modified}


def _run(article, row, base_row):
    reviews = {} if row is None else {"al": row}
    base = {} if base_row is None else {"al": base_row}
    return gate.check_blog_body_change_advances_modified(
        REPO_ROOT, blog_articles=[article], reviews=reviews, base_reviews=base
    )


def test_1_unchanged_body_passes():
    assert _run(_article(), _row(), _row()) == []


def test_2_body_changed_pin_not_updated_fails():
    errs = _run(_article(NEW), _row(OLD), _row(OLD))
    assert len(errs) == 1 and "body_html changed but guide_reviews.json pins" in errs[0]


def test_3_alabama_replay_pin_updated_date_not_advanced_fails():
    errs = _run(_article(NEW), _row(NEW), _row(OLD))
    assert len(errs) == 1 and "did not advance" in errs[0]


def test_4_body_changed_with_content_modified_advanced_passes():
    art = _article(NEW, content_modified="2026-10-23")
    assert _run(art, _row(NEW, body_modified="2026-10-23"), _row(OLD)) == []


def test_4b_body_changed_with_last_reviewed_advanced_passes():
    row = _row(NEW, last_reviewed="2026-10-23", body_modified="2026-10-23")
    assert _run(_article(NEW), row, _row(OLD)) == []


def test_5_body_modified_out_of_step_with_declared_date_fails():
    errs = _run(_article(), _row(body_modified="2026-10-23"), _row())
    assert len(errs) == 1 and "declared dateModified" in errs[0]


def test_6_declared_date_never_earlier_than_published():
    art = {"slug": "al", "published": "2026-10-20", "body_html": OLD}
    assert _run(art, _row(body_modified="2026-10-20"), None) == []


def test_7_new_post_with_no_base_row_passes():
    assert _run(_article(), _row(), None) == []


def test_8_missing_registry_row_fails():
    errs = _run(_article(), None, None)
    assert len(errs) == 1 and "no row" in errs[0]


def test_9_unreadable_base_is_an_error_not_a_skip(tmp_path):
    errs = gate.check_blog_body_change_advances_modified(
        tmp_path, blog_articles=[_article()], reviews={"al": _row()}
    )
    assert len(errs) == 1 and "could not read origin/main" in errs[0]


def test_10_real_repo_pins_match_shipped_bodies():
    assert gate.check_blog_body_change_advances_modified(REPO_ROOT) == []


def test_11_unimportable_generate_is_an_error_not_a_skip(monkeypatch):
    # GATE-65: repo_root does not govern `import generate` (sys.path/sys.modules
    # do), so block the import where it actually resolves.
    monkeypatch.setitem(sys.modules, "generate", None)
    errs = gate.check_blog_body_change_advances_modified(REPO_ROOT)
    assert len(errs) == 1 and "measuring nothing" in errs[0], errs


def test_12_a_syntaxerror_in_generate_is_reported_not_raised(monkeypatch):
    # GATE-67: test_11's blocked import raises ImportError, which the
    # pre-GATE-66 `except ImportError` also caught -- only a non-ImportError
    # can tell the widening apart from the arm it replaced.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "generate":
            raise SyntaxError("invalid syntax (generate.py, line 1)")
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "generate", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    errs = gate.check_blog_body_change_advances_modified(REPO_ROOT)
    assert len(errs) == 1 and "SyntaxError" in errs[0], errs
