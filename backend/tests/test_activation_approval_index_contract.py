"""One contract, two builders: the live-approval index in the model and in Alembic.

``agent_model_activation_approvals`` has one constraint that decides what a
customer may do next: ``uq_agent_activation_approval_live_job`` — at most one
``decision='approved'`` row per training job, which is what makes a withdrawal
free the slot for a new approval instead of locking the job forever. That
constraint is written down twice, by two different files:

* ``app/models/agent_strategy.py:349-355`` (what ``create_all`` builds, and so
  what every SQLite test lane enforces);
* ``migrations/versions/a7c1e4f9b2d5_add_model_activation_approvals.py:62``
  (what ``alembic upgrade`` builds).

SQLAlchemy honours ``postgresql_where`` only on PostgreSQL and
``sqlite_where`` only on SQLite, so a migration that passes the former alone
silently builds a *plain* ``UNIQUE(job_id)`` there — a schema where a withdrawn
approval keeps occupying the job's slot. The suite stays green either way,
because the lane that exercises re-approval runs on ``create_all``. This file
compares the two sources per dialect instead of trusting either one.

Evidence scope, stated because these nodes cannot state it themselves:

* nothing here runs Alembic. The migration-side index is recovered by parsing
  the ``op.create_index(...)`` call out of the revision's own source and handing
  those arguments to the same DDL compiler the dialect uses; no revision is
  executed and no database is created on disk;
* whether the *whole* chain replays on SQLite is a separate question and is
  answered elsewhere in the negative: ``docs/development-handoff.md:64-71`` and
  ``scripts/seed_review_fixtures.py:13-16`` both say the history is not proven on
  SQLite and tell nobody to run ``upgrade head`` against a live one. The SQLite
  schema this project supports is ``create_all``. What is proven below is that
  this index is not one of the places the two builders disagree — not that the
  chain runs;
* the PostgreSQL half is compiled DDL, not a ``\\d`` readback from a server. No
  server was started and none is contacted here, so "what the dev database holds
  today" stays a named, unexecuted readback (§20.2).
"""

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import Column, Index, MetaData, Table, Text, create_engine, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from app.models.agent_paper_execution import AgentPaperExecution
from app.models.agent_strategy import AgentModelActivationApproval

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"
APPROVALS_FILE = "a7c1e4f9b2d5_add_model_activation_approvals.py"
SKILL_UNIT_FILE = "b2c3d4e5f6g7_add_skill_unit_associations.py"
PAPER_FILE = "e5f6g7h8i9j0_add_agent_paper_executions.py"

APPROVALS_TABLE = "agent_model_activation_approvals"
LIVE_INDEX = "uq_agent_activation_approval_live_job"
LIVE_PREDICATE = "decision = 'approved'"
PAPER_TABLE = "agent_paper_executions"

DIALECTS = {"postgresql": postgresql.dialect(), "sqlite": sqlite.dialect()}


def _source(name: str) -> str:
    return (_MIGRATIONS_DIR / name).read_text(encoding="utf-8")


