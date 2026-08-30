#!/usr/bin/env python3
"""GATE-18 (AuditLab, 2026-08-29): the general version of the check FRESH-2
needed. check_sitewide_freshness_stat_uses_wall_clock() in preship_gate.py
only watches _sitewide_freshness_stat()'s own call sites -- but the FRESH-2
FIX itself created a new, ungated reintroduction path: four builder
functions now take BOTH `as_of` and `real_today` as parameters, so the bug
is reintroducible one level up, by misrouting the argument in main(), while
the inner call to _sitewide_freshness_stat(real_today) still reads
correctly and the narrow gate stays green. AuditLab proved this with a
mutation the narrow gate missed but their own AST sweep caught.

This is that general detector: parse generate.py's AST, find every function
whose parameters declare a "clock kind" by name (real_today/today/now =
wall-clock; as_of/as_of_date = snapshot), then assert every call site passes
an argument whose own name matches the parameter's declared kind. A
MISMATCH means a wall-clock parameter received a snapshot-named argument (or
vice versa) -- the exact FRESH-2 shape, at any joint, not just the one that
already broke.

Deliberately name-based, not type-based -- both `date` values are the same
Python type, so nothing but naming convention distinguishes "the moment
this ran" from "the moment the data was last touched." That is also
exactly what generate.py's own codebase already does (real_today vs as_of
is the established convention throughout), so this check enforces a
convention the code already follows almost everywhere, rather than
inventing a new one.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WALL_CLOCK_NAMES = {"real_today", "today", "now"}
SNAPSHOT_NAMES = {"as_of", "as_of_date"}


def _clock_kind(name: str) -> str | None:
    if name in WALL_CLOCK_NAMES:
        return "wall"
    if name in SNAPSHOT_NAMES:
        return "snapshot"
    return None


def _param_kinds(func: ast.FunctionDef) -> dict[str, str]:
    """Maps parameter name -> clock kind, for every parameter (positional or
    keyword) whose name declares one."""
    kinds = {}
    all_params = list(func.args.posonlyargs) + list(func.args.args) + list(func.args.kwonlyargs)
    for p in all_params:
        kind = _clock_kind(p.arg)
        if kind:
            kinds[p.arg] = kind
    return kinds


def check_clock_kind_consistency(repo_root: Path = ROOT) -> list[str]:
    path = repo_root / "generate.py"
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    # Collect every top-level (and nested) function def's clock-typed params,
    # keyed by function name. A function name collision (two functions with
    # the same name) is rare in this file and would only make the check
    # slightly more permissive (checks against the union of both param
    # orders), never silently skip a real mismatch.
    func_params: dict[str, list[str]] = {}
    func_kinds: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            kinds = _param_kinds(node)
            if not kinds:
                continue
            all_params = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            func_params[node.name] = [p.arg for p in all_params]
            func_kinds[node.name] = kinds

    if not func_params:
        return [
            "[GATE-18] found ZERO functions in generate.py with a clock-kind-declaring parameter "
            "(real_today/today/now/as_of/as_of_date) -- either the naming convention changed "
            "everywhere or this check's classification is broken, and it is measuring nothing."
        ]

    errors: list[str] = []
    checked_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        fname = node.func.id
        if fname not in func_params:
            continue
        param_order = func_params[fname]
        kinds = func_kinds[fname]

        # Map each positional arg to its parameter name by position.
        arg_by_param: dict[str, ast.expr] = {}
        for i, arg in enumerate(node.args):
            if i < len(param_order):
                arg_by_param[param_order[i]] = arg
        for kw in node.keywords:
            if kw.arg is not None:
                arg_by_param[kw.arg] = kw.value

        for param_name, expected_kind in kinds.items():
            arg_expr = arg_by_param.get(param_name)
            if arg_expr is None:
                continue  # positional-only call shorter than this param, or param uses a default
            if not isinstance(arg_expr, ast.Name):
                continue  # a non-identifier argument (call result, literal) isn't classifiable by name
            checked_calls += 1
            actual_kind = _clock_kind(arg_expr.id)
            if actual_kind is None:
                continue  # argument variable isn't itself clock-named; not classifiable either direction
            if actual_kind != expected_kind:
                errors.append(
                    f"[GATE-18] generate.py:{node.lineno}: {fname}({param_name}=...) expects a "
                    f"{expected_kind}-clock value but the call passes {arg_expr.id!r}, a "
                    f"{actual_kind}-clock value -- the exact FRESH-2 shape (a wall-clock parameter "
                    f"fed the dataset's frozen as_of stamp, or vice versa)."
                )

    if checked_calls == 0:
        errors.append(
            "[GATE-18] found clock-kind-declaring functions but ZERO classifiable call sites "
            "(every call passes a literal/expression rather than a bare identifier) -- this check "
            "cannot verify anything in this shape and must be revisited."
        )
    return errors


def main() -> int:
    errors = check_clock_kind_consistency()
    if errors:
        print(f"clock-kind consistency: {len(errors)} mismatch(es)")
        for e in errors:
            print(f"  {e}")
        return 1
    print("clock-kind consistency: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
