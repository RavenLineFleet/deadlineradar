"""STALE-52 (AuditLab, 2026-10-12): a state page's page-level "Last verified" date and the seal's
data-verified attribute must be the OLDEST record's date, not whichever record sorts first.

    python -B -m pytest scripts/test_state_page_last_verified.py -q

Runs build_state_page() on every real multi-date state with the records in BOTH orders, so a fix that is
only right by ordering luck (the 15 states where records[0] happens to be oldest) still fails.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(REPO_ROOT))
import generate  # noqa: E402

DATA = json.loads((REPO_ROOT / "data" / "cpa_deadlines.json").read_text(encoding="utf-8"))
AS_OF = date.fromisoformat(DATA["as_of_date"])
BY_SLUG = {}
for _r in DATA["records"]:
    BY_SLUG.setdefault(_r["state_slug"], []).append(_r)
MULTI_DATE = {s: rs for s, rs in BY_SLUG.items() if len({r["last_verified"] for r in rs}) > 1}


def _page_dates(slug, recs):
    _title, html = generate.build_state_page(slug, recs, AS_OF, BY_SLUG)
    # dr-seal now always carries the dr-seal--v2 modifier class too (2026-10-21
    # all-state seal rollout), so match on the leading class token, not an exact string.
    seal = re.search(r'class="dr-seal[^"]*" data-verified="([0-9-]{10})"', html)
    # STALE-73 (AuditLab, 2026-10-21): the visible trust-line text is humanized
    # ("Last verified: October 14, 2026") since c5e865990 -- data-verified stays
    # ISO (runtime staleness JS parses it), only the visible capture changed shape.
    trust = re.search(r'<strong data-verified="([0-9-]{10})">Last verified: ([A-Za-z]+ \d{1,2}, \d{4})</strong>', html)
    assert seal and trust, slug
    return seal.group(1), trust.group(1), trust.group(2)


def test_there_are_multi_date_states_to_exercise():
    assert len(MULTI_DATE) >= 10      # 19 on 2026-10-12; if the data converges this guard needs a synthetic case


def test_page_date_is_the_oldest_record_in_either_order():
    for slug, recs in MULTI_DATE.items():
        oldest = min(r["last_verified"] for r in recs)
        oldest_human = generate.fmt_date(date.fromisoformat(oldest))
        for order in (recs, list(reversed(recs))):
            assert _page_dates(slug, order) == (oldest, oldest, oldest_human), (slug, [r["last_verified"] for r in order])
