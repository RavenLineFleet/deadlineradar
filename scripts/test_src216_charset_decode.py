"""SRC-216: windows-1252 pages must decode without U+FFFD, UTF-8 bytes must never be re-read as
cp1252 because of a stale <meta> declaration (the browser path), and the build must fail on any
U+FFFD in shipped data."""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "reverify"))
import preship_gate  # noqa: E402
from source_check import _decode_html_body  # noqa: E402

FFFD = chr(0xFFFD)
NBSP = chr(0xA0)
RSQUO = chr(0x2019)
META_1252 = b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252"></head>'


def test_declared_cp1252_decodes_nbsp_and_curly_quote():
    # ilga.gov shape: NBSP run before the clause; leg.state.nv.us shape: 0x92 apostrophe
    t = _decode_html_body(META_1252 + b"b)\xa0\xa0\xa0 CPA Firms board\x92s")
    assert FFFD not in t
    assert ("b)" + NBSP * 3 + " CPA Firms") in t and ("board" + RSQUO + "s") in t


def test_utf8_bytes_win_over_a_stale_cp1252_declaration():
    # browser path: page.content() is re-encoded as UTF-8 while the markup still says windows-1252
    body = META_1252 + ("caf" + chr(0xE9) + " " + NBSP).encode("utf-8")
    t = _decode_html_body(body)
    assert t.endswith("caf" + chr(0xE9) + " " + NBSP)


def test_undeclared_invalid_utf8_falls_back_to_cp1252():
    assert _decode_html_body(b"plain \xa0 \x93q\x94") == "plain " + NBSP + " " + chr(0x201C) + "q" + chr(0x201D)


def test_utf16_sniff_still_first():
    assert _decode_html_body("<p>Rule 20</p>".encode("utf-16-le") * 10).startswith("<p>Rule 20</p>")


def test_gate_fails_on_replacement_char(tmp_path: pathlib.Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "worker" / "src").mkdir(parents=True)
    (tmp_path / "data" / "ok.json").write_text(json.dumps({"a": "b"}), encoding="utf-8")
    (tmp_path / "worker" / "src" / "bad.json").write_text(json.dumps({"h": "x" + FFFD * 2}, ensure_ascii=False) + chr(10), encoding="utf-8")
    errs = preship_gate.check_no_replacement_char_in_shipped_data(tmp_path)
    assert len(errs) == 1 and "worker/src/bad.json carries 2 U+FFFD" in errs[0]


def test_src237_hand_spec_is_scanned(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "worker" / "src").mkdir(parents=True)
    spec = tmp_path / "scripts" / "reverify"
    spec.mkdir(parents=True)
    (spec / "hand_spec.json").write_text(json.dumps({"anchor": "x" + FFFD}, ensure_ascii=False), encoding="utf-8")
    errs = preship_gate.check_no_replacement_char_in_shipped_data(tmp_path)
    assert len(errs) == 1 and "scripts/reverify/hand_spec.json carries 1 U+FFFD" in errs[0]


def test_repo_data_is_clean_now():
    assert preship_gate.check_no_replacement_char_in_shipped_data(HERE.parent) == []


def test_recut_anchors_carry_no_replacement_char():
    recipes = json.loads((HERE.parent / "data" / "reverify_recipes.json").read_text(encoding="utf-8"))
    recipes = recipes.get("recipes", recipes)
    for rid in ("il-firm", "il-cpe", "nv-cpe"):
        for c in recipes[rid]["checks"]:
            assert FFFD not in c["anchor"], rid
