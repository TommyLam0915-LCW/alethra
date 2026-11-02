"""Inventory every ``emit_event`` call site and how its actor_type is decided.

Read-only: this script parses source and prints JSON. It never touches a
database, and it never rewrites an event.

Classification, in the order applied:

* ``verified_request_origin`` — the handler derives the value from the
  credential channel it verified (``request_actor_provenance(request)``), or
  receives that value from a caller that does.
* ``recorded_order_provenance`` — the call copies ``actor_type`` from a stored
  order whose value was set by the submit path, so the original channel is
  preserved rather than re-inferred.
* ``system`` — a background executor writes the row, so the stored actor is the
  system rather than the user who triggered the work.
* ``legacy_unknown`` — the call site states ``unknown`` explicitly, or omits the
  value and therefore falls back to the fail-closed default.
* ``needs_review`` — anything else. Such a site must be read by hand before its
  label is trusted.
"""

import argparse
import ast
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional


def _enclosing_function(tree: ast.AST, lineno: int) -> Optional[ast.AST]:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def _classify(path: pathlib.Path, fn: Optional[ast.AST], expr: Optional[str]) -> str:
    if expr is None or expr in ("'unknown'", '"unknown"'):
        return "legacy_unknown"
    if expr in ("'system'", '"system"'):
        return "system"
    if "request_actor_provenance(request)" in expr:
        return "verified_request_origin"
    if expr == "actor_type":
        return "verified_request_origin"
    if expr.endswith(".actor_type"):
        return "recorded_order_provenance"
    return "needs_review"


def inventory(root: pathlib.Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted((root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "emit_event"
            ):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            expr = (
                ast.unparse(keywords["actor_type"])
                if "actor_type" in keywords
                else None
            )
            fn = _enclosing_function(tree, node.lineno)
            rows.append(
                {
                    "file": str(path.relative_to(root)),
                    "line": node.lineno,
                    "function": getattr(fn, "name", None),
                    "actor_type_expression": expr,
                    "classification": _classify(path, fn, expr),
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(pathlib.Path(__file__).resolve().parents[1]),
        help="backend directory containing app/",
    )
    args = parser.parse_args()
    rows = inventory(pathlib.Path(args.root))
    json.dump(
        {"total": len(rows), "sites": rows},
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
