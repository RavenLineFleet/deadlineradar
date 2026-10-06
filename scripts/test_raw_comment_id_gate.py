"""Tests for check_no_internal_ids_in_shipped_comments (LB-3 follow-up, AuditLab 2026-10-05).

    python -m pytest scripts/test_raw_comment_id_gate.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402


def _run(tmp_path, body):
    f = tmp_path / "index.html"
    f.write_text(body, encoding="utf-8")
    return gate.check_no_internal_ids_in_shipped_comments([f])


def test_catches_a11y18_in_script_comment(tmp_path):
    assert _run(tmp_path, "<script>\n// A11Y-18: a placeholder is not a name\n</script>")


def test_catches_pr6b_in_html_comment(tmp_path):
    assert _run(tmp_path, "<p>x</p><!-- PR6-B round 2 -->")


def test_catches_bare_pr6_label(tmp_path):
    assert _run(tmp_path, "<script>// PR6 (2026-10-02): cadence</script>")


def test_catches_prose_style_id_in_style_block(tmp_path):
    assert _run(tmp_path, "<style>/* see COPY-3 */ a{}</style>")


def test_rendered_prose_statute_cite_is_ignored(tmp_path):
    assert not _run(tmp_path, "<p>Per A.A.C. R4-1-345 and NAC-628.016.</p>")


def test_jsonld_statute_cite_is_ignored(tmp_path):
    assert not _run(tmp_path, '<script type="application/ld+json">{"text": "A.A.C. R4-1-345(B)"}</script>')


def test_allowlisted_token_is_ignored(tmp_path):
    assert not _run(tmp_path, "<script>// SHA-256 digest, UTF-8</script>")


def test_empty_input_is_not_a_silent_pass():
    assert gate.check_no_internal_ids_in_shipped_comments([])


def test_new_codenames_match():
    for name in ("GrowthLab", "LocalBot", "FleetChat", "StockWatch"):
        assert gate._PROSE_INTERNAL_NAME_RE.search(f"// {name} note")


def test_real_docs_tree_is_clean():
    from pathlib import Path
    docs = Path(HERE).parent / "docs"
    files = list(docs.rglob("*.html"))
    assert files
    assert gate.check_no_internal_ids_in_shipped_comments(files) == []
