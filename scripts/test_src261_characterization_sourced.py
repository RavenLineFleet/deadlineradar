"""SRC-261 (AuditLab, 2026-11-15): a /rule-changes/ card may not characterize a change
("overhauls", "simplifies", "minor", "outdated") in our own voice.

    python -m pytest scripts/test_src261_characterization_sourced.py -q

The word passes only inside a double-quoted span that status_evidence also carries.
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import preship_gate as pg  # noqa: E402


def _repo(tmp_path: Path, events: list) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "reg_change_events.json").write_text(json.dumps({"events": events}), encoding="utf-8")
    return tmp_path


def _ev(summary: str, evidence: str = "", kind: str = "rule_change") -> dict:
    return {"event_id": "x-1", "kind": kind, "summary_public": summary, "status_evidence": evidence}


def test_real_corpus_is_clean():
    assert pg.check_rule_change_characterization_sourced(REPO_ROOT) == []


def test_unquoted_characterization_fails(tmp_path):
    errs = pg.check_rule_change_characterization_sourced(_repo(tmp_path, [_ev("Act 1 overhauls CPA licensure.")]))
    assert len(errs) == 1 and "[SRC-261]" in errs[0] and "outside any quoted span" in errs[0]


def test_quote_not_in_evidence_fails(tmp_path):
    errs = pg.check_rule_change_characterization_sourced(
        _repo(tmp_path, [_ev('The board says it "removes outdated language".', "unrelated text")]))
    assert len(errs) == 1 and "does not carry" in errs[0]


def test_quote_carried_by_evidence_passes(tmp_path):
    ev = 'Descriptive summary: "(3) removes outdated language."'
    assert pg.check_rule_change_characterization_sourced(
        _repo(tmp_path, [_ev('The notice says it "removes outdated language."', ev)])) == []


def test_word_outside_quote_fails_even_when_another_quote_is_sourced(tmp_path):
    ev = '"takes effect November 1"'
    errs = pg.check_rule_change_characterization_sourced(
        _repo(tmp_path, [_ev('It "takes effect November 1" and simplifies reciprocity.', ev)]))
    assert len(errs) == 1 and "'simplifies'" in errs[0]


def test_source_conflict_cards_out_of_scope(tmp_path):
    assert pg.check_rule_change_characterization_sourced(
        _repo(tmp_path, [_ev("A minor wording conflict.", kind="source_conflict")])) == []


def test_unreadable_file_is_an_error_not_a_pass(tmp_path):
    errs = pg.check_rule_change_characterization_sourced(tmp_path)
    assert len(errs) == 1 and "measured nothing" in errs[0]
