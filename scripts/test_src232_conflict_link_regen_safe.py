"""SRC-232 (AuditLab, 2026-11-11): a producer run must not change any conflict card's links.

    python -m pytest scripts/test_src232_conflict_link_regen_safe.py -q

build_change_events.py took a conflict card's citation_url from the mobility record's
citation_url, which is the mobility page's primary link. For Michigan (727a, individual
privilege) and NMI (the 2002 enacting-act PDF) that document doesn't carry the conflict's
claim, so every regen reverted the hand-fixed card links, and the clobber report exits 0.
The record now carries conflict_citation_url; this pins build() output == committed.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_change_events as bce  # noqa: E402

LINK_FIELDS = ("citation", "citation_url", "secondary_url", "secondary_citation", "additional_sources")


def _committed() -> dict:
    data = json.loads((REPO_ROOT / "data" / "reg_change_events.json").read_text(encoding="utf-8"))
    return {e["event_id"]: e for e in data["events"] if e.get("kind") == "source_conflict"}


def _built() -> dict:
    events = bce.build(date.today())[0]
    return {e["event_id"]: e for e in events if e.get("kind") == "source_conflict"}


def test_regen_keeps_every_conflict_card_link():
    committed, built = _committed(), _built()
    assert set(committed) == set(built)
    assert len(committed) == 4  # positive control: the population is the 4 live cards
    for eid, e in committed.items():
        for f in LINK_FIELDS:
            assert built[eid].get(f) == e.get(f), (eid, f, built[eid].get(f), e.get(f))


def test_mi_and_nmi_cards_link_the_section_the_claim_is_about():
    built = _built()
    assert built["michigan-mobility-source-conflict"]["citation_url"].endswith("objectName=mcl-339-728")
    assert built["northern-mariana-islands-mobility-source-conflict"]["citation_url"] == \
        "https://cnmilaw.gov/commonwealth-code/3742"


def test_mobility_page_primary_link_is_untouched():
    rules = {r["state_slug"]: r for r in json.loads(bce.RULES.read_text(encoding="utf-8"))["records"]}
    assert rules["michigan"]["citation_url"].endswith("objectName=mcl-339-727a")
    assert rules["northern-mariana-islands"]["citation_url"].endswith("pl13-52.pdf")


def test_override_must_be_http(monkeypatch, tmp_path):
    rules = json.loads(bce.RULES.read_text(encoding="utf-8"))
    for r in rules["records"]:
        if r["state_slug"] == "michigan":
            r["conflict_citation_url"] = "javascript:alert(1)"
    p = tmp_path / "mobility_rules.json"
    p.write_text(json.dumps(rules), encoding="utf-8")
    monkeypatch.setattr(bce, "RULES", p)
    built = _built()
    assert built["michigan-mobility-source-conflict"]["citation_url"].endswith("objectName=mcl-339-727a")
