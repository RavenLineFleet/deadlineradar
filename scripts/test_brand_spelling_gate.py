"""COPY-64 (AuditLab LOW, 2026-11-19): check_brand_spelling must PASS on the real
tree and FAIL on each way an unhyphenated brand could ship.

    python -m pytest scripts/test_brand_spelling_gate.py -q
"""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402
from pathlib import Path  # noqa: E402

NL = chr(10)


@pytest.fixture()
def tree(tmp_path):
    shutil.copytree(os.path.join(ROOT, "docs", "pricing"), tmp_path / "docs" / "pricing")
    (tmp_path / "worker" / "src").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "worker", "src", "index.ts"), tmp_path / "worker" / "src" / "index.ts")
    return tmp_path


def test_real_tree_passes():
    assert gate.check_brand_spelling(Path(ROOT)) == []


def test_fixture_passes(tree):
    assert gate.check_brand_spelling(tree) == []


@pytest.mark.parametrize("bad", ["DeadlineRadar", "Deadline Radar"])
def test_unhyphenated_in_meta_description_fails(tree, bad):
    page = tree / "docs" / "pricing" / "index.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace("<head>", f'<head><meta name="description" content="See {bad} plans.">', 1),
                    encoding="utf-8")
    errs = gate.check_brand_spelling(tree)
    assert len(errs) == 1 and "docs/pricing/index.html" in errs[0] and bad in errs[0]


def test_unhyphenated_in_sitemap_xml_fails(tree):
    (tree / "docs" / "sitemap.xml").write_text("<urlset><!-- DeadlineRadar --></urlset>", encoding="utf-8")
    assert any("sitemap.xml" in e for e in gate.check_brand_spelling(tree))


def test_lowercase_csv_filename_allowed(tree):
    (tree / "docs" / "x.js").write_text('drTriggerCsvDownload("deadlineradar-roster.csv")', encoding="utf-8")
    assert gate.check_brand_spelling(tree) == []


def test_worker_string_literal_fails(tree):
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + '\nconst subj = "Your DeadlineRadar reminder";\n',
                 encoding="utf-8")
    errs = gate.check_brand_spelling(tree)
    assert len(errs) == 1 and "worker/src/index.ts:" in errs[0]


@pytest.mark.parametrize("comment", [
    "// DeadlineRadar internal note",
    "/* DeadlineRadar one-liner */",
    "/**\n * DeadlineRadar Worker docstring\n */",
])
def test_worker_comments_allowed(tree, comment):
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + "\n" + comment + "\n", encoding="utf-8")
    assert gate.check_brand_spelling(tree) == []


def test_empty_docs_is_not_a_pass(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "worker" / "src").mkdir(parents=True)
    (tmp_path / "worker" / "src" / "a.ts").write_text("export {};", encoding="utf-8")
    errs = gate.check_brand_spelling(tmp_path)
    assert errs and "measuring nothing" in errs[0]


def test_no_worker_ts_is_not_a_pass(tree):
    shutil.rmtree(tree / "worker")
    errs = gate.check_brand_spelling(tree)
    assert errs and "measuring nothing" in errs[0]


# --- COPY-64 residuals (AuditLab t857): G1 .svg, G2 nested worker/src, G3 template literals ---

def test_svg_in_docs_fails(tree):
    (tree / "docs" / "logo.svg").write_text("<svg><text>DeadlineRadar</text></svg>", encoding="utf-8")
    assert any("logo.svg" in e for e in gate.check_brand_spelling(tree))


def test_nested_worker_ts_fails(tree):
    (tree / "worker" / "src" / "lib").mkdir()
    (tree / "worker" / "src" / "lib" / "brand.ts").write_text('export const BRAND = "DeadlineRadar";\n',
                                                              encoding="utf-8")
    assert any("worker/src/lib/brand.ts:1" in e for e in gate.check_brand_spelling(tree))


