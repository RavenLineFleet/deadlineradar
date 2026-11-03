"""Repo-root pytest guard (AuditLab PROC-19 residual, 2026-11-03).

scripts/conftest.py clears git's hook env (GIT_DIR & co.) but is directory-scoped, so test files
outside scripts/ (reminders/, social/) would run a bare `pytest` from the repo root unguarded.
This runs the same guard for anything collected under the repo root.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "scripts" / "conftest.py"))
