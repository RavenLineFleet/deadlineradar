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


_MUTATORS = {"write_text", "write_bytes", "mkdir", "makedirs", "touch", "unlink", "remove",
             "rmdir", "rmtree", "rename", "copy", "copy2", "copyfile", "copytree", "move",
             "symlink_to", "hardlink_to"}


def _is_mutation(call):
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr in _MUTATORS:
        return True
    if isinstance(f, ast.Name) and f.id == "open":
        mode = call.args[1] if len(call.args) > 1 else next(
            (k.value for k in call.keywords if k.arg == "mode"), None)
        if mode is None:
            return False
        if not isinstance(mode, ast.Constant):
            return True  # non-literal mode: assume it writes
        return any(c in str(mode.value) for c in "wax+")
    return False


def _writes(node, funcs, depth, seen):
    """True if node, or a module function it calls (to `depth`), mutates the filesystem."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        if _is_mutation(n):
            return True
        f = n.func
        if depth and isinstance(f, ast.Name) and f.id in funcs and f.id not in seen:
            seen.add(f.id)
            if _writes(funcs[f.id], funcs, depth - 1, seen):
                return True
    return False


def _first_write_vs_guard(src):
    """(guard_line, first_mutating_line) over main(), transitive through module functions."""
    mod = ast.parse(src)
    funcs = {n.name: n for n in mod.body if isinstance(n, ast.FunctionDef)}
    guard = first = None
    for n in ast.walk(funcs["main"]):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Name) and n.func.id == "_rc_assert_bucketable":
            guard = n.lineno if guard is None else min(guard, n.lineno)
        if _writes(n, funcs, 4, set()):
            first = n.lineno if first is None else min(first, n.lineno)
    return guard, first


def test_main_checks_buckets_before_first_write():
    # AuditLab 2026-11-18 (GATE-131 close): the earlier textual find() on .write_text(/
    # SITE_DIR.mkdir( missed write_bytes and any writing callee. AST, transitive to depth 4.
    src = open(os.path.join(ROOT, "generate.py"), encoding="utf-8").read()
    guard, first = _first_write_vs_guard(src)
    assert guard is not None and first is not None and guard < first


def test_guard_order_detector_catches_a_write_above_the_guard():
    # Positive controls: the detector must fail a direct write_bytes and a writing callee
    # (two calls deep) placed above the guard -- the shapes the textual check let through.
    src = open(os.path.join(ROOT, "generate.py"), encoding="utf-8").read()
    anchor = "    _rc_assert_bucketable(json.loads(REG_CHANGE_EVENTS_PATH"
    assert src.count(anchor) == 1
    helpers = "\n\ndef _g131_probe_writer():\n    _g131_h()\n\n\ndef _g131_h():\n    open('x', 'wb').close()\n"
    for inject in ('    (SITE_DIR / "x").write_bytes(b"")\n', "    _g131_probe_writer()\n"):
        mutant = src.replace(anchor, inject + anchor) + helpers
        guard, first = _first_write_vs_guard(mutant)
        assert first < guard, inject
