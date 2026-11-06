"""SRC-175: an hour figure the cited text does not state in hours must carry its
caveat on the state page, and the state page's sourcing claim must name the date."""
import json
import re
from pathlib import Path

import generate

ROOT = Path(__file__).resolve().parent.parent


def _records():
    return json.loads((ROOT / "data" / "cpe_hours.json").read_text(encoding="utf-8"))["records"]


def _by_slug():
    return {r["state_slug"]: r for r in _records()}


def test_reverse_link_carries_basis_note_only_where_set():
    by_slug = _by_slug()
    ne = generate._cpe_hours_reverse_link_html("nebraska", by_slug)
    assert "ten days" in ne and "8-hours-per-day conversion" in ne
    nc = generate._cpe_hours_reverse_link_html("north-carolina", by_slug)
    assert "2,000 a year" in nc and "50-minutes-per-CPE-hour conversion" in nc
    # Negative control: a state with no note renders no extra sentence.
    tx = generate._cpe_hours_reverse_link_html("texas", by_slug)
    assert "conversion" not in tx and "Full CPE requirements" in tx


def test_every_unit_conversion_record_declares_a_basis_note():
    # A record whose own notes admit a unit conversion must surface it.
    pat = re.compile(r"conversion|minutes-based|rather than stating hours", re.I)
    missing = [r["id"] for r in _records() if pat.search(r.get("notes") or "") and not r.get("hours_basis_note")]
    assert missing == []


def test_trust_line_scopes_claim_to_renewal_date_when_asked():
    scoped = generate.trust_line("2026-11-01", "https://example.gov/", True, date_scoped=True)
    assert "renewal date checked against the state's codified statute or administrative rule" in scoped
    unscoped = generate.trust_line("2026-11-01", "https://example.gov/", True)
    assert "renewal date checked" not in unscoped
    assert "checked against the state's codified statute or administrative rule" in unscoped


def test_cpe_page_summary_makes_no_rule_sourcing_claim_where_a_basis_note_is_set():
    # SRC-176: meta, og and twitter descriptions all come from one string.
    from html import unescape

    for rec in _records():
        page = (ROOT / "docs" / f"{rec['state_slug']}-cpa-cpe-requirements" / "index.html").read_text(encoding="utf-8")
        metas = [unescape(m) for m in re.findall(r'<meta (?:name|property)="(?:description|og:description|twitter:description)" content="([^"]*)"', page)]
        assert len(metas) == 3, rec["id"]
        for meta in metas:
            if rec.get("hours_basis_note"):
                assert "sourced to" not in meta, rec["id"]
                assert "does not state this figure in hours" in meta, rec["id"]
            else:
                assert "does not state this figure in hours" not in meta, rec["id"]
