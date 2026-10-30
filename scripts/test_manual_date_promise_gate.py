"""COPY-54 (AuditLab, 2026-10-30): a manual date-entry promise on any built
page or /llms.txt must name the number of state pages that render the date input.

    python -m pytest scripts/test_manual_date_promise_gate.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

_INPUT = f'<input type="date" {gate._DATE_INPUT_MARKER}>'


def _tree(tmp_path, home_promise, llms_promise="Nothing to promise here."):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cpa_deadlines.json").write_text(json.dumps({"records": [
        {"state_slug": "alpha"}, {"state_slug": "beta"}, {"state_slug": "gamma"},
    ]}), encoding="utf-8")
    docs = tmp_path / "docs"
    for slug, has_input in (("alpha", True), ("beta", False), ("gamma", False)):
        (docs / slug).mkdir(parents=True)
        (docs / slug / "index.html").write_text(
            f"<p>{slug}</p>{_INPUT if has_input else ''}", encoding="utf-8")
    (docs / "index.html").write_text(
        f'<div class="item"><span class="n">2</span><span class="lbl">vary</span></div>'
        f'<p class="trust-footnote">{home_promise}</p>'
        f'<label>Enter the expiration date printed on your license</label>',
        encoding="utf-8")
    (docs / "llms.txt").write_text(f"- {llms_promise}\n", encoding="utf-8")
    return tmp_path


def test_promise_naming_the_census_passes(tmp_path):
    root = _tree(tmp_path, "In 1, you enter the date on your license and we track it.",
                 "In 1 of the 3, the visitor enters their own license's date.")
    assert gate.check_manual_date_promise_count(root) == []


def test_promise_naming_another_population_is_error(tmp_path):
    # The pre-COPY-54 shape: the "varies" count glued to the promise.
    root = _tree(tmp_path, "In the 2 that vary, you enter the date on your license.",
                 "2 vary, so the visitor enters their own license's date.")
    errors = gate.check_manual_date_promise_count(root)
    assert len(errors) == 2, errors
    assert all("[2]" in e and "1 state pages" in e for e in errors), errors


def test_census_glued_to_another_population_is_error(tmp_path):
    # GUARD-25: the census is named, but against the "varies" count. Only the
    # `nums - allowed` disjunct catches this.
    root = _tree(tmp_path, "In 1 of the 2 that vary, you enter the date on your license.",
                 "1 of the 2 that vary, the visitor enters their own license's date.")
    errors = gate.check_manual_date_promise_count(root)
    assert len(errors) == 2, errors
    assert all("[1, 2]" in e for e in errors), errors


def test_census_of_the_full_page_count_passes(tmp_path):
    # GUARD-25: len(slugs) stays allowed, so honest "6 of the 55" copy passes.
    root = _tree(tmp_path, "In 1 of the 3 jurisdictions, you enter the date on your license.")
    assert gate.check_manual_date_promise_count(root) == []


def test_promise_naming_only_the_full_page_count_is_error(tmp_path):
    # GUARD-27: "in all 3" names only len(slugs), which is in `allowed`, so
    # only the `census not in nums` disjunct catches this overclaim.
    root = _tree(tmp_path, "In all 3 jurisdictions, you enter the date on your license.")
    errors = gate.check_manual_date_promise_count(root)
    assert len(errors) == 1 and "[3]" in errors[0], errors


def test_promise_on_a_blog_post_is_scanned(tmp_path):
    # GUARD-26: the population is the whole built tree, not just / and llms.txt.
    root = _tree(tmp_path, "In 1, you enter the date on your license.")
    post = root / "docs" / "blog" / "some-post" / "index.html"
    post.parent.mkdir(parents=True)
    post.write_text("<p>In the 2 that vary, you enter the date from your license.</p>", encoding="utf-8")
    errors = gate.check_manual_date_promise_count(root)
    assert len(errors) == 1 and "/blog/some-post/index.html" in errors[0], errors


def test_census_of_zero_is_error_not_pass(tmp_path):
    root = _tree(tmp_path, "In 1, you enter the date on your license.")
    page = root / "docs" / "alpha" / "index.html"
    page.write_text("<p>alpha</p>", encoding="utf-8")
    errors = gate.check_manual_date_promise_count(root)
    assert len(errors) == 1 and "measuring nothing" in errors[0], errors


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
