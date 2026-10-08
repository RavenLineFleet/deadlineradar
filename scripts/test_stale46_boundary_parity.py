"""STALE-46: the Python pre-expiry lane and the Worker guard must trip at the same INSTANT.

    python -m pytest scripts/test_stale46_boundary_parity.py -q
"""
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402
import cpa_deadlines_staleness_check as cdsc  # noqa: E402

REPO = Path(HERE).parent
UTC = datetime.timezone.utc


def _node_stale(verified: str, instant: datetime.datetime) -> bool:
    ms = int(instant.timestamp() * 1000)
    js = (f"const v=new Date('{verified}T00:00:00Z');"
          f"console.log(Math.round((new Date({ms}).getTime()-v.getTime())/86_400_000)>30)")
    return subprocess.run(["node", "-e", js], capture_output=True, text=True).stdout.strip() == "true"


def test_real_repo_boundary_parity_clean():
    assert gate.check_stale_boundary_parity(REPO) == []


def test_auditlab_example_flips_at_1200z_on_day_plus_30():
    v = datetime.date(2026, 9, 21)
    edge = cdsc.stale_instant(v)
    assert edge == datetime.datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    assert not _node_stale("2026-09-21", edge - datetime.timedelta(seconds=1))
    assert _node_stale("2026-09-21", edge)


def test_python_age_and_staleness_match_node_across_the_edge():
    v = datetime.date(2026, 9, 21)
    edge = cdsc.stale_instant(v)
    for delta in (-1, 0, 1):
        now = edge + datetime.timedelta(seconds=delta)
        py_stale = cdsc.runtime_age_days(v, now) > cdsc.STALENESS_THRESHOLD_DAYS
        assert py_stale == _node_stale("2026-09-21", now), delta


def test_gate_fails_if_python_lane_reverts_to_calendar_plus_31(monkeypatch):
    # mutation: half-day -> full day is the old "verified + 31" boundary
    monkeypatch.setattr(cdsc, "STALE_ROUND_HALF_DAYS", 1.0)
    errors = gate.check_stale_boundary_parity(REPO)
    assert errors and all("STALE-46" in e for e in errors)


def test_gate_fails_if_worker_rounding_changes(tmp_path):
    (tmp_path / "worker" / "src").mkdir(parents=True)
    ts = (REPO / "worker" / "src" / "deadline.ts").read_text(encoding="utf-8")
    assert "Math.round((realToday" in ts
    (tmp_path / "worker" / "src" / "deadline.ts").write_text(ts.replace("Math.round((realToday", "Math.floor((realToday"), encoding="utf-8")
    shutil.copytree(REPO / "scripts", tmp_path / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    errors = gate.check_stale_boundary_parity(tmp_path)
    assert len(errors) == 1 and "STALE-46" in errors[0]


def test_missing_deadline_ts_fails_closed(tmp_path):
    errors = gate.check_stale_boundary_parity(tmp_path)
    assert len(errors) == 1 and "STALE-46" in errors[0]


def test_python_only_threshold_change_is_caught_by_the_gate_itself(monkeypatch):
    # STALE-47 MUT-1: cdsc threshold 30 -> 45 while deadline.ts stays 30
    monkeypatch.setattr(cdsc, "STALENESS_THRESHOLD_DAYS", 45)
    errors = gate.check_stale_boundary_parity(REPO)
    assert any("STALE-47" in e for e in errors)


def test_runtime_age_days_classification_is_probed_by_the_gate(monkeypatch):
    # STALE-47 MUT-2: age classifier reverts to a calendar floor, stale_instant untouched
    monkeypatch.setattr(cdsc, "runtime_age_days", lambda v, now: (now.date() - v).days)
    errors = gate.check_stale_boundary_parity(REPO)
    assert any("STALE-47" in e for e in errors)


def test_gate_internal_threshold_mirror_drift_is_caught(monkeypatch):
    # STALE-48 M1: preship_gate's own hand-kept mirror drifts from deadline.ts
    monkeypatch.setattr(gate, "CPA_DEADLINES_STALENESS_THRESHOLD_DAYS", 20)
    assert any("STALE-48" in e for e in gate.check_stale_boundary_parity(REPO))


def test_generate_py_threshold_drift_is_caught(tmp_path):
    # STALE-48 M2: generate.py's STALENESS_THRESHOLD_DAYS drifts from deadline.ts
    (tmp_path / "worker" / "src").mkdir(parents=True)
    shutil.copy(REPO / "worker" / "src" / "deadline.ts", tmp_path / "worker" / "src" / "deadline.ts")
    shutil.copytree(REPO / "scripts", tmp_path / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    src = (REPO / "generate.py").read_text(encoding="utf-8")
    assert "\nSTALENESS_THRESHOLD_DAYS = 30" in src
    (tmp_path / "generate.py").write_text(src.replace("\nSTALENESS_THRESHOLD_DAYS = 30", "\nSTALENESS_THRESHOLD_DAYS = 45", 1), encoding="utf-8")
    errors = gate.check_stale_boundary_parity(tmp_path)
    assert any("STALE-48" in e and "generate.py" in e for e in errors)
