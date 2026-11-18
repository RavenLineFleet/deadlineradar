"""COPY-59: the homepage "not yet final" tile must equal the feed's own buckets.

The tile on docs/index.html summarizes /rule-changes/. It used to count dated
future changes (including a signed law) under "pending" while dropping every
undated proposal. It must now equal the sum of the feed page's two
"Not yet final ..." section counts, read from the same built docs.

    python -m pytest scripts/test_copy59_pending_tile_matches_feed.py -q
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "docs", "index.html")
FEED = os.path.join(ROOT, "docs", "rule-changes", "index.html")

TILE_RE = re.compile(
    r'<span class="n">(\d+)</span><span class="lbl">tracked rule changes not yet final</span>'
)
FEED_RE = re.compile(r"<h2>Not yet final[^<(]*\((\d+)\)</h2>")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_tile_equals_feed_not_yet_final_buckets():
    index = _read(INDEX)
    if 'class="lbl">' not in index:
        pytest.skip("stats row not rendered")
    tile = TILE_RE.findall(index)
    if not tile:
        # Tile is withheld while the monitor is stale; the old label must not survive either.
        assert "pending rule changes tracked" not in index
        pytest.skip("monitor-freshness gate withheld the tile this build")
    assert len(tile) == 1
    feed_counts = [int(n) for n in FEED_RE.findall(_read(FEED))]
    assert feed_counts, "feed page lost its 'Not yet final' headings"
    assert int(tile[0]) == sum(feed_counts)


def test_old_mislabel_gone():
    assert "pending rule changes tracked" not in _read(INDEX)
