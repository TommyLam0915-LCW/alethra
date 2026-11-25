"""store the request de-duplication key beside a research run

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-16

``POST /api/v1/versions/{id}/runs`` bills one research unit per request, so a
caller whose response was lost could not retry without paying twice — and could
not tell a retry from a deliberate re-run either, because nothing recorded what
the first request was. This adds the two columns that make the difference
decidable: the client's key, and a hash of the request that carried it. The
service reads them inside the version lock, so the second request is answered
with the first attempt's row instead of a second unit (see
``app/services/research_runs.py::_admit_research_run``).

Scoping is what makes this safe to run on a live table. The unique index is over
``(experiment_version_id, idempotency_key)``, not over the key alone: a version
belongs to exactly one owner (``research_runs`` has no ``owner_id``, ownership is
reached through ``experiment_versions``), so a table-wide key would let one
account's key collide with — or, worse, resolve — another account's run. It is
also partial on ``idempotency_key IS NOT NULL``, so rows written without a key
stay what they always were, one per deliberate run, and keep being insertable in
duplicate.

Additive-only, and its policy is the one ``d0e1f2a3b4c5`` sets: both columns and
the index are expected to be ABSENT. Every precondition is checked before
anything is created, and any partial state — a hand-added column, a partial
apply, a ``create_all`` schema — is refused by name with nothing changed. It does
not repair (adding only what is missing would leave an index whose predicate
nobody reviewed deciding who may retry) and it does not drop and recreate
(dropping the column takes the keys that are currently protecting someone's
quota). Nothing is verified after the fact either: a check that re-reads stored
DDL can only compare text, so structure is established by this script alone.

Deliberately not done here:
* no backfill. Existing rows keep both columns NULL, which is an honest
  UNKNOWN — no past request can be given a key after the fact, and a NULL row is
  correctly invisible to de-duplication.
* no uniqueness on ``idempotency_request_hash``. It records what the request
  was; two different keys may legitimately describe the same request.
* no index on ``idempotency_key`` alone. Every lookup is by (version, key),
  which is the leftmost pair of the index above.

The predicate is written once here and once in ``app/models/research_run.py``,
and a test in ``tests/test_research_run_admission.py`` pins the two equal by
name, so drift is reported by the suite rather than by a production upgrade.

NOT RUN: this script has not been executed against PostgreSQL. The lane that
built these objects is ``Base.metadata.create_all``, which reads the model, not
this file; the contract test below compares the two as text, which is a
guarantee about two files and not a database result.
"""
from typing import Dict, List, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'research_runs'
KEY_COLUMN = 'idempotency_key'
HASH_COLUMN = 'idempotency_request_hash'
UNIQUE_NAME = 'uq_research_run_version_idempotency'
KEY_LENGTH = 128
HASH_LENGTH = 64

# Declared exactly once, and by the model too. ``IS NOT NULL`` is what keeps a
# keyless row — every run before this revision, and every deliberate re-run
# after it — out of the unique set.
KEYED_ROW_PREDICATE = 'idempotency_key IS NOT NULL'

OWN_OBJECTS = (KEY_COLUMN, HASH_COLUMN, UNIQUE_NAME)


def _found(bind) -> Dict[str, List[str]]:
    """What this revision's own objects look like before it touches anything.

    The table, then the two columns and the index named above. ``upgrade`` and
    ``downgrade`` both read this, so "the state is not one I created" is a
    measurement rather than an assumption.
    """
    tables = set(inspect(bind).get_table_names())
    if TABLE not in tables:
        return {'tables': [TABLE], 'column': [], 'indexes': []}
    columns = {c['name'] for c in inspect(bind).get_columns(TABLE)}
    indexes = {i['name'] for i in inspect(bind).get_indexes(TABLE)}
    return {
        'tables': [],
        'column': [name for name in (KEY_COLUMN, HASH_COLUMN) if name in columns],
        'indexes': sorted(name for name in (UNIQUE_NAME,) if name in indexes),
    }


def _describe(found: Dict[str, List[str]]) -> str:
    parts = []
    if found['tables']:
        parts.append(f"missing table(s) {found['tables']}")
    if found['column']:
        parts.append(f"column(s) {found['column']}")
    if found['indexes']:
        parts.append(f"index(es) {found['indexes']}")
    return ', '.join(parts) or 'nothing'


def upgrade() -> None:
    """Refuse any partial state, then add the two columns and the scoped index."""
    bind = op.get_bind()
    found = _found(bind)
    if found['tables']:
        raise RuntimeError(
            f"{_describe(found)}: {TABLE} must exist before de-duplication keys "
            "can be stored on its runs. Nothing was changed."
        )
    if found['column'] or found['indexes']:
        raise RuntimeError(
            f"{_describe(found)} already exist on {TABLE}. This revision adds "
            f"{list(OWN_OBJECTS)} and expects none of them to be present; it "
            "refuses instead of repairing or recreating, because it cannot know "
            "what scope, predicate or key space a state it did not create is "
            "enforcing — and those three are the whole contract of a retry. "
            "Nothing was changed."
        )

    op.add_column(
        TABLE, sa.Column(KEY_COLUMN, sa.String(KEY_LENGTH), nullable=True)
    )
    op.add_column(
        TABLE, sa.Column(HASH_COLUMN, sa.String(HASH_LENGTH), nullable=True)
    )
    op.execute(
        f"CREATE UNIQUE INDEX {UNIQUE_NAME} ON {TABLE} "
        f"(experiment_version_id, {KEY_COLUMN}) WHERE {KEYED_ROW_PREDICATE}"
    )


def downgrade() -> None:
    """Drop exactly what ``upgrade`` added, and refuse anything else it found."""
    found = _found(op.get_bind())
    if found['tables']:
        return
    unexpected = [name for name in found['indexes'] if name != UNIQUE_NAME]
    if unexpected:
        raise RuntimeError(
            f"{TABLE} carries index(es) {unexpected} that this revision named "
            "for its own objects but did not create; refusing to drop the ones it "
            "did rather than leave an unknown index deciding the contract."
        )
    if found['indexes']:
        op.execute(f'DROP INDEX {UNIQUE_NAME}')
    for name in (HASH_COLUMN, KEY_COLUMN):
        if name in found['column']:
            op.drop_column(TABLE, name)
