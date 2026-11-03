"""Repo-root pytest guard (AuditLab PROC-19 residual, 2026-11-03).

scripts/conftest.py clears git's hook env (GIT_DIR & co.) but is directory-scoped, so test files
outside scripts/ (reminders/, social/) would run a bare `pytest` from the repo root unguarded.
This runs the same guard for anything collected under the repo root. It is only loaded because
pytest.ini pins rootdir/confcutdir to the repo root (PROC-22): without it, a pytest launched from
inside reminders/ or social/ takes that subdir as rootdir and never reaches this file.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "scripts" / "conftest.py"))
