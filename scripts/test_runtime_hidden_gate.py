"""HIDDEN-2: the preship gate must catch an element that ships VISIBLE and is hidden at runtime
(`el.hidden = true`) while a class `display` rule outranks the UA [hidden] rule.

    python -m pytest scripts/test_runtime_hidden_gate.py -q
"""
import os
import sys
from pathlib import Path

REPO = Path(os.path.dirname(os.path.abspath(__file__))).parent
sys.path.insert(0, str(REPO / "scripts"))
import preship_gate as pg  # noqa: E402

PAGE = (
    '<html><body><div id="tog" class="box"></div><p id="msg" class="note"></p>'
    '<script>var el=document.getElementById("msg");el.hidden=true;'
    'function f(){var el=document.getElementById("tog");el.hidden=true}</script></body></html>'
)
CSS_BUG = ".box { display: flex; }\n.note { color: red; }\n"
CSS_FIXED = CSS_BUG + ".box[hidden] { display: none; }\n"


def _run(tmp_path, page, css):
    (tmp_path / "styles.css").write_text(css, encoding="utf-8")
    f = tmp_path / "index.html"
    f.write_text(page, encoding="utf-8")
    return pg.check_runtime_hidden_display_override([f], tmp_path)


def test_flags_runtime_hidden_element_with_flex_and_no_override(tmp_path):
    errs = _run(tmp_path, PAGE, CSS_BUG)
    assert len(errs) == 1 and ".box" in errs[0] and "el.hidden" in errs[0]


def test_override_clears_it(tmp_path):
    assert _run(tmp_path, PAGE, CSS_FIXED) == []


def test_reused_variable_name_binds_to_nearest_preceding_declaration(tmp_path):
    # `el` is declared twice; the first site must bind to #msg (.note, safe), the second to #tog.
    resolved, unresolved = pg._runtime_hidden_targets(PAGE)
    assert [r[3] for r in resolved] == ["msg", "tog"] and unresolved == 0


def test_id_selector_rule_is_checked_too(tmp_path):
    assert len(_run(tmp_path, PAGE, "#tog { display: block; }\n")) == 1
    assert _run(tmp_path, PAGE, "#tog { display: block; }\n#tog[hidden] { display: none; }\n") == []


def test_vacuity_guards(tmp_path):
    assert "missing or empty" in _run(tmp_path, PAGE, "")[0]
    assert "measuring nothing" in _run(tmp_path, "<html><script>x.hidden=true</script></html>", CSS_FIXED)[0]


def test_real_tree_is_clean_and_binds_the_billing_toggle():
    docs = REPO / "docs"
    files = sorted(docs.rglob("index.html"))
    assert pg.check_runtime_hidden_display_override(files, docs) == []
    resolved, _ = pg._runtime_hidden_targets((docs / "pricing" / "index.html").read_text(encoding="utf-8"))
    assert any("dr-billing-interval-toggle" in r[2] for r in resolved)


def test_real_tree_without_the_override_is_flagged(tmp_path):
    """Positive control on the shipped artifact: strip the 71e6d39a8 fix from a copy of styles.css."""
    docs = REPO / "docs"
    css = (docs / "styles.css").read_text(encoding="utf-8")
    stripped = css.replace(".dr-billing-interval-toggle[hidden] { display: none; }", "")
    assert stripped != css
    (tmp_path / "styles.css").write_text(stripped, encoding="utf-8")
    errs = pg.check_runtime_hidden_display_override([docs / "pricing" / "index.html"], tmp_path)
    assert errs and all("dr-billing-interval-toggle" in e for e in errs)


def test_wired_into_all_errors():
    src = (REPO / "scripts" / "preship_gate.py").read_text(encoding="utf-8")
    assert "all_errors += check_runtime_hidden_display_override(html_files, docs_dir)" in src
