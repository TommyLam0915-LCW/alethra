"""Model ↔ migration column-parity collection.

`conftest.py` builds the SQLite lane with ``Base.metadata.create_all`` while the
PostgreSQL lane is built by running the migrations.  A column that exists only
in a migration is therefore invisible to SQLite and only surfaces as
``no such column`` in whatever test happens to touch it — which is how
``paper_trading_runs.cancel_requested`` stayed broken.  These functions compare
the two declarations directly, with no database involved.

The revision order is recovered from each migration's own ``revision`` /
``down_revision`` literals with ``ast``; the scripts are never imported or
executed, so nothing can touch a database from here.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ADD_COLUMN = ("add_column",)
_DROP_COLUMN = ("drop_column", "drop_columns")
_CREATE_TABLE = ("create_table",)
_ALTER_COLUMN = ("alter_column",)


def _attr_name(func: ast.AST) -> str | None:
    """``op.add_column`` -> ``add_column``."""
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _str_arg(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_str(node: ast.AST | None, consts: dict[str, str]) -> str | None:
    """A string literal, or a module-level name bound to one.

    ``d0e1f2a3b4c5`` writes ``COLUMN = 'research_run_id'`` and passes the name,
    so treating a non-literal argument as "no column" hid a real column.
    """
    literal = _str_arg(node)
    if literal:
        return literal
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _str_sequence(node: ast.AST, consts: dict[str, str] | None = None) -> list[str]:
    """``'x'`` -> ``['x']``; ``('a', 'b')`` -> ``['a', 'b']``; ``None`` -> ``[]``."""
    consts = consts or {}
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for name in (_literal_str(elt, consts) for elt in node.elts) if name]
    single = _literal_str(node, consts)
    return [single] if single else []


def _assignments(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level ``NAME = value`` literals.

    Alembic writes the identity in both shapes — ``revision = 'x'`` and the
    annotated ``revision: str = 'x'``.  The second is an ``ast.AnnAssign``, so
    reading only ``ast.Assign`` silently drops those scripts; that dropped the
    entire base schema from the parity walk while still looking like a clean
    result.
    """
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found.setdefault(target.id, value)
    return found


