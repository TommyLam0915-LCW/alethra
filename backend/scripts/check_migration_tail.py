#!/usr/bin/env python3
"""Name the alembic head this checkout upgrades to, and refuse if it is not the
revision the owner approved.

Two different claims get confused, and the e2e entry point used to prove only
one of them:

* *what does this tree upgrade to?* - alembic can answer that, and the answer
  comes from the same files that define the upgrade, so a check built only on it
  compares one source against itself;
* *is that the chain this deployment was approved against?* - no resolver over
  the same tree can answer that. It needs a stated expectation.

``EXPECTED_TAIL`` is that expectation, written as an ordinary literal here. It
is checked against an **independently parsed** DAG - ``revision`` and
``down_revision`` read out of the revision modules with ``ast``, with no alembic
import - and, with ``--with-alembic``, against alembic's own resolution as a
third source. Three agreeing sources is the point: a revision file that is
deleted, reordered, re-parented or added without the owner noticing changes at
least one of them, and the entry point that starts a stack then has a schema it
can name instead of one it inherited.

Exit codes, so a caller can tell the refusals apart:

* ``0`` - one head, it is ``EXPECTED_TAIL[-1]``, and the tail chain matches;
* ``2`` - the tree does not describe one upgrade path: branched (no single head),
  two modules claiming one revision id, or a ``down_revision`` no module defines;
* ``3`` - the DAG does not match the pin (moved head, broken link, unparseable
  or missing revision module), or alembic disagrees with the independent parse;
* ``4`` - alembic was asked for and could not be run at all.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

MIGRATIONS_SUBDIR = "migrations"
VERSIONS_SUBDIR = os.path.join("migrations", "versions")

# The last four revisions, oldest to newest. Re-pinned 2026-11-22: the
# agent-model paper-execution ledger
# (``e5f6g7h8i9j0_add_agent_paper_executions``) extends the chain that the human
# decision lineage column (``d4e5f6g7h8i9``) closed. Like that revision and the
# two skill-unit revisions in front of it, this one has not been executed against
# any PostgreSQL database — writing a migration is not running one, and no
# approved deployment has this schema yet. Extending the tree again means editing
# this line deliberately, in the same change that adds the migration, which is
# the review signal a derived value cannot provide.
EXPECTED_TAIL = (
    "b2c3d4e5f6g7",
    "c3d4e5f6g7h8",
    "d4e5f6g7h8i9",
    "e5f6g7h8i9j0",
)


def parse_tree(backend_dir: str) -> dict:
    """``{revision: (down_revision, ...)}`` read straight out of the modules.

    ``ast`` rather than import: importing a revision module executes it, and a
    module that fails to import would take the whole check down with it. A file
    the parser cannot read is reported by name by the caller, not skipped.
    """
    versions = os.path.join(backend_dir, VERSIONS_SUBDIR)
    tree = {}
    for name in sorted(os.listdir(versions)):
        if not name.endswith(".py") or name == "__init__.py":
            continue
        path = os.path.join(versions, name)
        with open(path, encoding="utf-8") as handle:
            module = ast.parse(handle.read(), filename=path)
        found = {}
        for node in module.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                    found[target.id] = ast.literal_eval(node.value)
        if "revision" not in found:
            raise ValueError(f"{path}: no module-level `revision` assignment")
        down = found.get("down_revision")
        tree[found["revision"]] = (
            () if down is None else (down if isinstance(down, (tuple, list)) else (down,))
        )
    return tree


def heads_of(tree: dict) -> list:
    """Revisions no other revision names as a parent - merge-aware."""
    parents = set()
    for downs in tree.values():
        parents.update(downs)
    return sorted(set(tree) - parents)


def revision_sources(backend_dir: str) -> dict:
    """``{revision: [module path, ...]}``, counted per file instead of per key.

    ``parse_tree`` is keyed by revision, so two modules that claim the same id
    collapse into one node and the tree still looks walkable from here. The
    second file has to stay visible, or an accidental re-use of a revision name
    reads as a smaller tree rather than as a broken one.
    """
    versions = os.path.join(backend_dir, VERSIONS_SUBDIR)
    sources = {}
    for name in sorted(os.listdir(versions)):
        if not name.endswith(".py") or name == "__init__.py":
            continue
        path = os.path.join(versions, name)
        with open(path, encoding="utf-8") as handle:
            module = ast.parse(handle.read(), filename=path)
        found = None
        for node in module.body:
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, ast.AnnAssign) else [])
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "revision":
                    found = ast.literal_eval(node.value)
        if found is None:
            raise ValueError(f"{path}: no module-level `revision` assignment")
        sources.setdefault(found, []).append(path)
    return sources


def missing_dependencies(tree: dict) -> dict:
    """``{revision: [parent, ...]}`` for parents this tree never defines."""
    known = set(tree)
    return {
        revision: [down for down in downs if down not in known]
        for revision, downs in tree.items()
        if any(down not in known for down in downs)
    }


def unreachable_from(tree: dict, heads: list) -> list:
    """Revisions no head can walk back to - the ancestry the upgrade cannot reach."""
    seen, stack = set(), list(heads)
    while stack:
        current = stack.pop()
        if current in seen or current not in tree:
            continue
        seen.add(current)
        stack.extend(tree[current])
    return sorted(set(tree) - seen)


def tail_chain(tree: dict, head: str, length: int) -> list:
    """The last ``length`` revisions walking back from ``head``, oldest first.

    A merge (several ``down_revision`` values) or a parent that is not in the
    tree stops the walk short, which the caller reports as a mismatch rather
    than as a pass: a tail that cannot be named to its end is not the chain the
    pin describes.
    """
    chain = [head]
    while len(chain) < length:
        downs = tree.get(chain[-1])
        if downs is None or len(downs) != 1 or downs[0] not in tree:
            break
        chain.append(downs[0])
    return list(reversed(chain))


def alembic_head(backend_dir: str) -> str:
    """The head alembic itself resolves, as a third and separate source."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, MIGRATIONS_SUBDIR))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        raise ValueError(f"alembic resolved {len(heads)} heads: {sorted(heads)}")
    return heads[0]


