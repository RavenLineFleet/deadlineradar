"""Pins for the /deadline-calculator/ widget i18n (AuditLab I18N-2, 2026-10-11).

    python -m pytest scripts/test_calc_i18n.py -q
"""
import os
import re
import sys
from datetime import date
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(Path(HERE).parent))
import generate  # noqa: E402
import i18n  # noqa: E402

_PLACEHOLDER = re.compile(r"\{\w+\}")
_ENTITY = re.compile(r"&\w+;")


def test_js_uses_exactly_the_declared_keys_and_no_english_literals():
    js = re.sub(r"(?m)^\s*//.*$", "", generate._DEADLINE_CALC_JS)   # comments are stripped from the shipped page
    used = set(re.findall(r"I18N\.(\w+)", js))
    assert used == set(generate._CALC_I18N_KEYS), used ^ set(generate._CALC_I18N_KEYS)
    for phrase in ("Your next", "Select&hellip;", "Select your", "e.g. ", "See the full", "Your cohort",
                   "birth month", "birth year", "original certificate", "Last issuance", "already passed",
                   "individual license date", "separate filing", "'January'", '"January"'):
        assert phrase not in js, phrase
    # the only widget strings that stay outside i18n are data (state labels, gap notes, anchor labels)


def test_every_widget_key_exists_and_es_drafts_keep_placeholders_and_entities():
    for k in generate._CALC_I18N_KEYS + ("state_label", "select_state"):
        key = "calcw." + k
        assert key in i18n.EN, key
        es = i18n.ES.get(key)
        assert es is not None and es["en_hash"] == i18n.en_hash(key), key
        assert sorted(_PLACEHOLDER.findall(es["text"])) == sorted(_PLACEHOLDER.findall(i18n.EN[key])), key
        # &amp; is a word ("&") in EN and "y" in ES, so it is the one entity allowed to differ
        strip = lambda t: sorted(e for e in _ENTITY.findall(t) if e != "&amp;")  # noqa: E731
        assert strip(es["text"]) == strip(i18n.EN[key]), key
        assert ("<strong>" in es["text"]) == ("<strong>" in i18n.EN[key]), key


def test_date_style_en_matches_fmt_date_and_es_has_twelve_months():
    en_fmt = generate._calc_fmt_date("en")
    for m in range(1, 13):
        d = date(2027, m, 28)
        assert en_fmt(d) == generate.fmt_date(d)
    for lang, text in (("en", i18n.EN["calcw.date_style"]), ("es", i18n.ES["calcw.date_style"]["text"])):
        parts = text.split("|")
        assert len(parts) == 13 and all(parts), lang
        assert all(tok in parts[0] for tok in ("{month}", "{day}", "{year}")), lang
    # unreviewed Spanish falls back to English as ONE unit (template + months together)
    assert generate._calc_date_style("es") == generate._calc_date_style("en") or i18n.ES["calcw.date_style"]["reviewed"]


def _widget_data(lang):
    import json
    from collections import defaultdict
    recs = json.load(open(Path(HERE).parent / "data" / "cpa_deadlines.json", encoding="utf-8"))["records"]
    by = defaultdict(list)
    for r in recs:
        by[r["state_slug"]].append(r)
    return generate._deadline_calculator_widget_data(by, date(2026, 10, 11), lang)


def _labels(data):
    out = set()
    for e in data.values():
        out |= {g["group"] for g in e.get("groups", [])}
        out |= {e[k] for k in ("anchor_label", "term_label") if k in e}
    return out


def test_every_data_label_the_widget_shows_has_a_key_and_reviewed_es_replaces_all_of_them(monkeypatch):
    en = _labels(_widget_data("en"))             # raises KeyError if the data gained an unmapped label
    assert len(en) == 20 and en == set(generate._CALC_LABEL_KEYS)
    for label, key in generate._CALC_LABEL_KEYS.items():
        assert i18n.EN[key] == label             # EN output is the data string itself, byte for byte
        es = i18n.ES[key]
        assert es["en_hash"] == i18n.en_hash(key) and not _PLACEHOLDER.findall(es["text"]), key
    # with every label key reviewed, none of the 20 stays English (unreviewed ones fall back per t())
    for k in generate._CALC_LABEL_KEYS.values():
        monkeypatch.setitem(i18n.ES, k, {**i18n.ES[k], "reviewed": True})
    es_labels = _labels(_widget_data("es"))
    assert len(es_labels) == 20 and not (es_labels & en)


def test_unmapped_label_fails_the_build():
    import pytest
    with pytest.raises(KeyError):
        generate._calc_label("A brand new cohort name", "en")