def parse_script_identity(path: Path) -> tuple[str | None, list[str]]:
    """``(revision, down_revisions)`` for one migration script."""
    values = _assignments(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    revision = _str_arg(values["revision"]) if "revision" in values else None
    downs = _str_sequence(values["down_revision"]) if "down_revision" in values else []
    return revision, downs


def _column_names_in(args: list[ast.AST], consts: dict[str, str] | None = None) -> list[str]:
    """First argument of each ``sa.Column(...)`` in an argument list."""
    names = []
    for arg in args:
        if isinstance(arg, ast.Call) and _attr_name(arg.func) == "Column":
            if arg.args:
                name = _literal_str(arg.args[0], consts or {})
                if name:
                    names.append(name)
    return names


def _dropped_names(args: list[ast.AST], consts: dict[str, str] | None = None) -> list[str]:
    """Names from ``drop_column('t', 'c')`` and ``drop_columns('t', ['a', 'b'])``."""
    names: list[str] = []
    for arg in args:
        name = _literal_str(arg, consts or {})
        if name:
            names.append(name)
            continue
        names.extend(_str_sequence(arg, consts))
        names.extend(_column_names_in([arg], consts))
    return names


def _script_graph(paths: list[Path]) -> tuple[dict[str, list[str]], dict[str, Path]]:
    graph: dict[str, list[str]] = {}
    by_revision: dict[str, Path] = {}
    for path in paths:
        revision, downs = parse_script_identity(path)
        if revision is None:
            continue
        graph[revision] = downs
        by_revision[revision] = path
    return graph, by_revision


def _module_order(paths: list[Path]) -> list[Path]:
    """Apply order by down_revision; merge scripts visit each parent.

    ``a1b2c3d4e5f7`` declares a tuple of three parents, so a single ``down``
    would silently ignore two of them.  A parent naming no script in ``paths``
    is reported by :func:`unresolved_bases` rather than treated as a failure.
    """
    graph, by_revision = _script_graph(paths)

    ordered: list[Path] = []
    placed: set[str] = set()

    def visit(rev: str, seen: frozenset[str]) -> None:
        if rev in placed or rev in seen:
            return
        for down in graph[rev]:
            if down in graph:
                visit(down, seen | {rev})
        if rev not in placed:
            placed.add(rev)
            ordered.append(by_revision[rev])

    for rev in sorted(graph):
        visit(rev, frozenset())
    for rev in sorted(graph):
        if rev not in placed:
            placed.add(rev)
            ordered.append(by_revision[rev])
    return ordered


def unresolved_bases(paths: list[Path]) -> dict[str, list[str]]:
    """Revisions whose declared parents have no script in ``paths``."""
    graph, _ = _script_graph(paths)
    present = set(graph)
    return {
        rev: [down for down in downs if down not in present]
        for rev, downs in sorted(graph.items())
        if any(down not in present for down in downs)
    }


def identity_parse_failures(paths: list[Path]) -> list[Path]:
    """Scripts whose ``revision`` literal could not be read.

    Counting these against ``len(paths)`` is what makes the annotated-``revision``
    class of silent drop impossible: a migration the collector cannot name is a
    failure, not a missing table.
    """
    return [path for path in paths if parse_script_identity(path)[0] is None]


def migration_scripts(versions_dir: Path) -> list[Path]:
    """Every migration script in apply order — also the collector's count basis."""
    return _module_order(sorted(versions_dir.glob("*.py")))


def _upgrade_nodes(tree: ast.Module) -> list[ast.AST]:
    """Nodes that actually run on upgrade: ``upgrade()`` plus helpers it calls.

    Walking the whole module was wrong — the ``downgrade()`` below every
    ``op.add_column`` contains the matching ``op.drop_column``, so the forward
    schema cancelled itself and every migrated column looked model-only.
    Module-level helpers are followed because some scripts keep their ops there.
    """
    funcs = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    if "upgrade" not in funcs:
        return []

    collected: list[ast.AST] = []
    seen: set[str] = set()
    queue = [funcs["upgrade"]]
    while queue:
        func = queue.pop()
        if func.name in seen:
            continue
        seen.add(func.name)
        collected.append(func)
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                helper = funcs.get(node.func.id)
                if helper is not None and helper.name != func.name:
                    queue.append(helper)
    return collected


def _string_consts(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = 'literal'`` table aliases.

    ``e7f8a9b0c1d2`` and ``z0a1b2c3d4e5`` spell their tables as ``_CONSENTS =
    "agent_strategy_consents"`` / ``_TABLE = "agent_candidate_outcomes"`` and
    pass the name to ``op.create_table``/``op.add_column``, so an argument that
    is not a string literal is not necessarily an unresolvable table.
    """
    consts: dict[str, str] = {}
    for name, value in _assignments(tree).items():
        literal = _str_arg(value)
        if literal:
            consts[name] = literal
    return consts


def _receiver(call: ast.Call) -> str | None:
    """``batch_op.add_column(...)`` -> ``'batch_op'``; ``op.add_column(...)`` -> ``'op'``."""
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        return call.func.value.id
    return None


def _op_calls(func: ast.FunctionDef) -> list[ast.Call]:
    ops = set(_CREATE_TABLE) | set(_ADD_COLUMN) | set(_DROP_COLUMN) | set(_ALTER_COLUMN)
    return [node for node in ast.walk(func) if isinstance(node, ast.Call) and _attr_name(node.func) in ops]


def _batch_args(func: ast.FunctionDef) -> dict[str, ast.AST]:
    """``with op.batch_alter_table('t') as batch_op`` -> ``{'batch_op': 't' node}``.

    Batch ops carry no table argument — the table is the context manager's — so
    ``batch.add_column(sa.Column(...))`` has one argument where
    ``op.add_column('t', sa.Column(...))`` has two.  Attributing batch ops by
    their receiver is what keeps those columns from vanishing.
    """
    found: dict[str, ast.AST] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not (isinstance(call, ast.Call) and _attr_name(call.func) == "batch_alter_table"):
                continue
            if isinstance(item.optional_vars, ast.Name) and call.args:
                found.setdefault(item.optional_vars.id, call.args[0])
    return found


def _collect(versions_dir: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Forward-state tables, plus ops this collector could not attribute.

    Every missed capture in this file has shown up as a *plausible* result —
    the base schema absent, downgrade cancelling upgrade, batch and constant
    table names invisible — so the unresolved list is returned alongside the
    answer and the gate asserts it is empty.  A column this walk could not see
    is reported, never quietly counted as "not in the migrations".
    """
    tables: dict[str, set[str]] = {}
    unresolved: list[str] = []

    for path in migration_scripts(versions_dir):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        consts = _string_consts(tree)
        funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        if "upgrade" not in funcs:
            unresolved.append(f"{path.name}: no upgrade() function")
        for func in _upgrade_nodes(tree):
            batch = {name: _literal_str(node, consts) for name, node in _batch_args(func).items()}
            for call in _op_calls(func):
                fn = _attr_name(call.func)
                receiver = _receiver(call)
                if receiver is not None and receiver in batch:
                    table = batch[receiver]
                    args = call.args
                else:
                    table = _literal_str(call.args[0], consts) if call.args else None
                    args = call.args[1:]
                if table is None:
                    unresolved.append(f"{path.name}: {fn} with no resolvable table")
                    continue
                columns = tables.setdefault(table, set())
                if fn in _CREATE_TABLE or fn in _ADD_COLUMN:
                    found = _column_names_in(args, consts)
                    columns.update(found)
                    declared = sum(
                        1 for arg in args
                        if isinstance(arg, ast.Call) and _attr_name(arg.func) == "Column"
                    )
                    if declared > len(found):
                        unresolved.append(
                            f"{path.name}: {fn} on {table} has {declared - len(found)} "
                            "sa.Column argument(s) with no resolvable name"
                        )
                elif fn in _DROP_COLUMN:
                    for dropped in _dropped_names(args, consts):
                        columns.discard(dropped)
                elif fn in _ALTER_COLUMN:
                    old = _literal_str(args[0], consts) if args else None
                    rename = next((kw for kw in call.keywords if kw.arg == "new_column_name"), None)
                    if rename is None:
                        # nullable=/type= changes keep the column name; only a
                        # rename moves it, and an absent keyword is not a miss.
                        continue
                    new = _literal_str(rename.value, consts)
                    if old and new:
                        columns.discard(old)
                        columns.add(new)
                    else:
                        unresolved.append(
                            f"{path.name}: alter_column on {table} renames {old!r} "
                            f"to {new!r}; at least one name is unresolvable"
                        )

    return tables, unresolved


def collect_migration_columns(versions_dir: Path) -> dict[str, set[str]]:
    """table -> columns, as the migration chain leaves them."""
    return _collect(versions_dir)[0]


def unresolved_migration_ops(versions_dir: Path) -> list[str]:
    """Schema ops the collector could not name — the anti-silent-miss guard."""
    return _collect(versions_dir)[1]


def diff_against_models(
    migration_columns: dict[str, set[str]],
    model_tables: dict[str, set[str]],
) -> dict[str, dict[str, set[str]]]:
    """Per table: columns the chain created but no model declares, and vice versa."""
    report: dict[str, dict[str, set[str]]] = {}
    for table, columns in sorted(migration_columns.items()):
        if table not in model_tables:
            continue
        missing_in_model = columns - model_tables[table]
        extra_in_model = model_tables[table] - columns
        if missing_in_model or extra_in_model:
            report[table] = {
                "missing_in_model": missing_in_model,
                "extra_in_model": extra_in_model,
            }
    return report


def model_column_sets(metadata) -> dict[str, set[str]]:
    return {name: set(table.columns.keys()) for name, table in metadata.tables.items()}