def _literal(node):
    """An argument as the revision wrote it -- literals only, never a runtime name."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(e) for e in node.elts]
    if isinstance(node, ast.Call):
        fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if fname == "text" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            return text(node.args[0].value)
    raise AssertionError(
        f"{APPROVALS_FILE}: an op.create_index argument is not a literal "
        f"({ast.dump(node)}); this gate reads the shipped text, so a value "
        "computed at runtime has to be inlined or the gate named by hand"
    )


def _create_index_calls(source: str, *, in_upgrade: bool = True):
    """Every ``op.create_index(...)`` in a revision, as the arguments it passes."""
    tree = ast.parse(source)
    bounds = {
        n.name: (n.lineno, n.end_lineno)
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
    }
    upgrade = bounds.get("upgrade", (0, 0))
    calls = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_index"):
            continue
        if in_upgrade and not (upgrade[0] <= node.lineno < upgrade[1]):
            continue
        positional = [_literal(arg) for arg in node.args if not isinstance(arg, ast.Starred)]
        named = {k.arg: _literal(k.value) for k in node.keywords if k.arg}
        columns = named.pop("columns", None)
        if columns is None:
            columns = positional[2] if len(positional) > 2 else None
        calls.append({
            "name": positional[0] if positional else named.get("index_name"),
            "table": positional[1] if len(positional) > 1 else named.get("table_name"),
            "columns": columns,
            "kwargs": named,
            "lineno": node.lineno,
        })
    return calls


def _build(call, probe: Table) -> Index:
    """Rebuild the index the revision would have created, on a detached copy."""
    columns = call["columns"]
    assert columns and all(isinstance(c, str) for c in columns), (
        f"{call['name']}: the revision must name its columns literally, got "
        f"{columns!r} -- a list built at runtime is not something this gate can read"
    )
    return Index(call["name"], *(probe.c[c] for c in columns), **call["kwargs"])


def _ddl(index: Index, dialect: str) -> str:
    return str(CreateIndex(index).compile(dialect=DIALECTS[dialect])).strip()


def _model_indexes(table: Table) -> dict:
    return {i.name: [c.name for c in i.columns] for i in table.indexes}


def _approval_index(name: str) -> Index:
    return next(i for i in AgentModelActivationApproval.__table__.indexes if i.name == name)


def _migration_call(name: str):
    calls = [c for c in _create_index_calls(_source(APPROVALS_FILE))
             if c["name"] == name and c["table"] == APPROVALS_TABLE]
    assert len(calls) == 1, (
        f"{name} is created {len(calls)} times in {APPROVALS_FILE}'s upgrade(); "
        "this gate needs exactly one call to compare against the model"
    )
    return calls[0]


@pytest.fixture(scope="module")
def probe() -> Table:
    """A copy of the approvals table, so rebuilt indexes cannot attach to the model."""
    return AgentModelActivationApproval.__table__.to_metadata(MetaData())


# ── 一、created once, and never replaced ────────────────────────────────────


def test_the_live_approval_index_is_created_once_and_never_replaced():
    """Only the revision that adds the table builds it; no later one drops or rebuilds it."""
    created, dropped = [], []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        source = _source(path.name)
        if LIVE_INDEX not in source:
            continue
        created += [(path.name, c["lineno"])
                    for c in _create_index_calls(source)
                    if c["name"] == LIVE_INDEX and c["table"] == APPROVALS_TABLE]
        upgrade_body = source.split("def upgrade()", 1)[-1].split("def downgrade()", 1)[0]
        dropped += [(path.name, m.start()) for m in
                    re.finditer(r"drop_index\(\s*[\"']" + LIVE_INDEX, upgrade_body)]
    assert len(created) == 1, (
        f"{LIVE_INDEX} is created by {len(created)} revisions: {created}. Two "
        "builders means two predicates that can disagree, and this file would "
        "only compare against the one named at the top"
    )
    assert created[0][0] == APPROVALS_FILE, (
        f"{LIVE_INDEX} is built by {created[0][0]}, not the revision that adds "
        f"the table ({APPROVALS_FILE})"
    )
    assert dropped == [], (
        f"{LIVE_INDEX} is dropped inside an upgrade() at {dropped}: a revision "
        "that replaces it would make this comparison describe the past"
    )


# ── 二、the same index on PostgreSQL, from both sources ─────────────────────


def test_the_postgresql_build_matches_the_model_build(probe):
    """Same key, same predicate, same uniqueness -- or the shipped schema is a different contract."""
    model_ddl = _ddl(_approval_index(LIVE_INDEX), "postgresql")
    migration_ddl = _ddl(_build(_migration_call(LIVE_INDEX), probe), "postgresql")
    assert model_ddl == migration_ddl, (
        f"PostgreSQL: create_all builds {model_ddl!r} and alembic builds "
        f"{migration_ddl!r}; 'one live approval per job' would be enforced "
        "differently than it is tested"
    )
    assert model_ddl == (
        f"CREATE UNIQUE INDEX {LIVE_INDEX} ON {APPROVALS_TABLE} (job_id) "
        f"WHERE {LIVE_PREDICATE}"
    ), f"the index must stay partial on the one live row, got {model_ddl!r}"


# ── 三、the same index on SQLite, from both sources ─────────────────────────


def test_the_sqlite_build_matches_the_model_build(probe):
    """``sqlite_where`` is not optional: without it this dialect gets a plain UNIQUE(job_id).

    The kwarg is dialect-filtered by SQLAlchemy, so a revision that names only
    ``postgresql_where`` builds a non-partial index on SQLite -- a schema where a
    withdrawn approval keeps occupying the job's slot forever, while every lane
    built by ``create_all`` allows the re-approval.
    """
    model_index = _approval_index(LIVE_INDEX)
    call = _migration_call(LIVE_INDEX)
    assert set(call["kwargs"]) >= {"unique", "postgresql_where", "sqlite_where"}, (
        f"{APPROVALS_FILE}:{call['lineno']} passes {sorted(call['kwargs'])}; the "
        "model declares the predicate for both dialects "
        "(app/models/agent_strategy.py:349-355), so declaring it for one builder "
        "and not the other is exactly the drift this node exists to catch"
    )
    for dialect_kwarg in ("postgresql_where", "sqlite_where"):
        assert str(call["kwargs"][dialect_kwarg]) == str(model_index.dialect_kwargs[dialect_kwarg]), (
            f"{dialect_kwarg}: migration {str(call['kwargs'][dialect_kwarg])!r} "
            f"!= model {str(model_index.dialect_kwargs[dialect_kwarg])!r}"
        )
    assert _ddl(model_index, "sqlite") == _ddl(_build(call, probe), "sqlite"), (
        "the two builders emit different SQLite DDL for the constraint that "
        "decides whether a job can be approved again"
    )


# ── 四、what the compiled SQLite text actually enforces ─────────────────────


def test_the_sqlite_index_frees_the_slot_when_the_approval_is_withdrawn(probe):
    """The rendered text is executed on a two-column scratch table, so partiality is observed.

    The scratch table is not the shipped table: it carries only the two columns
    the index reads, because the thing under test is the *index* the revision
    produces, not the columns around it. Nothing is written to disk and no
    revision runs -- the statement executed here is the string this dialect's own
    compiler returns for the revision's own arguments.
    """
    ddl = _ddl(_build(_migration_call(LIVE_INDEX), probe), "sqlite")
    assert "WHERE" in ddl, (
        f"{ddl!r} carries no predicate; a non-partial UNIQUE(job_id) would make "
        "the re-approval below fail, and the reason would be lost"
    )
    engine = create_engine("sqlite://")
    scratch = MetaData()
    Table(APPROVALS_TABLE, scratch, Column("job_id", Text), Column("decision", Text))
    scratch.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql(ddl)

        def approve(job_id):
            return conn.exec_driver_sql(
                f"INSERT INTO {APPROVALS_TABLE} (job_id, decision) VALUES (?, 'approved')",
                (job_id,),
            )

        approve("job-1")
        with pytest.raises(IntegrityError):
            approve("job-1")
        conn.exec_driver_sql(
            f"UPDATE {APPROVALS_TABLE} SET decision='revoked' WHERE job_id='job-1'"
        )
        approve("job-1")
        conn.exec_driver_sql(
            f"INSERT INTO {APPROVALS_TABLE} (job_id, decision) VALUES ('job-1', 'rejected')"
        )
        rows = conn.exec_driver_sql(
            f"SELECT decision FROM {APPROVALS_TABLE} WHERE job_id='job-1' ORDER BY decision"
        ).scalars().all()
        assert rows == ["approved", "rejected", "revoked"], rows
    engine.dispose()


# ── 五、the named divergence this round records rather than renames ──────────


def test_the_skill_unit_index_diverges_by_name_only(probe):
    """The two sources agree on the columns and disagree on one index's name.

    ``ix_agent_activation_approval_skill_unit`` (model) and
    ``ix_agent_model_activation_approvals_skill_unit_id`` (``b2c3d4e5f6g7:121``)
    cover the same column. Renaming either side now would split databases already
    built the other way, so this node pins the pair as a *known* difference: it
    goes red if the names converge, if the columns drift apart, or if a third
    builder appears -- each of which changes what a readback must expect.
    """
    model_names = _model_indexes(AgentModelActivationApproval.__table__)
    built = {c["name"]: c["columns"]
             for f in (APPROVALS_FILE, SKILL_UNIT_FILE)
             for c in _create_index_calls(_source(f))
             if c["table"] == APPROVALS_TABLE}

    assert model_names["ix_agent_activation_approval_skill_unit"] == ["skill_unit_id"]
    assert built["ix_agent_model_activation_approvals_skill_unit_id"] == ["skill_unit_id"]
    assert "ix_agent_activation_approval_skill_unit" not in built, (
        "a migrated database now carries an index name the shipped history never "
        "created; the readback expects one of the two, not both"
    )
    assert "ix_agent_model_activation_approvals_skill_unit_id" not in model_names, (
        "the model declares an index name no revision builds"
    )
    assert set(built) - {"ix_agent_model_activation_approvals_skill_unit_id"} == (
        set(model_names) - {"ix_agent_activation_approval_skill_unit"}
    ), f"builders diverged beyond the pinned name pair: model={sorted(model_names)} built={sorted(built)}"


# ── 六、the paper ledger's indexes: paired by name, not position ────────────


def _paper_indexes_constant():
    """``INDEXES`` as the revision states it: pairs, so each name carries its columns."""
    tree = ast.parse(_source(PAPER_FILE))
    assign = next(
        (n for n in tree.body
         if isinstance(n, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "INDEXES" for t in n.targets)),
        None,
    )
    assert assign is not None, f"{PAPER_FILE} no longer assigns INDEXES"
    pairs = []
    for element in assign.value.elts:
        assert (isinstance(element, ast.Tuple) and len(element.elts) == 2
                and isinstance(element.elts[0], ast.Constant)), (
            f"{PAPER_FILE}: an INDEXES entry is not a (name, columns) pair: "
            f"{ast.dump(element)}"
        )
        pairs.append((element.elts[0].value, _literal(element.elts[1])))
    return pairs


def test_the_paper_execution_migration_names_each_index_with_its_columns():
    """A separate item from the approval index: the paper table paired index/column by position.

    ``zip(INDEXES, ([...], [...], [...]))`` keeps working when a tuple is
    reordered and silently builds ``ix_..._owner`` over ``job_id``. The regression
    is the pairing the revision states itself, checked against the model.
    """
    pairs = dict(_paper_indexes_constant())
    model_names = _model_indexes(AgentPaperExecution.__table__)

    assert pairs, f"{PAPER_FILE} names no indexes"
    assert set(pairs) <= set(model_names), (
        f"{PAPER_FILE} builds {sorted(set(pairs) - set(model_names))}, which the "
        "model does not declare -- a migrated database would carry an index no "
        "test lane asserts"
    )
    for name, columns in pairs.items():
        assert columns == model_names[name], (
            f"{name}: the revision indexes {columns}, the model indexes "
            f"{model_names[name]} -- positional pairing fails exactly this way, "
            "and the create_all lane cannot see it"
        )


def test_the_paper_execution_migration_has_no_positional_index_pairing():
    """The zip is gone and both loops unpack the pairs, so the shape cannot drift silently."""
    source = _source(PAPER_FILE)
    assert not re.search(r"\bzip\s*\(", source), (
        "a zip reappeared in this revision: paired tuples carry their own "
        "columns, a zip joins them by order only"
    )
    loops = [
        node.target for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
        and node.iter.id == "INDEXES"
    ]
    assert len(loops) == 2, (
        f"upgrade() and downgrade() are both expected to iterate INDEXES; found "
        f"{len(loops)} such loops -- one that iterates the pairs as a single "
        "name would match nothing in get_indexes() and silently skip the drop"
    )
    for target in loops:
        assert isinstance(target, ast.Tuple) and len(target.elts) == 2, (
            f"a loop over INDEXES unpacks {ast.dump(target)}, not (name, columns)"
        )
