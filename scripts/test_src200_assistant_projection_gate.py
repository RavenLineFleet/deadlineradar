"""SRC-200: every populated dataset field is served by the assistant API or explicitly withheld."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preship_gate as g

REPO = Path(__file__).resolve().parent.parent


def _copy(tmp_path: Path) -> Path:
    (tmp_path / "worker" / "src").mkdir(parents=True)
    shutil.copy(REPO / "worker" / "src" / "assistant.ts", tmp_path / "worker" / "src" / "assistant.ts")
    shutil.copytree(REPO / "data", tmp_path / "data")
    return tmp_path


def _rewrite_ts(root: Path, old: str, new: str) -> None:
    path = root / "worker" / "src" / "assistant.ts"
    src = path.read_text(encoding="utf-8")
    assert src.count(old) == 1
    path.write_text(src.replace(old, new), encoding="utf-8")


def test_clean_at_tip():
    assert g.check_assistant_projection_classifies_every_field(REPO) == []


def test_new_unclassified_field_fails(tmp_path):
    root = _copy(tmp_path)
    path = root / "data" / "reinstatement.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][0]["penalty_new_caveat"] = "confirm with the Board"
    path.write_text(json.dumps(data), encoding="utf-8")
    errs = g.check_assistant_projection_classifies_every_field(root)
    assert len(errs) == 1 and "penalty_new_caveat" in errs[0]


def test_dropping_a_field_from_the_projection_fails(tmp_path):
    root = _copy(tmp_path)
    src = (root / "worker" / "src" / "assistant.ts").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines(keepends=True) if l.strip().startswith("penalty_ethics_label: typeof r."))
    _rewrite_ts(root, line, "")
    errs = g.check_assistant_projection_classifies_every_field(root)
    assert len(errs) == 1 and "penalty_ethics_label" in errs[0]


def test_withheld_field_that_is_served_fails(tmp_path):
    root = _copy(tmp_path)
    needle = "    reinstatement_fee_usd: typeof r.reinstatement_fee_usd"
    _rewrite_ts(root, needle, "    leak: r.verification_history,\n" + needle)
    errs = g.check_assistant_projection_classifies_every_field(root)
    assert len(errs) == 1 and "verification_history" in errs[0] and "withheld" in errs[0]


def test_renamed_lookup_fails_closed(tmp_path):
    root = _copy(tmp_path)
    _rewrite_ts(root, "export function lookupAssistantRenewalFee", "export function renewalFeeLookup")
    errs = g.check_assistant_projection_classifies_every_field(root)
    assert len(errs) == 1 and "renewal_fees.json" in errs[0]


def test_new_fields_are_scanned_by_the_leak_guard():
    assert "penalty_ethics_label" in g._ASSISTANT_API_FIELDS_BY_DATASET["reinstatement.json"]
    assert "data_gap_note" in g._ASSISTANT_API_FIELDS_BY_DATASET["renewal_fees.json"]
    assert "grace_period_note" in g._ASSISTANT_API_FIELDS_BY_DATASET["cpa_deadlines.json"]
    assert "ethics_hours_note" in g._ASSISTANT_API_FIELDS_BY_DATASET["cpe_hours.json"]
    assert "hours_basis_note" in g._ASSISTANT_API_FIELDS_BY_DATASET["cpe_hours.json"]