@pytest.mark.parametrize("line", [
    "*{box-sizing:border-box} DeadlineRadar",   # CSS line inside an htmlPage() template
    "// DeadlineRadar",                          # comment-shaped text inside a template is served
    "/* DeadlineRadar */",
])
def test_comment_shaped_line_inside_template_literal_fails(tree, line):
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + f"\nconst page = `<style>\n{line}\n</style>`;\n",
                 encoding="utf-8")
    errs = gate.check_brand_spelling(tree)
    assert len(errs) == 1 and "worker/src/index.ts:" in errs[0]


@pytest.mark.parametrize("code", [
    'const x = 1; // DeadlineRadar trailing note',
    'const y = a /* DeadlineRadar */ + b;',
    'const r = s.replace(/"/g, "&quot;"); // DeadlineRadar after a quote-bearing regex',
    'const t = `a ${ `inner ${1}` } b`; // DeadlineRadar after nested templates',
])
def test_trailing_and_inline_comments_allowed(tree, code):
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + "\n" + code + "\n", encoding="utf-8")
    assert gate.check_brand_spelling(tree) == []


def test_url_in_string_is_not_a_comment(tree):
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + '\nconst u = "https://x.example/DeadlineRadar";\n',
                 encoding="utf-8")
    assert len(gate.check_brand_spelling(tree)) == 1


def test_tokenizer_desync_fails_loud(tree):
    (tree / "worker" / "src" / "broken.ts").write_text("const a = `never closed" + NL, encoding="utf-8")
    errs = gate.check_brand_spelling(tree)
    assert any("broken.ts" in e and "out of sync" in e for e in errs)


def test_every_real_worker_file_tokenizes_in_sync():
    files = sorted((Path(ROOT) / "worker" / "src").rglob("*.ts"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        lines, ok = gate._ts_lex_lines(text)
        assert ok, f
        assert len(lines) == text.count(NL) + 1, f


# COPY-64 G4 (AuditLab 2026-11-19): a postfix `!`/`++`/`--` before a division is not a
# regex start. The G3 tokenizer opened a phantom regex here and blocked every push.
@pytest.mark.parametrize("code", [
    "const r = a! / b;",
    "const r = o.c! / t;",
    "const s = `${a! / b}`;",
    "const r = i++ / 2;",
    "const r = i-- / 2;",
    "const r = f(x)! / 2;",
    "const r = xs[0]! / 2;",
])
def test_postfix_operator_division_is_not_a_regex(tree, code):
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + NL + code + NL, encoding="utf-8")
    assert gate.check_brand_spelling(tree) == []
    # ...and the brand on the next line is still seen (nothing swallowed).
    p.write_text(p.read_text(encoding="utf-8") + 'const z = "DeadlineRadar";' + NL,
                 encoding="utf-8")
    errs = gate.check_brand_spelling(tree)
    assert len(errs) == 1 and "DeadlineRadar" in errs[0]


@pytest.mark.parametrize("code", [
    r'if (!/^\d{4}-\d{2}$/.test(x)) { y = 1; } // DeadlineRadar note',
    r'function f(x) { return !/^\d+$/.test(x); } // DeadlineRadar note',
    r'const ok = a && !/^\/x/.test(b); // DeadlineRadar note',
])
def test_prefix_bang_regex_still_lexed_as_regex(tree, code):
    # The live `!/^\d{4}-...$/.test(...)` idiom (deadline.ts, index.ts, totp.ts): the
    # `!` is prefix, so the regex (here holding `/`) must not desync or eat the comment.
    p = tree / "worker" / "src" / "index.ts"
    p.write_text(p.read_text(encoding="utf-8") + NL + code + NL, encoding="utf-8")
    assert gate.check_brand_spelling(tree) == []


def test_one_ts_tokenizer_name_only():
    # G5 naming hazard: GATE-69 was a twice-defined `_strip_ts_comments`. The brand
    # arm's lexer must not sit beside it under a word-order near-homonym.
    src = Path(HERE, "preship_gate.py").read_text(encoding="utf-8")
    assert src.count(NL + "def _strip_ts_comments(") == 1
    assert ("_ts_strip" + "_comments") not in src
