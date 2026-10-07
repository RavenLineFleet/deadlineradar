"""An advisory must never change the preship verdict (AuditLab ADVIS-1, 2026-10-07):
a non-SystemExit exception from one advisory used to exit 1 after printing PASS and skip the rest."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import preship_gate as pg  # noqa: E402


def test_advisory_exception_is_contained_and_later_advisories_still_run(monkeypatch, capsys):
    ran = []
    boom = lambda *a: (_ for _ in ()).throw(ValueError("not-json"))  # noqa: E731
    monkeypatch.setattr(pg, "print_silent_drop_advisory", boom)
    monkeypatch.setattr(pg, "print_gap_list_advisory", lambda *a: ran.append("gap"))
    monkeypatch.setattr(pg, "print_double_hyphen_backlog_advisory", lambda *a: ran.append("last"))
    for name in dir(pg):
        if name.startswith("print_") and name.endswith("_advisory") and name not in (
                "print_silent_drop_advisory", "print_gap_list_advisory", "print_double_hyphen_backlog_advisory"):
            monkeypatch.setattr(pg, name, lambda *a: None)
    pg._run_advisories(Path("."), [], Path("."))  # must not raise
    assert ran == ["gap", "last"]
    assert "print_silent_drop_advisory errored, ignored: ValueError: not-json" in capsys.readouterr().out


def test_advisory_systemexit_is_contained(monkeypatch):
    for name in dir(pg):
        if name.startswith("print_") and name.endswith("_advisory"):
            monkeypatch.setattr(pg, name, lambda *a: None)
    monkeypatch.setattr(pg, "print_cpe_hours_staleness_advisory", lambda *a: sys.exit("skip"))
    pg._run_advisories(Path("."), [], Path("."))
