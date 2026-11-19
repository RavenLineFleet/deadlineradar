"""COPY-65: the homepage conflict tile counts JURISDICTIONS, like its label says.

COPY-61 deduped the sibling pending tile on jurisdiction_slug; the conflict tile
still counted events. Two conflict events for one jurisdiction must render 1.

    python -m pytest scripts/test_copy65_conflict_tile_counts_jurisdictions.py -q
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import generate  # noqa: E402

CONF_RE = re.compile(
    r'<span class="n">(\d+)</span><span class="lbl">jurisdictions? with a live source conflict</span>'
)


def _conf(n_events_per_slug):
    return [
        {"kind": "source_conflict", "jurisdiction_slug": slug, "event_id": f"{slug}-{i}"}
        for slug, n in n_events_per_slug.items()
        for i in range(n)
    ]


def test_duplicate_conflict_events_count_once():
    html = generate._render_monitor_stat_items(["idaho"], _conf({"oregon": 2}))
    assert CONF_RE.findall(html) == ["1"]
    assert "1</span><span class=\"lbl\">jurisdiction with a live" in html


def test_distinct_conflicts_each_count():
    html = generate._render_monitor_stat_items([], _conf({"oregon": 2, "guam": 1, "michigan": 3}))
    assert CONF_RE.findall(html) == ["3"]
    assert "jurisdictions with a live" in html


def test_pending_tile_unchanged():
    html = generate._render_monitor_stat_items(["a", "b", "c"], [])
    assert '<span class="n">3</span><span class="lbl">states with a rule change not yet final' in html
    assert CONF_RE.findall(html) == ["0"]


def test_built_homepage_matches_distinct_conflict_slugs():
    index = open(os.path.join(ROOT, "docs", "index.html"), encoding="utf-8").read()
    tile = CONF_RE.findall(index)
    if not tile:
        pytest.skip("monitor-freshness gate withheld the tile this build")
    raw = json.loads(generate.REG_CHANGE_EVENTS_PATH.read_text(encoding="utf-8"))
    slugs = {e.get("jurisdiction_slug") for e in raw.get("events", []) if e.get("kind") == "source_conflict"}
    assert tile == [str(len(slugs))]
