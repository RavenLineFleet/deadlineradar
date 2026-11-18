"""COPY-59/60/61: the homepage "not yet final" tile must agree with the feed.

The tile on docs/index.html summarizes /rule-changes/. COPY-59: it must count
the feed's two "Not yet final ..." sections, not dated future changes.
COPY-61: its unit is STATES, because one docket can be published as several
provision-level events (Idaho 24-3001-2601 is two). COPY-60: an enacted status
(ENACTED_DATE_PENDING) must never sit under "Not yet final".

    python -m pytest scripts/test_copy59_pending_tile_matches_feed.py -q
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "docs", "index.html")
FEED = os.path.join(ROOT, "docs", "rule-changes", "index.html")

TILE_RE = re.compile(
    r'<span class="n">(\d+)</span><span class="lbl">states with a rule change not yet final</span>'
)
SECTION_RE = re.compile(r"<h2>([^<]*)\((\d+)\)</h2>(.*?)(?=<h2>|</main>|$)", re.S)
JUR_RE = re.compile(r'<span class="rc-jurisdiction">([^<]+)</span>')
BADGE_RE = re.compile(r'<span class="rc-badge">([^<]+)</span>')


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _not_yet_final_sections():
    return [
        (head, int(n), body)
        for head, n, body in SECTION_RE.findall(_read(FEED))
        if head.startswith("Not yet final")
    ]


def test_tile_equals_states_in_feed_not_yet_final_sections():
    index = _read(INDEX)
    if 'class="lbl">' not in index:
        pytest.skip("stats row not rendered")
    tile = TILE_RE.findall(index)
    if not tile:
        assert "tracked rule changes not yet final" not in index
        pytest.skip("monitor-freshness gate withheld the tile this build")
    assert len(tile) == 1
    sections = _not_yet_final_sections()
    assert sections, "feed page lost its 'Not yet final' headings"
    states = set()
    for _head, n, body in sections:
        jurs = JUR_RE.findall(body)
        assert len(jurs) == n, "section count != its cards"
        states.update(jurs)
    assert int(tile[0]) == len(states)


def test_no_enacted_badge_under_not_yet_final():
    for head, _n, body in _not_yet_final_sections():
        for badge in BADGE_RE.findall(body):
            assert not badge.startswith("Enacted"), (head, badge)
            assert badge != "Adopted rule", (head, badge)


def test_old_labels_gone():
    index = _read(INDEX)
    assert "pending rule changes tracked" not in index
    assert "tracked rule changes not yet final" not in index
