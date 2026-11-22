"""SRC-284 (AuditLab MEDIUM, 2026-11-22): a rule-change card must also link
status_source_url when it is a different page from citation_url.

Arizona's only rendered link (azleg compiled 32-725) already serves the
post-2027 text, so the card's statement of current law had no supporting
link. The enacted act (Laws 2026 Ch. 17) was in the event, unrendered."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import generate  # noqa: E402

CIT = "https://www.azleg.gov/ars/32/00725.htm"
ACT = "https://www.azleg.gov/legtext/57leg/2R/laws/0017.htm"


def _event(**kw):
    e = {
        "jurisdiction": "Arizona", "jurisdiction_slug": "arizona", "kind": "rule_change",
        "status": "ENACTED", "effective_date": "2027-01-01", "citation": "A.R.S. 32-725",
        "citation_url": CIT, "confidence": "single_source", "verified_date": "2026-09-12",
        "summary_public": "x", "source": "difflab_reg_change_engine",
    }
    e.update(kw)
    return e


def test_distinct_status_source_renders_second_link():
    html = generate._rc_status_source_link_html(_event(status_source_url=ACT))
    assert f'href="{ACT}"' in html
    assert "Where we confirmed its status" in html


def test_same_url_renders_nothing():
    assert generate._rc_status_source_link_html(_event(status_source_url=CIT)) == ""


def test_missing_or_non_http_renders_nothing():
    assert generate._rc_status_source_link_html(_event()) == ""
    assert generate._rc_status_source_link_html(_event(status_source_url=None)) == ""
    assert generate._rc_status_source_link_html(_event(status_source_url="javascript:alert(1)")) == ""


def test_rule_change_card_carries_both_links():
    html = generate._rule_change_card_html(_event(status_source_url=ACT))
    assert f'href="{CIT}"' in html and f'href="{ACT}"' in html


def test_live_arizona_event_reaches_the_act():
    """Real data, not a fixture: the shipped Arizona event's card links the act."""
    import json
    path = os.path.join(os.path.dirname(HERE), "data", "reg_change_events.json")
    data = json.load(open(path, encoding="utf-8"))
    events = data["events"] if isinstance(data, dict) else data
    az = [e for e in events if e.get("jurisdiction_slug") == "arizona" and e.get("kind") == "rule_change"]
    assert az, "Arizona rule_change event missing -- this test's premise is gone"
    assert any(f'href="{ACT}"' in generate._rule_change_card_html(e) for e in az)
