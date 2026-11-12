"""BACKLOG 11-12 #8: /year-end-renewals/ gets internal links from the homepage,
/for-firms/ and the firm landing pages whose own firm date is the roundup's
target. All of them stop rendering once the target date has passed."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate as g  # noqa: E402

DOCS = ROOT / "docs"
LINK = 'href="/year-end-renewals/"'
FIRM_LINK_TEXT = "This firm deadline is one of several landing on December 31, 2026"


def _by_slug():
    recs = json.loads((ROOT / "data" / "cpa_deadlines.json").read_text(encoding="utf-8"))["records"]
    out = {}
    for r in recs:
        out.setdefault(r["state_slug"], []).append(r)
    return out


def test_promo_renders_before_target_and_not_after():
    assert LINK in g._year_end_promo_html(g.YEAR_END_TARGET_DATE)
    assert g._year_end_promo_html(g.YEAR_END_TARGET_DATE + timedelta(days=1)) == ""


def test_firm_link_only_for_listed_state():
    assert g._year_end_firm_link_html("kansas", {"kansas"})
    assert g._year_end_firm_link_html("nevada", {"kansas"}) == ""
    assert g._year_end_firm_link_html("kansas", set()) == ""
    assert g._year_end_firm_link_html("kansas", None) == ""


def test_homepage_and_for_firms_link_roundup():
    # Only meaningful while the roundup is live; the built pages are from today's build.
    if date.today() > g.YEAR_END_TARGET_DATE:
        return
    for page in ("index.html", "for-firms/index.html"):
        assert LINK in (DOCS / page).read_text(encoding="utf-8"), page


def test_firm_pages_link_exactly_the_firm_date_cohort():
    if date.today() > g.YEAR_END_TARGET_DATE:
        return
    target = g.YEAR_END_TARGET_DATE.isoformat()
    want = {r["state_slug"] for r in g._year_end_dec31_rows(_by_slug()) if r["firm_date"] == target}
    linked, unlinked = set(), set()
    for d in DOCS.glob("*-cpa-firm-renewal"):
        slug = d.name[: -len("-cpa-firm-renewal")]
        (linked if FIRM_LINK_TEXT in (d / "index.html").read_text(encoding="utf-8") else unlinked).add(slug)
    assert linked, "no firm page links the roundup -- this test would pass on a missing link"
    assert linked <= want, linked - want
    assert want & unlinked == set(), want & unlinked
    # Nevada is in the roundup for its individual date only; its firm date is Jan 31.
    assert "nevada" in unlinked
