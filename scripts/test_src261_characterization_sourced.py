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


def test_source_conflict_cards_in_scope(tmp_path):
    errs = pg.check_rule_change_characterization_sourced(
        _repo(tmp_path, [_ev("A minor wording conflict.", kind="source_conflict")]))
    assert len(errs) == 1 and "'minor'" in errs[0]


def test_other_kinds_out_of_scope(tmp_path):
    assert pg.check_rule_change_characterization_sourced(
        _repo(tmp_path, [_ev("A minor wording conflict.", kind="other")])) == []


def test_unreadable_file_is_an_error_not_a_pass(tmp_path):
    errs = pg.check_rule_change_characterization_sourced(tmp_path)
    assert len(errs) == 1 and "measured nothing" in errs[0]


def _real(event_id: str) -> dict:
    events = json.loads((REPO_ROOT / "data" / "reg_change_events.json").read_text(encoding="utf-8"))["events"]
    return next(e for e in events if e["event_id"] == event_id)


def test_src263_pa_card_names_the_b1_exam_exception():
    # AuditLab SRC-263: 4.2(b) is "Except as provided under subsection (b.1)"; (b)(3) is not the exam gate.
    e = _real("pennsylvania-regwatch-53f514f989a5")
    assert "(b.1)" in e["summary_public"] and "before taking the CPA examination" not in e["summary_public"]
    assert "Except as provided under subsection (b.1)" in e["status_evidence"]


def test_src264_ok_card_names_the_emergency_track():
    # AuditLab SRC-264: the cited RIS is for EMERGENCY rules; "proposed" may only describe the permanent track.
    e = _real("oklahoma-regwatch-ff8bf9c424be")
    assert "emergency" in e["summary_public"] and "EMERGENCY RULES" in e["status_evidence"]
    assert "implementing rules are proposed" not in e["summary_public"]


def test_src265_ok_card_names_the_approval_condition():
    # AuditLab SRC-265: the emergency rules took effect 11-01 only IF the Governor + Legislature approved
    # them within 45 days; the card must state that condition, sourced to the Sept 2026 Bulletin.
    e = _real("oklahoma-regwatch-ff8bf9c424be")
    assert "If the rules are approved, they will become effective on November 1, 2026." in e["summary_public"]
    assert "45 days to approve or disapprove" in e["summary_public"]
    assert "September%202026%20Bulletin.pdf" in e["status_evidence"]
    assert "If the rules are approved, they will become effective on November 1, 2026." in e["status_evidence"]
