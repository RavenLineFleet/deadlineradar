"""WALK-1 (11-12 firm buyer walk, finding 3): the first-login questionnaire
asked firms whether they'd *want* Slack/Teams notifications and a custom
reminder schedule. Both already exist (/for-firms/ says the Slack/Teams digest
is included on every paid plan; reminder cadence is a dashboard setting), so
offering them as wish-list items told a new firm they were missing. Pin that
neither comes back as a questionnaire option."""
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "docs" / "firm-dashboard" / "index.html"


def _questionnaire_values() -> list[str]:
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r'<form id="dr-questionnaire-form">(.*?)</form>', html, re.S)
    assert m, "questionnaire form not found -- this test would pass on a missing form"
    return re.findall(r'name="feature" value="([^"]+)"', m.group(1))


def test_questionnaire_still_has_options():
    assert len(_questionnaire_values()) >= 4


def test_questionnaire_does_not_offer_shipped_features():
    vals = [v.lower() for v in _questionnaire_values()]
    assert not any("slack" in v or "teams" in v for v in vals), vals
    assert "custom reminder schedule" not in vals, vals
