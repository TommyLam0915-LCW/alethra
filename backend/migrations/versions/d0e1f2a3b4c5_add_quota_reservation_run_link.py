"""link quota reservations to the research run that holds them

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-15

Additive-only: one nullable column, one foreign key, two partial indexes. Before
this revision the ledger could say "this user has a reserved research unit" but
not which run held it, so attribution was a timestamp guess and a sweep could
free a unit an executing run was about to settle (2026-11-15 review, Q1).
Ownership becomes a read.

Predecessor policy — strict, and it is the whole design of ``upgrade``: this
revision expects the column, the foreign key and both indexes to be ABSENT. It
checks every precondition before it creates anything, then creates. If any of
its own objects is already there — a partial apply, a hand-added column, a
``create_all`` schema — it refuses with the names of what it found, and changes
nothing. It does not repair (adding only what is missing would leave a foreign
key or a predicate nobody reviewed) and it does not drop and recreate (dropping
a column takes the attributions stored in it, which are the evidence this
revision exists to protect). A state this script did not produce is a state a
human has to look at, so refusing is the only answer that cannot be wrong.

Nothing is verified after the fact either, and that is deliberate. A check that
re-reads stored DDL can only compare text — the previous revision claimed to
match predicates "exactly" while it substring-matched them, which passes for a
wider or narrower predicate than the one written here. Structure is therefore
established by this script alone, and a database that already has some of it is
refused rather than graded.

Why this revision id is not ``e5f6a7b8c9d0``: that id is already taken by
``e5f6a7b8c9d0_add_intent_cancel_requested.py``, mid-chain behind
``c7e2f9a41b83`` and in front of ``f6a7b8c9d0e1``. Reusing it does not create a
branch — Alembic refuses to load with a ``DuplicateRevision`` error, so
``upgrade`` would not reach the database at all. The id below was checked free
against every ``revision`` line in this directory, and ``c9d0e1f2a3b4`` is the
one head of the chain.

Deliberately not done here:
* no backfill. Existing rows keep ``research_run_id`` NULL, which is an honest
  UNKNOWN — a date order over old rows is not evidence of attribution, and an
  unattributed unit is left for a human to decide rather than auto-released.
* ``ondelete=RESTRICT``. Deleting a run must not take its accounting evidence
  with it; the reservation row is the ledger, so it outlives the run.
* the unique index is partial and scoped to ``quota_type = 'research_run'``:
  that is the billing contract (at most one unit per run) and it leaves other
  quota types free to share a subject id.

``downgrade`` drops only what ``upgrade`` added, and refuses the same way if it
finds the objects in a shape it did not create.

NOT RUN: this script has not been executed against PostgreSQL. The predicates
below are shared with ``app/models/quota.py`` by name and pinned equal by
``tests/test_research_run_admission.py``, which is a text-level guarantee about
two files, not a database result.
"""
from typing import Dict, List, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'quota_reservations'
COLUMN = 'research_run_id'
FK_NAME = 'fk_quota_reservation_research_run'
INDEX_NAME = 'ix_quota_reservation_research_run'
UNIQUE_NAME = 'uq_quota_reservation_run_unit'
RUN_TABLE = 'research_runs'

# The two predicates, written exactly once. The model in
# ``app/models/quota.py`` declares the same strings in ``postgresql_where`` /
# ``sqlite_where``, and a test beside that file pins the equality, so a drift is
# reported by the suite rather than discovered by a production upgrade.
NOT_NULL_PREDICATE = 'research_run_id IS NOT NULL'
ONE_UNIT_PER_RUN_PREDICATE = "quota_type = 'research_run' AND research_run_id IS NOT NULL"