def check(backend_dir: str, *, with_alembic: bool) -> dict:
    report = {"backend_dir": backend_dir, "expected_tail": list(EXPECTED_TAIL)}
    try:
        tree = parse_tree(backend_dir)
    except (OSError, SyntaxError, ValueError) as exc:
        return {"status": "UNPARSEABLE", "detail": str(exc), **report}

    report["revision_count"] = len(tree)
    sources = revision_sources(backend_dir)
    report["module_count"] = sum(len(paths) for paths in sources.values())
    duplicated = {r: p for r, p in sources.items() if len(p) > 1}
    if duplicated:
        return {"status": "DUPLICATE_REVISION", "detail": (
            "; ".join(f"{r} claimed by {', '.join(p)}" for r, p in sorted(duplicated.items()))
        ), **report}

    heads = heads_of(tree)
    report["parsed_heads"] = heads
    if len(heads) != 1:
        return {"status": "BRANCHED_TREE", "detail": (
            f"{len(heads)} heads ({', '.join(heads)}) - a branched tree has no "
            "single 'head' to upgrade to, so the schema is undefined"
        ), **report}

    head = heads[0]
    report["parsed_head"] = head
    ghosts = missing_dependencies(tree)
    if ghosts:
        return {"status": "MISSING_DEPENDENCY", "detail": (
            "; ".join(f"{r} names down_revision {', '.join(d)} which no module defines"
                      for r, d in sorted(ghosts.items()))
        ), **report}
    report["unreachable_from_head"] = unreachable_from(tree, heads)

    report["parsed_tail"] = tail_chain(tree, head, len(EXPECTED_TAIL))
    if report["parsed_tail"] != list(EXPECTED_TAIL):
        return {"status": "TAIL_MISMATCH", "detail": (
            f"the tree ends at {report['parsed_tail']}, the approved tail is "
            f"{list(EXPECTED_TAIL)}"
        ), **report}

    if with_alembic:
        try:
            resolver_head = alembic_head(backend_dir)
        except ImportError as exc:
            return {"status": "NO_ALEMBIC", "detail": str(exc), **report}
        except ValueError as exc:
            return {"status": "BRANCHED_TREE", "detail": str(exc), **report}
        report["alembic_head"] = resolver_head
        if resolver_head != head:
            return {"status": "SOURCES_DISAGREE", "detail": (
                f"the independent parse says {head} and alembic says {resolver_head}"
            ), **report}

    return {"status": "OK", "head": head, **report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--backend-dir",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--with-alembic",
        action="store_true",
        help="also require alembic's own resolution to agree with the parse",
    )
    args = parser.parse_args(argv)

    report = check(args.backend_dir, with_alembic=args.with_alembic)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["status"] == "OK":
        print(report["head"])
    else:
        print(f"migration tree check {report['status']}: {report.get('detail')}",
              file=sys.stderr)

    return {
        "OK": 0,
        "BRANCHED_TREE": 2,
        "DUPLICATE_REVISION": 2,
        "MISSING_DEPENDENCY": 2,
        "NO_ALEMBIC": 4,
    }.get(report["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
