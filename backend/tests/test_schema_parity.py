"""Model ↔ migration column-parity gate.

``conftest.py`` builds the SQLite lane from ``Base.metadata.create_all`` while
the PostgreSQL lane is built by running the migration chain, so a column that
lives on one side only fails at whichever test happens to touch it.
``paper_trading_runs.cancel_requested`` was added by migration
``c9d0e1f2a3b4`` and never declared in the model; the SQLite lane could not see
it and the failure surfaced as ``no such column``.  These tests compare the two
declarations directly, with no database, so that class is reported by the suite
instead of by a production upgrade.

Every collector defect found while writing this gate produced a *plausible*
answer rather than an error — see the individual tests, which each pin one of
them.  The counts asserted here are measured, not predicted.
"""

from pathlib import Path

from app.database import Base
import app.models  # noqa: F401  (registers every table on Base.metadata)

from tests.schema_parity import (
    collect_migration_columns,
    diff_against_models,
    identity_parse_failures,
    migration_scripts,
    model_column_sets,
    unresolved_migration_ops,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_DIR / "migrations" / "versions"


def test_every_migration_script_is_collected():
    """A script the collector cannot name must fail here, not vanish.

    15 of 54 scripts declare ``revision: str = '…'``, which is ``ast.AnnAssign``
    rather than ``ast.Assign``.  Reading only the latter dropped the base schema
    and still produced a full-looking table list.
    """
    on_disk = sorted(VERSIONS_DIR.glob("*.py"))
    collected = migration_scripts(VERSIONS_DIR)

    assert on_disk, f"no migration scripts found under {VERSIONS_DIR}"
    assert len(on_disk) > 1, "a single script cannot prove the chain is walked"
    assert len(collected) == len(on_disk), (
        f"{len(on_disk)} scripts on disk but {len(collected)} collected; "
        "some revision is unreadable or unreachable from the chain"
    )
    unparsable = identity_parse_failures(on_disk)
    assert not unparsable, [p.name for p in unparsable]


def test_collector_resolves_every_schema_operation():
    """No op is allowed to be dropped for want of a resolvable name.

    ``op.batch_alter_table('t') as batch`` puts the table on the context manager
    instead of the call, and ``_TABLE = 'agent_candidate_outcomes'`` puts it on a
    module constant.  Both were missed silently before being handled.
    """
    assert unresolved_migration_ops(VERSIONS_DIR) == []


def test_collector_sees_the_base_schema():
    """The initial revision's ``create_table`` columns really are collected."""
    columns = collect_migration_columns(VERSIONS_DIR)

    assert "users" in columns, sorted(columns)[:10]
    # users is created whole by 55a8d07a7f40; a near-empty set means the
    # create_table branch is not firing and every later diff is meaningless.
    assert len(columns["users"]) > 5, sorted(columns["users"])


def test_forward_state_ignores_downgrade():
    """``downgrade`` must not cancel the ``upgrade`` it mirrors.

    ``c9d0e1f2a3b4`` adds ``cancel_requested`` and drops it again in
    ``downgrade``.  Walking the whole module made the forward schema
    self-negating and every migrated column looked model-only.
    """
    columns = collect_migration_columns(VERSIONS_DIR)
    models = model_column_sets(Base.metadata)

    for table in ("paper_trading_runs", "order_intents"):
        assert "cancel_requested" in columns[table], (
            f"{table}: the chain's forward state lost a column its upgrade added, "
            "which means downgrade operations are being applied"
        )
        assert "cancel_requested" in models[table], (
            f"{table}: the column exists only in the migration, so the SQLite "
            "lane built from create_all cannot see it"
        )


def test_model_and_migration_columns_parity():
    """The two schema definitions agree, column for column.

    ``missing_in_model`` is what breaks the SQLite lane; ``extra_in_model`` is
    what breaks the PostgreSQL lane.  Both must be empty.
    """
    migration_columns = collect_migration_columns(VERSIONS_DIR)
    models = model_column_sets(Base.metadata)

    assert models, "Base.metadata is empty — app.models was not imported"
    assert len(migration_columns) >= len(models), (
        f"migrations build {len(migration_columns)} tables but models declare "
        f"{len(models)}; the chain cannot be the superset it should be"
    )

    drift = diff_against_models(migration_columns, models)
    assert drift == {}, {
        table: {key: sorted(value) for key, value in sides.items()}
        for table, sides in drift.items()
    }


def test_only_the_sacrificial_table_is_migration_only():
    """``_test_sacrificial_`` is the one table with no model, by design."""
    migration_columns = collect_migration_columns(VERSIONS_DIR)
    models = model_column_sets(Base.metadata)

    assert migration_columns, "collector returned nothing to compare"
    assert models, "Base.metadata is empty — app.models was not imported"
    assert sorted(t for t in migration_columns if t not in models) == ["_test_sacrificial_"]


def test_diff_detects_drift_in_both_directions(tmp_path):
    """Positive control: the gate can fail, on synthetic input with no DB.

    An empty drift result is only meaningful if a known drift is reported, so
    this writes a real script file — upgrade plus its downgrade — and injects
    one column into each side.
    """
    (tmp_path / "x1_injected.py").write_text(
        '''
"""injected drift fixture"""
revision = 'x1'
down_revision = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table('widgets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
    )
    op.add_column('widgets', sa.Column('added_later', sa.String(), nullable=True))


def downgrade():
    op.drop_column('widgets', 'added_later')
    op.drop_table('widgets')
''',
        encoding="utf-8",
    )

    columns = collect_migration_columns(tmp_path)
    # downgrade must not remove what upgrade created.
    assert columns["widgets"] == {"id", "name", "added_later"}, sorted(columns["widgets"])

    # Column in the chain but not in the model: the cancel_requested shape.
    drift = diff_against_models(columns, {"widgets": {"id", "name", "added_later"}})
    assert drift == {}, drift

    only_in_migration = diff_against_models(columns, {"widgets": {"id", "name"}})
    assert only_in_migration == {
        "widgets": {"missing_in_model": {"added_later"}, "extra_in_model": set()}
    }, only_in_migration

    only_in_model = diff_against_models({"widgets": {"id", "name"}}, columns)
    assert only_in_model == {
        "widgets": {"missing_in_model": set(), "extra_in_model": {"added_later"}}
    }, only_in_model
