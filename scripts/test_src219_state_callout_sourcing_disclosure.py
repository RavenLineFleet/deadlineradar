"""SRC-219: the state-page "Heads up" callout must disclose what /rule-changes/
discloses for the same record: anything below the two-source standard, and the
verified date."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate  # noqa: E402

CAVEAT = "not yet independently confirmed by a second source"


def _event(**over):
    e = {
        "event_id": "t-1",
        "kind": "rule_change",
        "jurisdiction": "Testland",
        "jurisdiction_slug": "testland",
        "topic": "CPA rule change",
        "status": "ENACTED",
        "effective_date": "2099-01-01",
        "summary_public": "A summary.",
        "citation": "Rule 1",
        "citation_url": "https://example.gov/rule",
        "source": "difflab_reg_change_engine",
        "confidence": "single_source",
        "verified_date": "2026-09-12",
    }
    e.update(over)
    return e


def _callout(monkeypatch, e):
    monkeypatch.setattr(generate, "_UPCOMING_CHANGE_EVENTS_BY_STATE", {"testland": [e]})
    return generate._upcoming_change_callout_html("testland")


def test_single_source_gets_caveat_and_verified_date(monkeypatch):
    html = _callout(monkeypatch, _event())
    assert CAVEAT in html
    assert 'data-verified="2026-09-12"' in html
    assert "Verified September 12, 2026" in html


def test_dual_source_has_no_caveat_but_keeps_verified_date(monkeypatch):
    html = _callout(monkeypatch, _event(confidence="dual_source"))
    assert "Sourcing:" not in html
    assert CAVEAT not in html
    assert 'data-verified="2026-09-12"' in html


def test_non_engine_single_source_wording(monkeypatch):
    html = _callout(monkeypatch, _event(source="manual", confidence="single_source"))
    assert "single-source legal research" in html and CAVEAT in html


def test_unrecognised_confidence_fails_toward_disclosure(monkeypatch):
    for bad in (None, "", "triple_source"):
        html = _callout(monkeypatch, _event(source="manual", confidence=bad))
        assert "Sourcing: sourcing basis unconfirmed" in html


def test_missing_verified_date_renders_no_badge(monkeypatch):
    html = _callout(monkeypatch, _event(verified_date=None))
    assert "data-verified" not in html
    assert CAVEAT in html


def test_callout_and_card_share_one_label():
    for over in (
        {},
        {"source": "manual"},
        {"source": "manual", "confidence": "dual_source"},
        {"confidence": "dual_source"},
        {"confidence": None, "source": "manual"},
    ):
        e = _event(**over)
        assert generate.esc(generate._rc_source_label(e)) in generate._rule_change_card_html(e)