def _found(bind) -> Dict[str, List[str]]:
    """What this revision's own objects look like before it touches anything.

    Three keys, each a list of names actually present: the linkage column, the
    foreign keys guarding it, the indexes over it. ``upgrade`` and ``downgrade``
    both read this, so the sentence "the state is not one I created" is a
    measurement rather than an assumption.
    """
    tables = set(inspect(bind).get_table_names())
    if TABLE not in tables:
        return {'tables': [TABLE], 'column': [], 'foreign_keys': [], 'indexes': []}
    columns = {c['name'] for c in inspect(bind).get_columns(TABLE)}
    indexes = {i['name'] for i in inspect(bind).get_indexes(TABLE)}
    foreign_keys = [
        fk['name'] or '<unnamed>'
        for fk in inspect(bind).get_foreign_keys(TABLE)
        if COLUMN in fk.get('constrained_columns', [])
    ]
    return {
        'tables': [],
        'column': [COLUMN] if COLUMN in columns else [],
        'foreign_keys': foreign_keys,
        'indexes': sorted(
            name for name in (INDEX_NAME, UNIQUE_NAME) if name in indexes
        ),
    }


def _describe(found: Dict[str, List[str]]) -> str:
    parts = []
    if found['tables']:
        parts.append(f"missing table(s) {found['tables']}")
    if found['column']:
        parts.append(f"column {found['column'][0]}")
    if found['foreign_keys']:
        parts.append(f"foreign key(s) {found['foreign_keys']}")
    if found['indexes']:
        parts.append(f"index(es) {found['indexes']}")
    return ', '.join(parts) or 'nothing'


def upgrade() -> None:
    """Refuse any partial state, then add column, RESTRICT foreign key, indexes."""
    bind = op.get_bind()
    found = _found(bind)
    if found['tables']:
        raise RuntimeError(
            f"{_describe(found)}: {TABLE} and {RUN_TABLE} must both exist before "
            "this revision links a reservation to a run. Nothing was changed."
        )
    if RUN_TABLE not in set(inspect(bind).get_table_names()):
        raise RuntimeError(
            f"the {RUN_TABLE} table is missing, so the linkage would point at "
            "nothing. Nothing was changed."
        )
    if found['column'] or found['foreign_keys'] or found['indexes']:
        raise RuntimeError(
            f"{_describe(found)} already exist on {TABLE}. This revision adds "
            "these objects and expects none of them to be present; it refuses "
            "instead of repairing or recreating, because it cannot know what "
            "predicate, RESTRICT behaviour or attribution a state it did not "
            "create is enforcing. Nothing was changed."
        )

    op.add_column(
        TABLE, sa.Column(COLUMN, postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        FK_NAME, TABLE, RUN_TABLE, [COLUMN], ['id'], ondelete='RESTRICT'
    )
    op.execute(
        f"CREATE INDEX {INDEX_NAME} ON {TABLE} ({COLUMN}) "
        f"WHERE {NOT_NULL_PREDICATE}"
    )
    op.execute(
        f"CREATE UNIQUE INDEX {UNIQUE_NAME} ON {TABLE} ({COLUMN}) "
        f"WHERE {ONE_UNIT_PER_RUN_PREDICATE}"
    )


def downgrade() -> None:
    """Drop exactly what ``upgrade`` added, and refuse anything else it found."""
    bind = op.get_bind()
    found = _found(bind)
    unexpected = [
        name for name in found['indexes'] if name not in (INDEX_NAME, UNIQUE_NAME)
    ]
    if unexpected:
        raise RuntimeError(
            f"{TABLE} carries index(es) {unexpected} named for {COLUMN} that this "
            "revision did not create; refusing to drop the ones it did rather "
            "than leave an unknown index deciding the contract."
        )
    if len(found['foreign_keys']) > 1:
        raise RuntimeError(
            f"{TABLE}.{COLUMN} carries {len(found['foreign_keys'])} foreign keys "
            f"({found['foreign_keys']}); the linkage must have one owner, so this "
            "downgrade refuses to guess which one to drop."
        )
    for name in (UNIQUE_NAME, INDEX_NAME):
        if name in found['indexes']:
            op.execute(f'DROP INDEX {name}')
    if found['column']:
        if found['foreign_keys']:
            op.drop_constraint(found['foreign_keys'][0], TABLE, type_='foreignkey')
        op.drop_column(TABLE, COLUMN)
