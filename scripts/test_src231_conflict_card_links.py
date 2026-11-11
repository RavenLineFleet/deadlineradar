"""SRC-231 (AuditLab, 2026-11-11): every source-conflict card links every side it says disagrees.

    python -m pytest scripts/test_src231_conflict_card_links.py -q

Oregon's card said its sources disagree and told the reader to "read both citations yourself",
but both rendered links were the side that agrees with the statute (ORS 673 + the Board's
peer-review page). The OAR it said disagrees had no URL. The card now renders a labelled second
link plus `additional_sources`.
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, str(REPO_ROOT))
import generate  # noqa: E402

OAR_0345 = "https://secure.sos.state.or.us/oard/view.action?ruleNumber=801-010-0345"
OAR_0010 = "https://secure.sos.state.or.us/oard/view.action?ruleNumber=801-005-0010"


def _conflicts(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {e["jurisdiction_slug"]: e for e in data["events"] if e.get("kind") == "source_conflict"}


def test_oregon_card_links_the_disagreeing_rule():
    html = generate._rule_conflict_card_html(_conflicts(REPO_ROOT / "data" / "reg_change_events.json")["oregon"])
    assert f'href="{OAR_0345}"' in html
    assert f'href="{OAR_0010}"' in html
    # positive control: the narrow side is still linked too
    assert 'href="https://www.oregonlegislature.gov/bills_laws/ors/ors673.html"' in html
    assert 'href="https://www.oregon.gov/boa/pages/peer-review.aspx"' in html


def test_no_conflict_card_falls_back_to_unlabelled_second_source():
    for slug, e in _conflicts(REPO_ROOT / "data" / "reg_change_events.json").items():
        html = generate._rule_conflict_card_html(e)
        assert ">Second source<" not in html, slug
        assert html.count("<a href=") >= 2, slug


def test_served_copy_matches_data_copy():
    a = (REPO_ROOT / "data" / "reg_change_events.json").read_bytes()
    b = (REPO_ROOT / "worker" / "src" / "reg_change_events.json").read_bytes()
    assert a == b


def test_malformed_additional_source_is_skipped_not_rendered_bare():
    e = dict(_conflicts(REPO_ROOT / "data" / "reg_change_events.json")["oregon"])
    e["additional_sources"] = [
        {"url": "javascript:alert(1)", "citation": "x"},
        {"url": "https://example.test/no-label", "citation": ""},
        "not-a-dict",
        {"url": "https://example.test/ok", "citation": "OK label"},
    ]
    html = generate._rule_conflict_card_html(e)
    assert "javascript:" not in html
    assert "example.test/no-label" not in html
    assert 'href="https://example.test/ok">OK label</a>' in html
