"""GATE-131: a DIED_WITHDRAWN rule_change must publish, not stop the build.

build_change_events.py accepts DIED_WITHDRAWN (VALID_RULE_CHANGE_STATUSES), so a
proposal dying is a routine data event. COPY-60's guard raised on it after
main() had already overwritten every state page. Now it is its own disposition:
its own /rule-changes/ section, never upcoming, never "not yet final", and the
guard runs before main() writes anything.

    python -m pytest scripts/test_gate131_died_withdrawn.py -q
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import generate  # noqa: E402

SECTION_RE = re.compile(r"<h2>([^<]*)\((\d+)\)</h2>(.*?)(?=<h2>|<p class=\"backlink\"|$)", re.S)
JUR_RE = re.compile(r'<span class="rc-jurisdiction">([^<]+)</span>')

DIED = [
    {"kind": "rule_change", "event_id": "g131-future", "status": "DIED_WITHDRAWN",
     "jurisdiction": "Gate131 Future", "jurisdiction_slug": "g131-future", "upcoming": True,
     "effective_date": "2099-01-01", "summary_public": "probe", "citation_url": "https://example.gov/a"},
    {"kind": "rule_change", "event_id": "g131-past", "status": "DIED_WITHDRAWN",
     "jurisdiction": "Gate131 Past", "jurisdiction_slug": "g131-past", "upcoming": False,
     "effective_date": "2020-01-01", "summary_public": "probe", "citation_url": "https://example.gov/b"},
    {"kind": "rule_change", "event_id": "g131-undated", "status": "DIED_WITHDRAWN",
     "jurisdiction": "Gate131 Undated", "jurisdiction_slug": "g131-undated",
     "summary_public": "probe", "citation_url": "https://example.gov/c"},
]


def _sections(html):
    return {head.strip(): (int(n), JUR_RE.findall(body)) for head, n, body in SECTION_RE.findall(html)}


def _page_with(tmp_path, monkeypatch, extra):
    raw = json.loads(generate.REG_CHANGE_EVENTS_PATH.read_text(encoding="utf-8"))
    raw["events"] = raw.get("events", []) + extra
    p = tmp_path / "reg_change_events.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(generate, "REG_CHANGE_EVENTS_PATH", p)
    return generate.build_rule_changes_page()


def test_guard_accepts_died_withdrawn():
    generate._rc_assert_bucketable(DIED)


def test_died_withdrawn_is_never_upcoming_or_not_yet_final():
    for e in DIED:
        assert not generate._is_still_upcoming(e)
        assert not generate._rc_is_not_yet_final(e)
        assert not generate._rc_is_enacted(e)


def test_feed_gives_died_its_own_section_and_leaves_others_unchanged(tmp_path, monkeypatch):
    base = _sections(generate.build_rule_changes_page())
    probe = _sections(_page_with(tmp_path, monkeypatch, DIED))
    died_head = "Died or withdrawn"
    assert died_head not in base
    n, jurs = probe.pop(died_head)
    assert n == 3 and sorted(jurs) == sorted(e["jurisdiction"] for e in DIED)
    # every other section, heading and count, is exactly what live data renders
    assert {h: v[0] for h, v in probe.items()} == {h: v[0] for h, v in base.items()}
    for _h, (_n, others) in probe.items():
        assert not any(j.startswith("Gate131") for j in others)


def test_died_card_never_reads_in_force(tmp_path, monkeypatch):
    html = _page_with(tmp_path, monkeypatch, DIED)
    body = html[html.index("<h2>Died or withdrawn"):]
    assert body.count("withdrawn, so it will not take effect") == 2  # the 2 dated probes
    assert "not yet in force" not in body
    assert "re-verify on or after" not in body


def test_main_checks_buckets_before_first_write():
    src = open(os.path.join(ROOT, "generate.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    seg = ast.get_source_segment(src, fn)
    guard, first_write = seg.find("_rc_assert_bucketable("), seg.find(".write_text(")
    assert guard != -1 and first_write != -1 and guard < first_write
    assert guard < seg.find("SITE_DIR.mkdir(")
