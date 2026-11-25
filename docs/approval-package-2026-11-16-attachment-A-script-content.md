# 附件 A — 執行包腳本與三支 migration 的內容（規則式遮罩後嵌入）

> 本檔由 `python3 /tmp/render_attachment_A.py` 產生，嵌入目前工作樹的檔案內容，
> 逐字但**經規則式機密遮罩**（見文末「機密處理」）；行數、位元組與 SHA256 都量自未遮罩的原檔。
> 產生時的樹身分：
> `branch=live1-impl` / `HEAD=5a34e3fed0f2d43a4e87dfeb714acd5c9f79197a` / 髒檔案 138 個（`git status --porcelain=v1 -uall` 的 SHA256 = `16e707f15d16bda2c7d565c152eac94b9593e058d75daa9a41199ee8ed17ef1d`）。
> 任何被嵌入檔案修改之後，本檔即為過期快照 — 重新執行產生器，不要手改本檔。
> 位元組數、行數、SHA256 皆由產生器當場量得，不從別處抄錄。

## 索引

| # | 角色 | 路徑 | 行 | 位元組 | SHA256（完整，未遮罩檔） | git | 遮罩 |
|---|---|---|---|---|---|---|---|
| 1 | migration | `backend/migrations/versions/d0e1f2a3b4c5_add_quota_reservation_run_link.py` | 191 | 8509 | `b6270a4a3acf5aef232b6b95413b86e72ccc4c05e3399ccde6b1f08b6b7d4c85` | tracked | - |
| 2 | migration | `backend/migrations/versions/e1f2a3b4c5d6_add_runstatus_cancelled.py` | 95 | 3638 | `177932bd417120820415259646e2be9427fb748f2d34b64d86e4cd6afb65c573` | **untracked** | - |
| 3 | migration | `backend/migrations/versions/f2a3b4c5d6e7_add_research_run_idempotency.py` | 158 | 6998 | `981ab19fe48be1a2be51b875ed0640d01ebd98644aeb8c8c8c8676c64eb8bf4a` | **untracked** | - |
| 4 | lane | `backend/scripts/run_tests.sh` | 494 | 21313 | `eb2fdefd5a38a4ff0d7593831c2fc776cd5ed9acf6e082c93c44a118ba39d3b2` | tracked | - |
| 5 | gate | `backend/scripts/skip_budget.py` | 263 | 11061 | `d1da8e23305ad8f395e5b284b38d91c8f370b0870a5a63ae969630e531775d7c` | **untracked** | - |
| 6 | gate | `backend/scripts/run_evidence.py` | 588 | 26566 | `83570fb1f738e6a9ea139d60170db9dbd063e867203cb15b78fbaf36602b10bf` | **untracked** | - |
| 7 | gate | `backend/scripts/check_env_hygiene.py` | 620 | 30324 | `bd07aef661c865e7934e8c3e7cda58dbc2718173f523193f20f6b6df16b43820` | **untracked** | 3 |
| 8 | gate | `backend/scripts/check_migration_tail.py` | 201 | 7726 | `387bc4c6606d8925acb55b1932755f363af2c9edaf70220c38a7b16488cfdc9d` | **untracked** | - |
| 9 | stack | `run_r2.sh` | 742 | 33391 | `6ee8bfbe163ece518a08979121023e723cd463285049377324a46b094480be16` | **untracked** | 4 |

SHA256 是**磁碟上的原檔**；嵌入內容是同一檔案經遮罩後的版本，兩者相差的正是「遮罩」欄
所列的位置（見各節標頭）。位元組與行數都量自原檔，所以被遮罩的段落會比原檔短。

---

## 1. `backend/migrations/versions/d0e1f2a3b4c5_add_quota_reservation_run_link.py`

- 角色：migration；行 191；位元組 8509；SHA256（原檔）`b6270a4a3acf5aef232b6b95413b86e72ccc4c05e3399ccde6b1f08b6b7d4c85`；git 已追蹤；遮罩 0 處。

```
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
```

---

## 2. `backend/migrations/versions/e1f2a3b4c5d6_add_runstatus_cancelled.py`

- 角色：migration；行 95；位元組 3638；SHA256（原檔）`177932bd417120820415259646e2be9427fb748f2d34b64d86e4cd6afb65c573`；git **未追蹤**；遮罩 0 處。

```
"""runstatus gains 'cancelled': a research run can be closed by decision

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-16

Why a new state instead of a timeout: ``research_runs`` admits durably, so an
interrupted attempt leaves a committed non-terminal row that holds its version
(409) and its quota unit. Recovery refuses to close it on age, because a slow
backtest and a killed one are the same row. ``POST /api/v1/runs/{id}/cancel``
lets the account that started the work record an outcome it actually knows —
that it gave up — and hands the unit back. That outcome needs a value of its
own: writing it as ``failed`` would tell the reader a computation broke.

The value is added, never reinterpreted. Rows already in ``pending``,
``running``, ``completed`` or ``failed`` keep their meaning, and the two
predicates that decide who may publish an outcome (``status IN
('pending','running')``) are unchanged, so a cancelled row stays closed to a
late writer without any backfill here.

PostgreSQL only: on SQLite the column is VARCHAR, so the same statement would
have nothing to alter. A value cannot be removed from a PostgreSQL enum, so
``downgrade`` leaves it in place — the code that reads it goes back, the label
stays, and the row values that use it are the only thing that can disappear with
the rows themselves.
"""
from typing import List, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUM_TYPE = "runstatus"
NEW_VALUE = "cancelled"


def _labels(bind) -> Union[List[str], None]:
    """The enum's values in declaration order, or None if the type is absent."""
    if bind.dialect.name != "postgresql":
        return None
    found = bind.execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname = :name ORDER BY e.enumsortorder"
        ),
        {"name": ENUM_TYPE},
    ).fetchall()
    return None if not found else [row[0] for row in found]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    labels = _labels(bind)
    if labels is None:
        raise RuntimeError(
            f"the {ENUM_TYPE} enum type does not exist, so this revision has no "
            "state to add a value to. Nothing was changed."
        )
    if NEW_VALUE in labels:
        raise RuntimeError(
            f"{ENUM_TYPE} already carries '{NEW_VALUE}'. This revision adds that "
            "value and expects it to be absent; it refuses instead of assuming "
            "that whatever put it there means the same thing this lane means by "
            "it. Nothing was changed."
        )

    # ALTER TYPE ... ADD VALUE takes effect for other sessions only once this
    # transaction commits, which is exactly when the code that writes the value
    # starts running.
    bind.execute(
        sa.text(f"ALTER TYPE {ENUM_TYPE} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    labels = _labels(bind) or []
    print(
        f"runstatus keeps {labels} on downgrade: PostgreSQL cannot remove a "
        "value from an enum type, and no stored row is rewritten by this "
        "revision. The application that reads 'cancelled' goes back with the "
        "code, not with the type."
    )
```

---

## 3. `backend/migrations/versions/f2a3b4c5d6e7_add_research_run_idempotency.py`

- 角色：migration；行 158；位元組 6998；SHA256（原檔）`981ab19fe48be1a2be51b875ed0640d01ebd98644aeb8c8c8c8676c64eb8bf4a`；git **未追蹤**；遮罩 0 處。

```
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
```

---

## 4. `backend/scripts/run_tests.sh`

- 角色：lane；行 494；位元組 21313；SHA256（原檔）`eb2fdefd5a38a4ff0d7593831c2fc776cd5ed9acf6e082c93c44a118ba39d3b2`；git 已追蹤；遮罩 0 處。

```
#!/bin/bash
# Backend suite entry point. Two layers, never merged:
#
#   ./backend/scripts/run_tests.sh sqlite [pytest args…]
#       Default suite on a run-specific SQLite file. The PostgreSQL-only
#       variables are cleared on purpose, so PostgreSQL modules skip for the
#       stated reason (printed by -rs) instead of tripping over a half-set env.
#
#   ./backend/scripts/run_tests.sh pg [pytest args…]
#       PostgreSQL-required suites on a disposable database. Requires
#       ALETHRA_PG_TEST_URL; all four DB variables are derived from it, and a
#       read-only preflight proves they agree — and that the database answers —
#       before pytest starts.
#
# A sqlite result is not a PostgreSQL result and vice versa. Each run prints the
# layer it executed, so a count can never be read across layers.
#
# The sqlite layer also enforces two ceilings. Every run writes its own
# directory (`alethra-runs/<layer>-<utc>-<pid>`) holding identity.txt,
# collect.txt (the node ids this tree collects), pytest.log, junit.xml (the
# outcome records, which are the only artifact that distinguishes an
# item-level skip from a collection-level one) and exit-code.txt -- the tested
# process's own status, never the logger's. Against those artifacts,
# `scripts/skip_budget.py` censuses the printed skip reasons by category and
# `scripts/run_evidence.py` reconciles records against collected ids and
# refuses a selection that no longer matches `tests/collected_nodes.txt`.
# Over budget, vanished nodes, or an unacknowledged selection diff, the lane
# exits non-zero even though every selected test passed.
#
# Environment:
#   ALETHRA_PYTHON                   interpreter override; must be executable.
#                                    Unset, this script uses this checkout's own
#                                    venv (backend/.venv, then backend/venv) and
#                                    refuses if neither exists. It never falls
#                                    back to a system python3: a host interpreter
#                                    that happens to carry pytest/sqlalchemy is a
#                                    second dependency set, not this lane.
#   ALETHRA_PG_TEST_URL              required for `pg`; never printed
#   ALETHRA_TEST_REQUIRED_DB_PREFIX  default alethra_ (disposable boundary)
#   ALETHRA_SQLITE_DB                optional database file for `sqlite`
#   TMPDIR                           where that file is created when
#                                    ALETHRA_SQLITE_DB is unset (default /tmp).
#                                    The directory must exist and be writable:
#                                    the run is refused here rather than failing
#                                    in every test fixture below.
#   ALETHRA_RUN_DIR                  the artifact directory for this run. It is
#                                    created exclusively: an existing directory
#                                    is refused rather than reused or renamed,
#                                    because a shared path is how one run's log
#                                    ended up inside another run's file.
#   ALETHRA_LANE_LOG                 optional copy destination for pytest.log,
#                                    written by copy after the run, never by the
#                                    tee that the tested process writes through.
#   JWT_SECRET_KEY                   generated per run when unset
#
# Exit: pytest's status, 2 refused precondition, 3 preflight rejected the env.
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BACKEND="$ROOT/backend"

usage() {
  awk 'NR == 1 { next } /^set -u/ { exit } { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
  "") MODE="sqlite" ;;
  sqlite | pg)
    MODE=$1
    shift
    ;;
  *)
    echo "unknown layer: $1 (expected sqlite or pg)" >&2
    usage >&2
    exit 2
    ;;
esac

if [ -n "${ALETHRA_PYTHON:-}" ]; then
  PYTHON=$ALETHRA_PYTHON
  [ -f "$PYTHON" ] && [ -x "$PYTHON" ] || {
    echo "refusing: ALETHRA_PYTHON='$PYTHON' is not an executable file" >&2
    exit 2
  }
else
  PYTHON=""
  for candidate in "$BACKEND/.venv/bin/python" "$BACKEND/venv/bin/python"; do
    if [ -f "$candidate" ] && [ -x "$candidate" ]; then
      PYTHON=$candidate
      break
    fi
  done
  [ -n "$PYTHON" ] || {
    echo "refusing: this checkout has no venv interpreter. Probed:" >&2
    echo "  $BACKEND/.venv/bin/python" >&2
    echo "  $BACKEND/venv/bin/python" >&2
    echo "Set ALETHRA_PYTHON to the interpreter this lane was built with." >&2
    echo "A system python3 is not used silently: it carries its own pytest and" >&2
    echo "SQLAlchemy, so its counts would be read as this lane's." >&2
    exit 2
  }
fi

"$PYTHON" -c 'import pytest, sqlalchemy' 2>/dev/null ||
  { echo "refusing: $PYTHON cannot import pytest/sqlalchemy; set ALETHRA_PYTHON" >&2; exit 2; }

# The counts below are only comparable to another run if the dependency set was
# the same one, so the versions are printed instead of inferred from the path.
print_lane_fingerprint() {
  "$PYTHON" -c 'import platform, pytest, sqlalchemy; print("lane fingerprint: python %s | pytest %s | sqlalchemy %s" % (platform.python_version(), pytest.__version__, sqlalchemy.__version__))'
}

if [ -z "${JWT_SECRET_KEY:-}" ]; then
  JWT_SECRET_KEY=$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')
  export JWT_SECRET_KEY
fi

export PYTHONPATH="$BACKEND${PYTHONPATH:+:$PYTHONPATH}"

preflight() {
  "$PYTHON" -c '
import os
import sys

from scripts.check_db_env import analyse

prefix = os.environ.get("ALETHRA_TEST_REQUIRED_DB_PREFIX") or None
analysis = analyse(os.environ, required_prefix=prefix)
for problem in analysis.errors:
    print("preflight: " + problem, file=sys.stderr)
if analysis.errors:
    sys.exit(1)
print("preflight ok: " + analysis.summary)
'
}

if [ "$MODE" = "pg" ]; then
  if [ -z "${ALETHRA_PG_TEST_URL:-}" ]; then
    echo "refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)" >&2
    exit 2
  fi
  export DATABASE_URL="$ALETHRA_PG_TEST_URL"
  export ALETHRA_TEST_DB="$ALETHRA_PG_TEST_URL"
  export ALETHRA_PG_DATABASE_URL="$ALETHRA_PG_TEST_URL"
  export ALETHRA_TEST_POSTGRES_URL="$ALETHRA_PG_TEST_URL"
  export ALETHRA_TEST_REQUIRED_DB_PREFIX=${ALETHRA_TEST_REQUIRED_DB_PREFIX:-alethra_}
  export TEST_MODE=true
  echo "backend suite — layer: pg (PostgreSQL; pass the PostgreSQL-required test paths)"
  echo "interpreter: $PYTHON"
  print_lane_fingerprint
  preflight || {
    echo "preflight rejected the environment; pytest was not started." >&2
    exit 3
  }
  # Integrate safety gate before any DB writes
  "$PYTHON" -c '
import sys
from test_safety_gate import verify_test_database, TestDatabaseGateError

try:
    gate_info = verify_test_database()
    print("Safety gate passed:", gate_info["database"], "on", str(gate_info["server_addr"]) + ":" + str(gate_info["server_port"]))
except TestDatabaseGateError as e:
    print("SAFETY GATE FAILED:", e, file=sys.stderr)
    sys.exit(1)
' || {
    echo "safety gate rejected the database connection; pytest was not started." >&2
    exit 3
  }
  "$PYTHON" -c '
import os
import sys

from scripts.check_db_env import analyse, connection_problem

analysis = analyse(os.environ, required_prefix=os.environ.get("ALETHRA_TEST_REQUIRED_DB_PREFIX"))
if analysis.errors:
    sys.exit(1)
problem = connection_problem(os.environ["DATABASE_URL"], analysis.database)
if problem:
    print("preflight: " + problem, file=sys.stderr)
    sys.exit(1)
print("preflight ok: database answered as " + analysis.database)
' || {
    echo "preflight could not reach the database; pytest was not started." >&2
    exit 3
  }
else
  DB_FILE=${ALETHRA_SQLITE_DB:-${TMPDIR:-/tmp}/alethra-sqlite-test-$$.db}
  case "$DB_FILE" in
    /*) ;;
    *)
      echo "refusing: ALETHRA_SQLITE_DB must be an absolute path, got '$DB_FILE'" >&2
      exit 2
      ;;
  esac
  DB_DIR=${DB_FILE%/*}
  [ -n "$DB_DIR" ] || DB_DIR=/
  if [ -n "${ALETHRA_SQLITE_DB:-}" ]; then
    DB_SOURCE="ALETHRA_SQLITE_DB=$ALETHRA_SQLITE_DB"
  elif [ -n "${TMPDIR:-}" ]; then
    DB_SOURCE="TMPDIR=$TMPDIR"
  else
    DB_SOURCE="the default /tmp"
  fi
  # An unusable directory has to be said here: sqlite opens no file silently and
  # every test then fails at setup with "unable to open database file", which
  # reads as a product regression rather than as a missing scratch directory.
  [ -d "$DB_DIR" ] || {
    echo "refusing: the directory for the sqlite test database does not exist: $DB_DIR" >&2
    echo "that path came from $DB_SOURCE" >&2
    echo "create it, or point TMPDIR (or ALETHRA_SQLITE_DB) at a directory that exists" >&2
    exit 2
  }
  [ -w "$DB_DIR" ] || {
    echo "refusing: the sqlite test database directory $DB_DIR ($DB_SOURCE) is not writable" >&2
    exit 2
  }
  unset ALETHRA_PG_DATABASE_URL ALETHRA_TEST_POSTGRES_URL ALETHRA_TEST_REQUIRED_DB_PREFIX
  export DATABASE_URL="sqlite:///$DB_FILE"
  export ALETHRA_TEST_DB="$DATABASE_URL"
  echo "backend suite — layer: sqlite (default suite)"
  echo "interpreter: $PYTHON"
  print_lane_fingerprint
  preflight || {
    echo "preflight rejected the environment; pytest was not started." >&2
    exit 3
  }
  echo "note: PostgreSQL-only modules will skip here; the lane prints each reason."
  echo "      this run is not a PostgreSQL result."
fi

cd "$BACKEND" || exit 2

# The identity of a measurement is its command, its tree and its selection, and
# a log that carries none of the three cannot be re-run by anybody. The
# authoritative 2026-11-16 run is the reason: its command had to be reconstructed
# afterwards from the shape of the output, because nothing printed it.
# Two things have to be true for a count in a log to be checkable later, and
# neither held before: pytest honours only the LAST -r flag, so a caller asking
# for -rf silently overrode the -rs below and the run lost every skip reason;
# and the verbosity the caller chose changes what the collector prints, so a
# log could not be reconciled against the tree it ran on. The lane therefore
# writes its own identity header (command, cwd, revision, collected count and
# node-id count) into every run directory and echoes it, rather than relying on
# pytest's own header surviving the caller's options, and it normalises -q for
# the collection step (further below).
LANE_ARGS="$*"

# What pytest was actually pointed at, decided from CLI semantics rather than
# from "some argument did not start with a dash": an option's value is not a
# selection ("-p no:warnings" used to convince this lane that specific tests
# were named, which silently switched the whole-suite ceiling off), while a
# -k/-m/--deselect/--ignore expression IS a narrowed selection even though it
# carries no path, and --collect-only executes nothing at all.
SELECTED_TESTS=""
FILTER_ARGS=""
COLLECT_ONLY=""
for arg in "$@"; do
  case "$arg" in
    --co | --collect-only)
      COLLECT_ONLY=YES
      ;;
    -k | -m | --deselect | --ignore | --ignore-glob)
      FILTER_ARGS="$FILTER_ARGS $arg"
      ;;
    -k?* | -m?* | --deselect=* | --ignore=* | --ignore-glob=*)
      FILTER_ARGS="$FILTER_ARGS $arg"
      ;;
    -*) ;;
    *)
      # What pytest can be pointed at is a path that exists here, optionally
      # followed by "::node::id".
      if [ -e "${arg%%::*}" ]; then
        SELECTED_TESTS="$SELECTED_TESTS $arg"
      fi
      ;;
  esac
done
EXPLICIT=""
if [ -n "$SELECTED_TESTS" ] || [ -n "$FILTER_ARGS" ]; then
  EXPLICIT=YES
fi
# What the identity artifact has to say about the selection. The gate's own
# decision reads $EXPLICIT, but a durable evidence file must not carry the
# shell's sentinel: a reader who finds "YES" cannot tell which way it was set.
SELECTION_KIND=${EXPLICIT:+explicit-selection}
SELECTION_KIND=${SELECTION_KIND:-whole-suite}

# One directory per run, created exclusively. Sharing an output path is not a
# convenience here: a focused run given the full-suite run's log path truncated
# it from byte 0 and left a hole where the identity header had been.
RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR=${ALETHRA_RUN_DIR:-${TMPDIR:-/tmp}/alethra-runs/$MODE-$RUN_STAMP-$$}
RUN_PARENT=${RUN_DIR%/*}
[ -n "$RUN_PARENT" ] || RUN_PARENT=/
# The default tree under $TMPDIR/alethra-runs is created with -p because it is
# storage, not a measurement. An explicitly named ALETHRA_RUN_DIR is not: its
# parent has to exist, otherwise a typo in the variable invents a directory and
# the run's evidence lands somewhere nobody will look for it again. The run
# directory itself is always created exclusively, so no two runs can share one.
# mkdir's failure is not a single cause, and conflating them put "the run
# directory already exists" on the screen when the truth was that TMPDIR named a
# directory that had never been created.
if [ -z "${ALETHRA_RUN_DIR:-}" ]; then
  mkdir -p "$RUN_PARENT" 2>/dev/null
elif [ ! -d "$RUN_PARENT" ]; then
  echo "refusing: ALETHRA_RUN_DIR=$RUN_DIR has no parent directory: $RUN_PARENT" >&2
  echo "It is not created for you: an evidence path that has to be invented mid-run" >&2
  echo "is a path a later reader cannot find. Create it, or name a real one." >&2
  exit 2
fi
if ! mkdir "$RUN_DIR" 2>/dev/null; then
  if [ -e "$RUN_DIR" ]; then
    echo "refusing: the run directory already exists: $RUN_DIR" >&2
    echo "nothing is reused, renamed-and-retried, or shared: unset ALETHRA_RUN_DIR" >&2
    echo "and let the lane make a new one." >&2
  else
    echo "refusing: the run directory could not be created: $RUN_DIR" >&2
    echo "its parent $RUN_PARENT is not writable, so nothing was run and nothing saved." >&2
  fi
  exit 2
fi
[ -d "$RUN_DIR" ] || {
  echo "refusing: mkdir reported success but $RUN_DIR is not a directory" >&2
  exit 2
}

# Where this run puts its evidence is this shell's private coordinate, not an
# input to the suite. It has to be deleted from the environment before pytest is
# started, because some of those tests start the lane themselves: an inherited
# ALETHRA_RUN_DIR made every one of those nested calls answer "the run directory
# already exists" and exit 2, and an inherited ALETHRA_LANE_LOG handed a child
# the parent's copy destination. The paths are kept here first so the gates
# below still read what the caller asked for.
LANE_LOG_COPY=${ALETHRA_LANE_LOG:-}
BUDGET_PATH=${ALETHRA_SKIP_BUDGET:-$BACKEND/tests/skip_budget.json}
MANIFEST_PATH=${ALETHRA_COLLECTED_MANIFEST:-$BACKEND/tests/collected_nodes.txt}
unset ALETHRA_RUN_DIR ALETHRA_LANE_LOG ALETHRA_SKIP_BUDGET ALETHRA_COLLECTED_MANIFEST

tree_digest() {
  find . -name '*.py' -type f \
    -not -path './.venv/*' -not -path './venv/*' \
    -not -path '*/__pycache__/*' -not -path './node_modules/*' \
    2>/dev/null | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 |
    cut -d' ' -f1
}

# The collector's output format is part of this lane's contract, so the quiet
# level is normalised instead of inherited. pytest reads repeated -q as a
# count, and a caller who already passed -q plus the -q appended here becomes
# -qq, which prints per-file totals ("tests/test_x.py: 20") instead of node ids
# and drops the "N tests collected" line -- leaving nothing to reconcile
# junit.xml against and a run that cannot be shown to be the pinned selection.
# Every -q form is therefore removed from the caller's arguments for this step
# only, and exactly one is re-added; the run below still uses "$@" untouched.
COLLECT_ARGS=()
for arg in "$@"; do
  case "$arg" in
    -q | -qq | -qqq | -qqqq | -qqqqq) ;;
    *) COLLECT_ARGS+=("$arg") ;;
  esac
done

# bash 3.2 expands an empty array as unbound under `set -u`, and the default
# whole-suite call is exactly that case.
if [ "${#COLLECT_ARGS[@]}" -gt 0 ]; then
  "$PYTHON" -m pytest "${COLLECT_ARGS[@]}" --collect-only -q > "$RUN_DIR/collect.txt" 2>&1
else
  "$PYTHON" -m pytest --collect-only -q > "$RUN_DIR/collect.txt" 2>&1
fi
CRC=$?
SELECTION=$(grep -E "collected|deselected" "$RUN_DIR/collect.txt" | tail -n 1)
COLLECTED_IDS=$(grep -cE '^[^[:space:]]+\.py(::.*)+$' "$RUN_DIR/collect.txt")

emit_identity() {
  echo "command: $0 $MODE $LANE_ARGS"
  echo "cwd: $(pwd)"
  echo "git: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo '<git could not answer>')$(
    git -C "$ROOT" status --porcelain 2>/dev/null | grep -q . && echo " (working tree dirty: run 'git status --porcelain' for the diff identity')"
  )"
  echo "porcelain: $(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ') path(s) sha256 $(git -C "$ROOT" status --porcelain 2>/dev/null | shasum -a 256 | cut -d' ' -f1)"
  echo "interpreter: $PYTHON"
  print_lane_fingerprint
  if [ "$MODE" = "pg" ]; then
    echo "target: ALETHRA_PG_TEST_URL (not printed)"
  else
    echo "target: sqlite $DB_FILE"
  fi
  echo "provider: DATA_PROVIDER=${DATA_PROVIDER:-<unset>}"
  echo "layer: $MODE"
  echo "selection-kind: $SELECTION_KIND${FILTER_ARGS:+ with filters:$FILTER_ARGS}${COLLECT_ONLY:+ (collect-only)}"
  echo "tree-digest-before: $(tree_digest)"
  echo "collected-node-ids: $COLLECTED_IDS"
  echo "selection on this tree: ${SELECTION:-<the collector printed no selection line>}"
}

emit_identity | tee "$RUN_DIR/identity.txt"
case "$SELECTION" in
  *"no tests collected"*)
    # A run that executes nothing cannot produce a result, so it is refused
    # rather than reported as a green 0/0.
    echo "refusing: the selection matched no tests, so this lane would run nothing." >&2
    echo "collect output: $RUN_DIR/collect.txt" >&2
    exit 2
    ;;
  *[0-9]*" collected"*) ;;
  *)
    # Everything deselected (or no count at all) is the same outcome: nothing
    # would be executed, and a log without a selected count cannot be audited.
    echo "refusing: no test was selected ('$SELECTION'), so this lane would run nothing." >&2
    echo "collect output: $RUN_DIR/collect.txt" >&2
    exit 2
    ;;
esac
if [ "$CRC" -ne 0 ]; then
  echo "note: the collector exited $CRC; a collection error is kept as evidence," >&2
  echo "      not smoothed over -- see $RUN_DIR/collect.txt."
fi

# A skip is self-reported by the very run it excuses, so the lane does not
# finish when pytest returns: the census of the reasons pytest printed is
# checked against the ceiling a real measurement wrote
# (backend/tests/skip_budget.json), and the outcome records in junit.xml are
# reconciled against the node ids in collect.txt. Over budget, a vanished
# node, or a selection that no longer matches the pinned one is reported as a
# failed run -- otherwise "nothing ran" and "nothing needed running" print the
# same green. The pg layer has no such ceiling yet, so it is not judged here.
"$PYTHON" -m pytest "$@" -rfEs --junitxml="$RUN_DIR/junit.xml" 2>&1 |
  tee "$RUN_DIR/pytest.log"
RC=${PIPESTATUS[0]}
echo "$RC" > "$RUN_DIR/exit-code.txt"

{
  echo "tree-digest-after: $(tree_digest)"
  echo "exit-code: $RC"
} | tee -a "$RUN_DIR/identity.txt"
TREE_BEFORE=$(grep '^tree-digest-before: ' "$RUN_DIR/identity.txt" | head -n 1 | cut -d' ' -f2)
TREE_AFTER=$(grep '^tree-digest-after: ' "$RUN_DIR/identity.txt" | head -n 1 | cut -d' ' -f2)
if [ "$TREE_BEFORE" = "$TREE_AFTER" ]; then
  echo "tree changed during run: no (python digest $TREE_BEFORE before and after)"
else
  echo "tree changed during run: YES ($TREE_BEFORE -> $TREE_AFTER)"
  echo "this run measures a moving tree; it is not a fixed-version result."
fi
echo "run dir: $RUN_DIR"
echo "console log: $RUN_DIR/pytest.log"
echo "outcome records: $RUN_DIR/junit.xml"
echo "exit code: $RUN_DIR/exit-code.txt (pytest's own status, recorded before any gate)"

# ALETHRA_LANE_LOG is a copy, not the place the run happens: the run directory
# above is what the gates read. The copy carries the identity header as well as
# the console output, because a log that cannot say which command and which
# revision produced it is only believable beside a transcript.
if [ -n "$LANE_LOG_COPY" ]; then
  LANE_LOG_PARTIAL="${LANE_LOG_COPY%.*}.$$.$RANDOM.partial"
  if cat "$RUN_DIR/identity.txt" "$RUN_DIR/pytest.log" > "$LANE_LOG_PARTIAL" 2>&1 &&
    mv -f "$LANE_LOG_PARTIAL" "$LANE_LOG_COPY" 2>&1; then
    echo "lane log copy: $LANE_LOG_COPY"
  else
    rm -f "$LANE_LOG_PARTIAL"
    echo "note: could not write the lane log copy to ALETHRA_LANE_LOG=$LANE_LOG_COPY;" >&2
    echo "      the run's own artifacts remain under $RUN_DIR." >&2
  fi
fi

if [ "$MODE" = "sqlite" ] && [ -z "$COLLECT_ONLY" ]; then
  CHECK=("$BACKEND/scripts/skip_budget.py" "$RUN_DIR/pytest.log" check --budget "$BUDGET_PATH")
  CHECK+=("--identity" "$RUN_DIR/identity.txt")
  EVIDENCE=("$BACKEND/scripts/run_evidence.py" "$RUN_DIR" check
    --budget "$BUDGET_PATH" --manifest "$MANIFEST_PATH")
  if [ -n "$EXPLICIT" ]; then
    CHECK+=("--explicit-selection")
    EVIDENCE+=("--explicit-selection")
  fi
  "$PYTHON" "${CHECK[@]}"
  BRC=$?
  "$PYTHON" "${EVIDENCE[@]}"
  ERC=$?
  GATE=$BRC
  [ "$ERC" -ne 0 ] && GATE=$ERC
  if [ "$RC" -eq 0 ] && [ "$GATE" -ne 0 ]; then
    echo "not green: pytest passed but the evidence gates refused (skip census $BRC, ledger $ERC)." >&2
    RC=$GATE
  fi
elif [ "$MODE" = "sqlite" ]; then
  echo "collect-only run: nothing executed, so no ceiling is judged and no ledger closes."
fi

exit "$RC"
```

---

## 5. `backend/scripts/skip_budget.py`

- 角色：gate；行 263；位元組 11061；SHA256（原檔）`d1da8e23305ad8f395e5b284b38d91c8f370b0870a5a63ae969630e531775d7c`；git **未追蹤**；遮罩 0 處。

```
#!/usr/bin/env python3
"""Skip census of a lane log, checked against a generated budget.

A skip is self-reported by the very run it excuses, so an unbounded skip count
turns "not executed" into a green line. This script makes the count a checked
quantity instead: `backend/tests/skip_budget.json` holds the per-category
ceiling, and a whole-suite lane run that exceeds it exits non-zero.

Three categories, kept apart on purpose:

  pg_only      the node needs a PostgreSQL server (row locks, psycopg2 type
               adaptation, NUMERIC, MVCC, current_database(), ALETHRA_PG_* unset)
  live_stack   the node needs an explicitly started stack (ALETHRA_LIVE_PROBES)
  other        any other excuse

Merging them would let a new non-PostgreSQL excuse hide inside the 160-odd
entries that already have a legitimate reason, so `other` carries the tightest
ceiling and is the one an auditor reads first.

The budget file is generated, never typed: `--write-budget` derives every number
from a real lane log and records that log's path, sha256 and HEAD so the ceiling
can be traced back to a measurement. Editing the numbers by hand to make a run
pass is the failure mode this exists to catch.

Exit: 0 within budget, 1 over budget or a self-check violation, 2 refused
(missing or malformed budget, whole-suite selection that does not match the
pinned one).
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys

CATEGORIES = ("pg_only", "live_stack", "other")

# SKIPPED [1] tests/x.py:76: PG-ONLY [tests/x.py:42]: <reason...>
# The bracket count is a grouping: pytest folds identical locations into
# SKIPPED [N], so the units contributed by a line are N, not 1.
SKIP_LINE = re.compile(r"^SKIPPED \[(\d+)\] (\S+?): \d+: (.*)$")
MODULE_SKIP_LINE = re.compile(r"^SKIPPED \[(\d+)\] (\S+?): (.*)$")
COLLECTED = re.compile(r"(\d+) tests? collected")
LIVE_STACK = "live-stack probe"
PGISH = re.compile(
    r"postgres|postgresql|alethra_pg|alethra_test_db|psycopg|mvcc|numeric|row lock|row-lock",
    re.I,
)


class Refused(Exception):
    """The check itself could not be performed; never report this as a pass."""


def classify(reason):
    if reason.startswith("PG-ONLY [") or reason.startswith("PG-SETUP ["):
        return "pg_only"
    if LIVE_STACK in reason:
        return "live_stack"
    if PGISH.search(reason):
        return "pg_only"
    return "other"


def census(log_text):
    """Return (units, per-category counts, rows) from a lane log."""
    counts = collections.Counter()
    units = 0
    rows = []
    for line in log_text.splitlines():
        if not line.startswith("SKIPPED ["):
            continue
        m = SKIP_LINE.match(line) or MODULE_SKIP_LINE.match(line)
        if not m:
            raise Refused(f"cannot parse a skip line: {line!r}")
        n = int(m.group(1))
        category = classify(m.group(3))
        counts[category] += n
        units += n
        rows.append((m.group(2), category, n, m.group(3)))
    return units, counts, rows


def collected_from(log_text):
    """The collected count the lane printed, or None when it printed none."""
    for line in log_text.splitlines():
        if line.startswith("selection on this tree:"):
            m = COLLECTED.search(line)
            if m:
                return int(m.group(1))
            return None
    return None


def load_budget(path):
    if not os.path.exists(path):
        raise Refused(
            f"no budget at {path}; generate it with --write-budget from a real lane "
            "log. A missing ceiling is not an unlimited ceiling."
        )
    try:
        with open(path, encoding="utf-8") as fh:
            budget = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"budget at {path} is unreadable: {exc}")
    for key in ("collected", "total") + CATEGORIES:
        if not isinstance(budget.get(key), int) or budget[key] < 0:
            raise Refused(f"budget at {path} has no usable integer {key!r}")
    parts = sum(budget[c] for c in CATEGORIES)
    if parts != budget["total"]:
        raise Refused(
            f"budget at {path} does not add up: {' + '.join(CATEGORIES)} = {parts} "
            f"but total = {budget['total']}"
        )
    return budget


def cmd_write_budget(args):
    log_text = open(args.log, encoding="utf-8", errors="replace").read()
    units, counts, rows = census(log_text)
    collected = collected_from(log_text)
    where = args.log
    if collected is None and args.identity:
        # The same fallback cmd_check uses: a caller who asked for `-q` gets no
        # "N tests collected" header, and the run's identity artifact holds the
        # number regardless of verbosity. Without it a quiet whole-suite run --
        # the shape the lane is normally driven in -- could never be re-measured.
        identity_text = open(args.identity, encoding="utf-8", errors="replace").read()
        collected = collected_from(identity_text)
        where = args.identity
    if collected is None:
        raise Refused(
            f"{args.log} carries no 'selection on this tree: N tests collected' line, "
            "so its skip census cannot be tied to a selection."
        )
    if units != sum(counts[c] for c in CATEGORIES):
        raise Refused("internal: census units and category counts disagree")
    head = sha = None
    for line in log_text.splitlines():
        if line.startswith("git: "):
            head = line[5:].strip()
    if os.path.exists(args.log):
        sha = hashlib.sha256(open(args.log, "rb").read()).hexdigest()
    payload = {
        "collected": collected,
        "total": units,
        **{c: counts[c] for c in CATEGORIES},
        "provenance": {
            "generated_by": "backend/scripts/skip_budget.py <lane log> write-budget",
            "source_log": args.log,
            "source_log_sha256": sha,
            "collected_from": where,
            "source_git_line": head,
            "note": "ceiling, not a target; regenerate only from a real lane log",
        },
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {args.out}: collected={collected} total={units} "
          + " ".join(f"{c}={counts[c]}" for c in CATEGORIES))
    return 0


def cmd_check(args):
    log_text = open(args.log, encoding="utf-8", errors="replace").read()
    budget = load_budget(args.budget)
    units, counts, rows = census(log_text)
    collected = collected_from(log_text)
    where = args.log
    if collected is None and args.identity:
        # A caller who asked for `-q` gets no "N tests collected" header in the
        # console transcript -- that is pytest's documented behaviour, not a
        # missing measurement. The lane records the same number in the run's
        # identity artifact regardless of the caller's verbosity, so it is read
        # from there instead of refusing a run that was measured perfectly.
        identity_text = open(args.identity, encoding="utf-8", errors="replace").read()
        collected = collected_from(identity_text)
        where = args.identity

    print(f"skip census of {args.log}: {units} units "
          + " ".join(f"{c}={counts[c]}" for c in CATEGORIES)
          + f" (categories sum to {sum(counts[c] for c in CATEGORIES)})")
    print(f"budget {args.budget}: collected={budget['collected']} total={budget['total']} "
          + " ".join(f"{c}={budget[c]}" for c in CATEGORIES))

    if args.explicit_selection:
        print("this run selected specific tests, so the whole-suite ceiling does not "
              "apply; the census is reported but not enforced.")
        for c in CATEGORIES:
            if counts[c] > budget[c]:
                print(f"  note: {c}={counts[c]} exceeds its whole-suite ceiling "
                      f"{budget[c]} because this is a subset run")
        return 0

    if collected is None:
        raise Refused(
            "neither %s%s printed a collected count, so this run cannot be shown to "
            "be the budgeted selection" % (
                args.log,
                "" if not args.identity else " nor " + args.identity)
        )
    if collected != budget["collected"]:
        raise Refused(
            f"this whole-suite run collected {collected} tests but the budget pins "
            f"{budget['collected']}: the selection changed (files added, removed or "
            "deselected), so the ceiling was not measured against this suite. "
            "Re-measure and regenerate the budget from a real lane log."
        )

    over = [c for c in CATEGORIES if counts[c] > budget[c]]
    if units > budget["total"]:
        over.append("total")
    if over:
        print("OVER BUDGET:", file=sys.stderr)
        for c in over:
            print(f"  {c}: measured {counts[c] if c != 'total' else units} "
                  f"> ceiling {budget[c]}", file=sys.stderr)
        print("  new skips must be justified in the ledger and the budget "
              "regenerated from a real log -- not raised to match.", file=sys.stderr)
        return 1
    print("within budget; headroom " + " ".join(
        f"{c}={budget[c] - counts[c]}" for c in CATEGORIES) +
        f" total={budget['total'] - units}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", help="lane log to census")
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("write-budget", help="generate the budget from a real lane log")
    w.add_argument("--out", required=True)
    w.add_argument("--identity", help="the run's identity.txt, the fallback source "
                                      "of the collected count when the caller's "
                                      "verbosity hid it from the transcript")
    c = sub.add_parser("check", help="census the log and enforce the budget")
    c.add_argument("--budget", required=True)
    c.add_argument("--identity", help="the run's identity.txt, the fallback source "
                                      "of the collected count when the caller's "
                                      "verbosity hid it from the transcript")
    c.add_argument("--explicit-selection", action="store_true",
                   help="the run named specific tests, so only report")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "write-budget":
            ns = argparse.Namespace(log=args.log, out=args.out, identity=args.identity)
            return cmd_write_budget(ns)
        if args.cmd == "check":
            ns = argparse.Namespace(log=args.log, budget=args.budget,
                                    identity=args.identity,
                                    explicit_selection=args.explicit_selection)
            return cmd_check(ns)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    ap.error("a subcommand is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

---

## 6. `backend/scripts/run_evidence.py`

- 角色：gate；行 588；位元組 26566；SHA256（原檔）`83570fb1f738e6a9ea139d60170db9dbd063e867203cb15b78fbaf36602b10bf`；git **未追蹤**；遮罩 0 處。

```
#!/usr/bin/env python3
"""Evidence ledger for one lane run dir, built from that run's own artifacts.

Why this exists: the 2026-11-17 full-suite run reported `3479 collected` and
`3298 passed + 183 skipped + 1 failed = 3482`, and the lane's log could not say
where the three extra outcome records came from. Parsing a `-q` log cannot: the
`SKIPPED [n] file:line:` prefix is the **raise site**, not the node, and four
different raise sites appear while only three records are extra. The JUnit
artifact does say, because pytest writes a collection-level skip as
`<testcase classname="" name="tests.test_x"><skipped message="collection skipped">`
while an item-level skip carries a real classname.

So every number in this ledger is derived from `junit.xml` plus `collect.txt`
(normalised node ids) and the three are reconciled against each other. Anything
that cannot be matched to an exact node id is printed under `UNRESOLVED` and
stays unresolved -- it is never closed by arithmetic on totals.

Commands:
  ledger  <run dir>                     print/write the reconciliation
  check   <run dir> [--manifest M] [--budget B] [--explicit-selection]
                                        enforce: no vanished node, no new skip
                                        category, no unacknowledged manifest diff
  pin     <run dir> --manifest-out M --budget-out B [--ack-diff SHA]
                                        rewrite the pinned selection and ceiling,
                                        which requires acknowledging the diff

Exit: 0 ok, 1 gate violation (unreconciled records, vanished nodes, over
budget, unacknowledged diff), 2 refused (missing or malformed artifacts).
"""
import argparse
import ast
import collections
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skip_budget import CATEGORIES, Refused, census, classify  # noqa: E402  one classifier

ARTIFACTS = ("identity.txt", "pytest.log", "junit.xml", "collect.txt", "exit-code.txt")
SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected|xfailed|xpassed)")


class Unreadable(Exception):
    """A named artifact is missing or malformed -- a refusal, never a pass."""


def read(run_dir, name):
    path = os.path.join(run_dir, name)
    if not os.path.exists(path):
        raise Unreadable(
            f"{path} is missing. This tool only reconciles a run that saved its "
            "own artifacts; it will not reconstruct them from memory."
        )
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def split_node(rest):
    """`::`-split a node id's non-path part, treating `[...]` as one token.

    A parametrised id may itself contain the separator: `test_loopback_hosts[::1]`
    is the IPv6 loopback address, not a class boundary. Splitting it naively
    produces the name `1]` under a class path ending in `[`, which matches no
    JUnit record, and the ledger then reports one collected node with no outcome
    *and* one outcome with no node for a test that passed cleanly.
    """
    parts, buf, depth, i = [], [], 0, 0
    while i < len(rest):
        char = rest[i]
        if char == "[":
            depth += 1
        elif char == "]" and depth:
            depth -= 1
        if depth == 0 and rest[i:i + 2] == "::":
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(char)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p]


def junit_key(node_id):
    """The (classname, name) pair pytest writes for this exact node id."""
    module, _, rest = node_id.partition("::")
    parts = split_node(rest)
    name = parts[-1] if parts else ""
    cls = parts[:-1]
    stem = module[:-3] if module.endswith(".py") else module
    return (".".join([stem.replace("/", ".")] + cls), name)


def key_index(ids):
    index = {}
    for node_id in ids:
        index.setdefault(junit_key(node_id), node_id)
    return index


COLLECTION_SKIP = re.compile(r"""['"]Skipped:\s*(.*)['"]\s*\)\s*$""", re.S)


def collection_reason(text):
    """The sentence a module-level skip used, out of pytest's own repr.

    A collection-level `<testcase>` carries `message="collection skipped"` and
    puts the real sentence in the element text, as the Python repr of the tuple
    (path, lineno, "Skipped: <sentence>"). That repr quotes the inner string
    with `"` whenever the sentence contains an apostrophe -- and every PG-ONLY
    sentence does, because they say "PostgreSQL's" -- so a reader written for
    one quote style finds nothing, hands the classifier the placeholder instead
    of the sentence, and re-labels three PostgreSQL-only skips as `other` while
    the console census still calls them `pg_only`. Decoding the literal is
    exact; the pattern below is the fallback, never a guess.
    """
    stripped = (text or "").strip()
    if stripped.startswith("("):
        try:
            parts = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            parts = None
        if (isinstance(parts, (list, tuple)) and len(parts) == 3
                and isinstance(parts[2], str)):
            sentence = parts[2].strip()
            return (sentence[len("Skipped:"):].strip()
                    if sentence.startswith("Skipped:") else sentence)
    m = COLLECTION_SKIP.search(stripped)
    if m:
        return m.group(1).strip()
    return None


def skip_reason(child):
    """The sentence the node used to excuse itself, from either JUnit shape.

    An item-level skip carries it in `message`; a collection-level skip carries
    `message="collection skipped"` and puts the real sentence in the element
    text. Reading only `message` would classify every module skip as `other`
    and make this census disagree with the one skip_budget.py takes from the
    console -- a silent reclassification is exactly what this tool exists to
    catch.
    """
    message = (child.get("message") or "").strip()
    if message and message != "collection skipped":
        return message
    found = collection_reason(child.text)
    if found:
        return found
    return message


def parse_junit(text, known):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise Unreadable(f"junit.xml is not parseable: {exc}")
    suites = root.findall("testsuite") or ([root] if root.tag == "testsuite" else [])
    if not suites:
        raise Unreadable("junit.xml has no <testsuite> element")
    records = []
    attrs = collections.Counter()
    for suite in suites:
        for key in ("tests", "skipped", "failures", "errors"):
            value = suite.get(key)
            if value is not None:
                attrs[key] += int(value)
        for testcase in suite.findall("testcase"):
            classname = testcase.get("classname", "")
            name = testcase.get("name", "")
            if classname == "":
                kind, ident = "collection", name
            elif (classname, name) in known:
                kind, ident = "item", known[(classname, name)]
            else:
                kind, ident = "unresolved", f"{classname}::{name}"
            child = testcase.find("skipped")
            outcome = "passed"
            reason = ""
            if child is not None:
                outcome = "skipped"
                reason = skip_reason(child)
            elif testcase.find("failure") is not None:
                outcome = "failed"
                reason = (testcase.find("failure").get("message") or "").strip()
            elif testcase.find("error") is not None:
                outcome = "error"
                reason = (testcase.find("error").get("message") or "").strip()
            records.append({"id": ident, "kind": kind, "outcome": outcome,
                            "reason": reason})
    return records, attrs


NODE_ID = re.compile(r"^\S+\.py(?:::.+)+$")


def manifest_ids(text):
    """The node ids exactly as the collector printed them, rootdir-relative.

    Three of these suites live at `backend/` rather than `backend/tests/`
    (`test_provider_isolation.py` and friends), so their ids legitimately have
    no directory. Rewriting them to `tests/...` here would make the ledger
    disagree with pytest about which node ran. A parametrised id may contain
    spaces, which is why the match is anchored on `path.py::` rather than on
    "the line has no blanks".
    """
    return [line.strip() for line in text.splitlines() if NODE_ID.match(line.strip())]


def summary_counts(log_text):
    """The numbers from pytest's own final summary line, if the log has one."""
    tail = [line for line in log_text.splitlines() if SUMMARY_COUNT.search(line)
            and ("passed" in line or "failed" in line or "error" in line or "skipped" in line)]
    if not tail:
        return {}
    counts = {}
    for value, label in SUMMARY_COUNT.findall(tail[-1]):
        counts[label] = counts.get(label, 0) + int(value)
    return {"line": tail[-1].strip("=").strip(), "counts": counts}


def build_ledger(run_dir):
    ids = manifest_ids(read(run_dir, "collect.txt"))
    known = set(ids)
    records, attrs = parse_junit(read(run_dir, "junit.xml"), key_index(ids))
    log_text = read(run_dir, "pytest.log")
    summary = summary_counts(log_text)
    exit_code = read(run_dir, "exit-code.txt").strip()

    by_outcome = collections.Counter()
    item_skips = collections.Counter()
    collection_skips = collections.Counter()
    duplicate_ids = [ident for ident, n in collections.Counter(
        r["id"] for r in records).items() if n > 1]
    for record in records:
        by_outcome[record["outcome"]] += 1
        if record["outcome"] != "skipped":
            continue
        # A collection-level skip excuses itself with the same kind of sentence
        # an item-level one does, and skip_budget.py counts both as units. The
        # ledger keeps them in the same categories so the two censuses are
        # comparable, and keeps the split so units and items never get merged.
        category = classify(record["reason"])
        if record["kind"] == "collection":
            collection_skips[category] += 1
        else:
            item_skips[category] += 1
    skips = collections.Counter(item_skips)
    skips.update(collection_skips)
    category_total = sum(skips[c] for c in CATEGORIES)

    item_records = [r for r in records if r["kind"] == "item"]
    collection_records = [r for r in records if r["kind"] == "collection"]
    unresolved = [r for r in records if r["kind"] == "unresolved"]
    executed_item_ids = {r["id"] for r in item_records}
    never_ran = sorted(known - executed_item_ids)

    collected_items = len(known)
    reported_units = attrs["tests"] if attrs["tests"] else len(records)
    item_side = (by_outcome["passed"] + by_outcome["failed"] +
                 by_outcome["error"] + by_outcome["skipped"] -
                 len(collection_records))

    # The same run, censused a second way: skip_budget.py reads the sentences
    # pytest printed, this ledger reads the sentences pytest recorded. When the
    # two disagree, one of them is mis-binning or re-counting, and the ledger
    # says so instead of picking the convenient number.
    try:
        log_units, log_counts, _ = census(log_text)
        console_census = {"units": log_units,
                          **{c: log_counts[c] for c in CATEGORIES}}
    except Refused as exc:
        console_census = {"error": str(exc)}

    disagreements = []
    if "error" in console_census:
        disagreements.append("the console census could not be parsed: "
                            + console_census["error"])
    else:
        for c in CATEGORIES:
            if console_census[c] != skips[c]:
                disagreements.append(
                    f"{c}: console says {console_census[c]}, the recorded "
                    f"outcomes say {skips[c]}")
        if console_census["units"] != category_total:
            disagreements.append(
                f"units: console says {console_census['units']}, the recorded "
                f"outcomes say {category_total}")

    ledger = {
        "run_dir": run_dir,
        "identity": {line.split(":")[0]: line.split(":", 1)[1].strip()
                     for line in read(run_dir, "identity.txt").splitlines()
                     if ":" in line},
        "exit_code": exit_code,
        "collected_items": collected_items,
        "junit": {"tests": attrs["tests"], "skipped": attrs["skipped"],
                  "failures": attrs["failures"], "errors": attrs["errors"]},
        "pytest_summary_line": summary.get("line"),
        "pytest_summary_counts": summary.get("counts", {}),
        "outcomes": dict(by_outcome),
        "collection_level_skip_modules": sorted(r["id"] for r in collection_records),
        "skip_categories": {c: skips[c] for c in CATEGORIES},
        "skip_category_total": category_total,
        "console_census": console_census,
        "census_disagreements": disagreements,
        "duplicate_collected_lines": sorted(
            i for i, n in collections.Counter(ids).items() if n > 1),
        "item_level_skips": {c: item_skips[c] for c in CATEGORIES},
        "collection_level_skips_by_category": {c: collection_skips[c] for c in CATEGORIES},
        "collection_level_skips": len(collection_records),
        "reconciliation": {
            "reported_units": reported_units,
            "collected_items": collected_items,
            "delta_units_minus_items": reported_units - collected_items,
            "collection_level_records": len(collection_records),
            "items_accounted": item_side,
            "items_vs_records_ok": item_side == collected_items,
            "delta_explained_ok": len(collection_records) ==
                                  reported_units - collected_items,
        },
        "unresolved_records": [{"id": r["id"], "outcome": r["outcome"],
                                "reason": r["reason"][:200]} for r in unresolved],
        "duplicate_records": duplicate_ids,
        "collected_but_no_record": never_ran,
        "records_without_node_id": [r["id"] for r in unresolved],
    }
    ledger["artifact_sha256"] = {
        name: hashlib.sha256(open(os.path.join(run_dir, name), "rb").read()).hexdigest()
        for name in ARTIFACTS if os.path.exists(os.path.join(run_dir, name))
    }
    return ledger


def render(ledger):
    out = []
    recon = ledger["reconciliation"]
    out.append(f"run dir            {ledger['run_dir']}")
    out.append(f"exit code          {ledger['exit_code']}")
    out.append(f"collected items    {ledger['collected_items']} (collect.txt)")
    out.append(f"junit records      tests={ledger['junit']['tests']} "
               f"skipped={ledger['junit']['skipped']} failures={ledger['junit']['failures']} "
               f"errors={ledger['junit']['errors']}")
    out.append(f"pytest summary     {ledger['pytest_summary_line']}")
    out.append(f"outcomes           " + " ".join(
        f"{k}={v}" for k, v in sorted(ledger["outcomes"].items())))
    out.append(f"skip categories    pg_only={ledger['skip_categories']['pg_only']} "
               f"live_stack={ledger['skip_categories']['live_stack']} "
               f"other={ledger['skip_categories']['other']} "
               f"(total {ledger['skip_category_total']} = item "
               f"{sum(ledger['item_level_skips'].values())} + collection "
               f"{sum(ledger['collection_level_skips_by_category'].values())})")
    cc = ledger["console_census"]
    if "error" in cc:
        out.append(f"console census      unparseable: {cc['error']}")
    else:
        out.append(f"console census      units={cc['units']} "
                   + " ".join(f"{c}={cc[c]}" for c in CATEGORIES)
                   + (" -> agrees with the recorded outcomes"
                      if not ledger["census_disagreements"] else
                      " -> DISAGREES: " + "; ".join(ledger["census_disagreements"])))
    out.append(f"collection-level   {ledger['collection_level_skips']} "
               f"module(s): {', '.join(ledger['collection_level_skip_modules']) or '-'}")
    out.append(f"reported units     {recon['reported_units']} = collected "
               f"{recon['collected_items']} + collection-level "
               f"{recon['collection_level_records']} -> "
               f"{'OK' if recon['items_accounted'] and recon['delta_explained_ok'] else 'DOES NOT RECONCILE'}")
    for label, key in (("unresolved records", "unresolved_records"),
                       ("duplicate records", "duplicate_records"),
                       ("collected but no record", "collected_but_no_record"),
                       ("record without id", "records_without_node_id")):
        entries = ledger[key]
        out.append(f"{label:<19} {len(entries)}" +
                   (f"  <- UNRESOLVED: {', '.join(str(e) for e in entries[:5])}" if entries else ""))
    return "\n".join(out)


def cmd_ledger(args):
    ledger = build_ledger(args.run_dir)
    text = render(ledger)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"ledger json: {args.json_out}")
    return 0


def structural_failures(ledger):
    """Why this run's own artifacts do not add up, with no ceiling in sight.

    These are the checks that survive a focused run: a subset has no business
    being judged against the whole suite's ceiling, but its junit records still
    have to account for its collected items, or the numbers coming out of it
    are not a measurement of anything.
    """
    recon = ledger["reconciliation"]
    failures = []
    if ledger["census_disagreements"]:
        failures.extend("the two censuses of the same run disagree, so neither "
                        "number may be quoted as this run's skip count: " + line
                        for line in ledger["census_disagreements"])
    if ledger["duplicate_collected_lines"]:
        failures.append("collect.txt lists a node id more than once: "
                        + ", ".join(ledger["duplicate_collected_lines"][:5]))
    if ledger["unresolved_records"]:
        failures.append(f"{len(ledger['unresolved_records'])} junit record(s) could not "
                        "be mapped to a collected node id")
    if ledger["duplicate_records"]:
        failures.append(f"duplicate node id(s) recorded: "
                        f"{', '.join(ledger['duplicate_records'][:5])} -- a run that "
                        "reports one node twice is not a count of nodes")
    if ledger["collected_but_no_record"]:
        failures.append(f"{len(ledger['collected_but_no_record'])} collected node(s) "
                        "produced no outcome record (whole-file skip or crash)")
    if not recon["items_accounted"]:
        failures.append(f"item records ({recon['items_accounted']}) do not equal "
                        f"collected items ({recon['collected_items']})")
    if not recon["delta_explained_ok"]:
        failures.append("the difference between reported units and collected items is "
                        "not accounted for by collection-level records; the ledger "
                        "does not close and will not be closed by subtraction")
    return failures


def cmd_check(args):
    ledger = build_ledger(args.run_dir)
    print(render(ledger))
    failures = structural_failures(ledger)
    if args.explicit_selection:
        # A subset cannot hold the whole suite's 164 PostgreSQL-only nodes, so
        # its census is reported and not judged -- but its own artifacts still
        # have to add up, which is what the block above checks.
        print("this run selected specific tests, so the whole-suite ceiling does not "
              "apply; the census is reported but not enforced.")
    else:
        collected_now = set(manifest_ids(read(args.run_dir, "collect.txt")))
        if args.manifest:
            if os.path.exists(args.manifest):
                with open(args.manifest, encoding="utf-8") as fh:
                    pinned = set(manifest_ids(fh.read()))
            else:
                pinned = None
                failures.append(
                    f"no pinned selection manifest at {args.manifest}. A whole-suite "
                    "run is only comparable once the node ids it collected are pinned "
                    "by `run_evidence.py <run dir> pin`; totals alone cannot show that "
                    "a node was deleted.")
            if pinned is not None:
                gone = sorted(pinned - collected_now)
                added = sorted(collected_now - pinned)
                if gone:
                    failures.append(f"{len(gone)} pinned node(s) are no longer collected "
                                    f"(removed, renamed or made uncollectable): "
                                    f"{', '.join(gone[:5])}")
                if added:
                    failures.append(f"{len(added)} node(s) are collected but not pinned; the "
                                    "pinned selection is re-written only by `pin --ack-diff`, "
                                    "never by accepting the newest total: "
                                    f"{', '.join(added[:5])}")
        if args.budget and os.path.exists(args.budget):
            with open(args.budget, encoding="utf-8") as fh:
                budget = json.load(fh)
            for c in CATEGORIES:
                if ledger["skip_categories"][c] > budget.get(c, 0):
                    failures.append(f"{c}: measured {ledger['skip_categories'][c]} > "
                                    f"ceiling {budget[c]}")
            if ledger["skip_category_total"] > budget.get("total", 0):
                failures.append(f"total: measured {ledger['skip_category_total']} > "
                                f"ceiling {budget['total']}")
    if failures:
        print("EVIDENCE GATE REFUSES:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("evidence gate: ledger closes against this run's own artifacts")
    return 0


def diff_block(pinned, current):
    gone = sorted(pinned - current)
    added = sorted(current - pinned)
    body = "\n".join(["-" + g for g in gone] + ["+" + a for a in added])
    return body, gone, added


def cmd_pin(args):
    ledger = build_ledger(args.run_dir)
    current = set(manifest_ids(read(args.run_dir, "collect.txt")))
    broken = structural_failures(ledger)
    if broken:
        print("refused: a ceiling is not pinned from a run whose own ledger does not "
              "close:", file=sys.stderr)
        for line in broken:
            print(f"  - {line}", file=sys.stderr)
        return 2
    if ledger["exit_code"] not in ("0", "1"):
        print(f"refused: this run exited {ledger['exit_code']!r}, which is not a "
              "measurement of a suite that ran, so no ceiling and no manifest come "
              "from it.", file=sys.stderr)
        return 2
    pinned = set()
    if os.path.exists(args.manifest_out):
        pinned = set(manifest_ids(open(args.manifest_out, encoding="utf-8").read()))
    body, gone, added = diff_block(pinned, current)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    print(f"selection diff vs pinned: -{len(gone)} +{len(added)}")
    print("acknowledgement digest: " + digest)
    for line in body.splitlines()[:40]:
        print("  " + line)
    if body and args.ack_diff != digest:
        print("refused: re-pinning requires --ack-diff <the digest above>, so the "
              "exact added/removed node ids have to be read before the ceiling moves.",
              file=sys.stderr)
        return 2
    with open(args.manifest_out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(current)) + "\n")
    provenance = {
        "run_dir": args.run_dir,
        "exit_code": ledger["exit_code"],
        "collected_items": ledger["collected_items"],
        "artifacts": ledger["artifact_sha256"],
        "identity": ledger["identity"],
    }
    payload = {
        "collected": ledger["collected_items"],
        "total": ledger["skip_category_total"],
        **{c: ledger["skip_categories"][c] for c in CATEGORIES},
        "provenance": {
            "generated_by": "backend/scripts/run_evidence.py <run dir> pin",
            "note": "ceiling, not a target; regenerated only from a run dir whose "
                    "ledger closes, and only after reading the selection diff",
            # The console census is the one body of text both tools can re-read, so
            # the budget names it even when the pin -- not write-budget -- wrote it.
            "source_log": os.path.join(args.run_dir, "pytest.log"),
            "source_log_sha256": ledger["artifact_sha256"]["pytest.log"],
            **provenance,
        },
    }
    with open(args.budget_out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"pinned {args.manifest_out} ({len(current)} node ids) and {args.budget_out} "
          f"(total={payload['total']} " +
          " ".join(f"{c}={payload[c]}" for c in CATEGORIES) + ")")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    sub = ap.add_subparsers(dest="cmd")
    l = sub.add_parser("ledger")
    l.add_argument("--json-out")
    c = sub.add_parser("check")
    c.add_argument("--manifest")
    c.add_argument("--budget")
    c.add_argument("--explicit-selection", action="store_true")
    p = sub.add_parser("pin")
    p.add_argument("--manifest-out", required=True)
    p.add_argument("--budget-out", required=True)
    p.add_argument("--ack-diff")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "ledger":
            return cmd_ledger(args)
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "pin":
            return cmd_pin(args)
    except (Unreadable, Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    ap.error("a subcommand is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

---

## 7. `backend/scripts/check_env_hygiene.py`

- 角色：gate；行 620；位元組 30324；SHA256（原檔）`bd07aef661c865e7934e8c3e7cda58dbc2718173f523193f20f6b6df16b43820`；git **未追蹤**；遮罩 3 處。
- 遮罩位置（原檔行號／形狀／鍵名）：20（url，inline-userinfo）、57（url，inline-userinfo）、351（url，inline-userinfo）

```
"""One answer to "does this tree keep a credential in plain text?".

The trigger was ``backend/.env.test``. It is tracked by git, and its first
commit (``5af8521``) wrote a database URL with the password inline. The next
commit (``77e84ca``) replaced that password with
``${ALETHRA_TEST_RUNTIME_PASSWORD}``, so the current file is clean — but the
old line is still reachable from history, and nothing in the repository
noticed either way. ``.gitignore`` covers ``.env``, ``.env.local`` and
``.env.*.local`` and says nothing about ``.env.test``, which is why the file
could be committed at all.

A reminder in a document cannot catch the next one, so this module is the
check. Three rules, each with a scope chosen so that it cannot be noise:

* ``credential_literal`` — a credential-shaped key assigned a literal value
  **in a file that configures the running application**: ``.env*`` files, shell
  entry points and compose/yaml environment blocks. Python source is excluded
  on purpose: ``password_hash=get_password_hash(...)`` is a function argument,
  and a gate that reports 159 of those is a gate nobody reads.
* ``literal_url_password`` — ``scheme://user:‹REDACTED›@host`` with an inline
  password in the same scope, which is how the original defect was written.
* ``provider_token`` — a live-format provider key (Stripe/OpenAI/GitHub/AWS…)
  or a signed JWT, searched in **every** tracked text file. The shape alone
  makes the call, so this rule has no scope problem: a real key is a real key
  wherever it appears, including inside a template that was published
  deliberately.
* ``credential_default`` — a credential-shaped name with a literal standing
  behind it as the *default*: ``process.env.X_PASSWORD || "<literal>"`` and
  ``os.environ.get("X_PASSWORD", "<literal>")``. Searched in every tracked text
  file for the same reason as the rule above, and it is a different defect from
  ``credential_literal``: an assignment states a value, a default states that
  the program **still runs** when the operator forgets to inject one. The
  frontend acceptance specs are exactly that shape, which is why they were
  invisible while the assignment rule was limited to configuration files.

Accepted instead of reported: an empty value, a ``${VAR}`` / ``${VAR:-…}`` /
``${VAR:?…}`` / ``$VAR`` injection, a ``$(…)`` value generated at run time, and
a word that says it is a placeholder (``your-secret-key``, ``change-me…``,
``xxx``, ``<token>``). The line the vocabulary draws is *instruction versus
credential*: a value that tells you to substitute something is an instruction,
while a value you could log in with is a credential even if everybody knows it.
So ``postgres``, ``password``, ``secret``, ``admin`` and ``alethra`` are not on
the list — they are the strings a reader might actually be told to use, and each
one works as a password.

Findings carry path, line, key name, a kind and a content digest. The value is
never read into a finding's text, so running this tool -- or pasting its output
into a log, an issue or a test failure -- cannot leak what it looks for. The
length used to be printed and is not: for a four-character default password a
character count is the first thing an attacker already knows.

What the scanner supports, and what it cannot see:

* assignment syntax on one line, in ``.env*`` and compose/yaml environment
  blocks. A credential assembled across lines, built by string concatenation,
  or read from a file at run time is outside its grammar and is not reported.
* the inline ``scheme://user:‹REDACTED›@host`` form, and provider-key and JWT shapes
  in any tracked text file. A key that has been base64'd, encrypted or split
  into variables is not recognised by shape.
* a default that is a *literal* at the ``getenv(… , X)`` / ``X || "…"`` position.
  A fallback computed by a call, built across lines, or moved behind a name
  (``const P = "…"; env.X || P``) has no literal at the operator and is not
  reported. That limit is also why the contract tests assemble their refused
  lines from constants instead of writing them out: this repository is what the
  scanner reads, so a fixture left in the open becomes a real occurrence of the
  debt the ledger counts.
* tracked files (``git ls-files``) by default, and with ``--pre-commit`` the
  files a commit would actually carry: the index **plus** everything untracked
  and not ignored (``git ls-files --others --exclude-standard``). The second
  scope exists because the first has a specific hole -- a new
  ``backend/migrations/versions/x.py`` or ``backend/app/api/y.py`` that has not
  been ``git add``-ed yet is invisible to ``ls-files`` and is exactly the file
  that gets a secret into the first commit. An *ignored* untracked file (a real
  ``.env`` on a laptop, a build artifact, anything in ``node_modules``/venv) is
  still not scanned, and neither is a file force-added past an ignore rule:
  those are only visible once they enter the index, at which point the default
  scope sees them. Binary files and files over 2 MB are skipped in both scopes.
* **not git history.** This answers "what does the tree say now". A password
  removed from a later commit is still reachable from the commit that wrote it
  (``backend/.env.test`` at ``5af8521``, whose value class is UNKNOWN here),
  and only history rewriting addresses that. It is a separate, owner-approved
  operation, not a finding this tool can withdraw or downgrade.

Exit codes: 0 when the scan equals ``TRACKED_DEBT`` exactly, 1 when git could
not answer (an unreadable tree is never a pass), 2 when the tree and the ledger
disagree — a new literal, a ledgered position now holding different text, a
ledger entry that has been fixed but not deleted, or an occurrence count that
moved.

NOT RUN against PostgreSQL or any network: this reads the working tree only.
It starts no service, opens no socket and writes nothing.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

SECRET_KEY_SHAPE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|API_?KEY|ACCESS_KEY|PRIVATE_KEY|"
    r"AUTH_TOKEN|BEARER_TOKEN|CREDENTIAL|SIGNING_KEY)"
)
# The fallback-default rule accepts a bare ``TOKEN`` too. The assignment rule
# above does not, because ``TOKEN_COUNT=12`` and ``X_TOKENS=[…]`` are
# configuration; a *literal standing behind* ``||`` has nowhere harmless to be.
CREDENTIAL_NAME_SHAPE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|API_?KEY|ACCESS_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL|SIGNING_KEY)"
)
ENV_GET_DEFAULT = re.compile(
    r"""(?:environ\.get|getenv)\(\s*["']([A-Za-z0-9_]+)["']\s*,\s*["']([^"']+)["']"""
)
OR_DEFAULT = re.compile(
    r"""([A-Za-z0-9_]+)\s*\|\|\s*["']([^"']+)["']"""
)
NUMERIC_VALUE = re.compile(r"^[\d.]+$")
# ``${VAR}`` and every shell default/error form — ``${VAR:-d}``, ``${VAR-d}``,
# ``${VAR:?e}``, ``${VAR?e}``, ``${VAR:+a}`` — the value is injected by the
# caller, so the file carries no secret. ``:?`` additionally refuses to source,
# which is why the template uses it and backend/.env.test.example is checked
# for that form specifically.
TEMPLATE_REFERENCE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::?[-+?=#][^}]*)?\}$")
ENV_REFERENCE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")
RUNTIME_GENERATED = re.compile(r"^(\$\(|`)")
REFERENCE_VALUE = re.compile(r"^\$\{?[A-Za-z_]")

# Strings that mean "substitute me", never a credential. A default password is
# not on this list on purpose: ``postgres``, ``password``, ``admin``, ``secret``
# and ``alethra`` are all values a reader might be told to use, and each one is
# a credential. Neither is ``change-this-to-…``: a compose file applies its
# value whether or not a human meant to change it, so those lines stay findings.
PLACEHOLDER_VALUE = re.compile(
    r"^(?:(?:your|my|sample|placeholder|example|dummy|fake)[_-][A-Za-z0-9_-]+|"
    r"(?:change|replace)[ _-]?(?:me|this|that)[A-Za-z0-9 _-]*|"
    r"(?:put|set|insert)[_-][A-Za-z0-9_-]+|"
    r"todo|tbd|t\.b\.d|none|null|<[^>]*>|\[[^\]]*\]|\{\{[^}]*\}\}|\*{3,}|x{3,}|0{3,})$",
    re.IGNORECASE,
)

PROVIDER_TOKEN_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("stripe", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("stripe_publishable", re.compile(r"\bpk_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("stripe_webhook", re.compile(r"\bwhsec_[A-Za-z0-9]{16,}\b")),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{28,}\b")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("signed_jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

# Extensions read at all, for the provider-token rule.
TEXT_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".ts", ".tsx", ".js", ".jsx", ".mjs",
     ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".md",
     ".txt", ".example", ".tpl", ".template", ".service", ".unified", ".env"}
)
# Extensions whose *assignments* are application configuration. Shell scripts
# are deliberately absent: a script line is as likely to be a command, a
# heredoc or a fixture password handed to curl as it is to be environment
# configuration, and a rule that guesses wrong in either direction is worse
# than no rule. The script-side literals are inventoried instead in
# EXECUTION_RECORD_20261109.md §去敏紀錄 7 (acceptance JWT, 8 tracked files /
# 10 occurrences) and the same section's second class (test:test@…:5434).
SETTINGS_SUFFIXES = frozenset({".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env"})
SKIP_NAME_PARTS = ("node_modules", ".venv", "venv", "__pycache__", ".next")
MAX_FILE_BYTES = 2_000_000

ENV_STYLE_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
YAML_ASSIGNMENT = re.compile(r"^-?\s*([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
YAML_ITEM_ASSIGNMENT = re.compile(r"^\s*-\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def is_injected(value: str) -> bool:
    """True when something else supplies the value at run time."""
    return bool(TEMPLATE_REFERENCE.match(value) or ENV_REFERENCE.match(value)
                or RUNTIME_GENERATED.match(value))


def digest(value: str) -> str:
    """A content binding for the ledger, not a disclosure of the value.

    The owner's objection to the old ledger was structural: an entry named only
    ``(path, key, kind)``, so any text written at that position was blessed --
    change ``POSTGRES_PASSWORD=postgres`` to a real password and the gate still
    said "matches the ledger". Binding the content closes that, and the binding
    is a hash because the alternative (a skeleton, or a length) is what the
    finding used to print and is exactly the guessable fingerprint that was
    withdrawn.

    What the hash reveals is bounded by what the repository already contains:
    every ledger entry is a literal sitting in a tracked file, so a dictionary
    that recovers the value from its digest is an attack on data the reader has
    already been handed. Nothing here is claimed for a value that is NOT in the
    tree -- which is why the ledger may only ever bind what ``scan`` returned.
    """
    return hashlib.sha256(b"alethra-env-hygiene-v1\x00" + value.encode("utf-8", "replace")).hexdigest()[:12]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    key: str
    kind: str
    value_digest: str

    def __str__(self) -> str:
        # No length, no digest, no fragment: a scan is run to be pasted into
        # logs and issue threads, and the previous form printed the character
        # count of every credential it found.
        return f"{self.path}:{self.line}: {self.key} -> {self.kind}"


@dataclass(frozen=True)
class Analysis:
    files_scanned: int
    settings_files: int
    findings: Tuple[Finding, ...]
    scope: str = "tracked"

    @property
    def summary(self) -> str:
        counts: dict = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        detail = "none" if not counts else ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return (
            f"{len(self.findings)} finding(s) across {self.files_scanned} {self.scope} text files "
            f"({self.settings_files} configuration files in assignment scope): {detail}"
        )


def tracked_files(repo_root: Path) -> List[str]:
    """Every path git tracks, or ``[]`` when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.split("\0") if p]


def _git_ls_files_z(repo_root: Path, *args: str) -> Optional[List[str]]:
    """One ``git ls-files -z`` call; ``None`` when git could not answer at all.

    ``None`` and ``[]`` are different answers and must not be merged: an empty
    list means git looked and found nothing, which is a fact about the tree,
    while no answer means the scan has no evidence and must not print a pass.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", *args],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.split("\0") if p]


def untracked_files(repo_root: Path) -> Optional[List[str]]:
    """Files present but not in the index and not ignored -- ``None`` if git is silent."""
    return _git_ls_files_z(repo_root, "--others", "--exclude-standard")


def commit_candidate_files(repo_root: Path) -> Optional[List[str]]:
    """What a commit would carry now: the index plus untracked-not-ignored paths.

    Returns ``None`` when git could not answer either half, because a scan that
    silently lost one of the two sets reports "clean" over a tree it never read.
    """
    index = _git_ls_files_z(repo_root)
    others = untracked_files(repo_root)
    if index is None or others is None:
        return None
    return sorted(set(index) | set(others))


def _suffix(path: str) -> str:
    name = Path(path).name
    if name.startswith(".env"):
        return ".env"
    return Path(name).suffix.lower()


def is_text_file(path: str) -> bool:
    if any(part in SKIP_NAME_PARTS for part in Path(path).parts):
        return False
    return _suffix(path) in TEXT_SUFFIXES


def is_settings_file(path: str) -> bool:
    """A file whose assignments configure the running application."""
    return is_text_file(path) and _suffix(path) in SETTINGS_SUFFIXES


def effective_value(raw: str) -> str:
    """Drop the inline comment, surrounding quotes and padding around a value."""
    value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def assignments(line: str) -> List[Tuple[str, str]]:
    """Credential-shaped ``(key, raw value)`` pairs this line assigns."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    out: List[Tuple[str, str]] = []
    for pattern in (ENV_STYLE_ASSIGNMENT, YAML_ASSIGNMENT, YAML_ITEM_ASSIGNMENT):
        match = pattern.match(stripped)
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def _authority(value: str) -> str:
    """``user:pass@host:port`` of a URL, with ``${…}`` bodies kept whole.

    A plain ``split(\"/\")`` would cut inside a message such as
    ``${VAR:?set this before /deploy}`` and leave an unterminated reference
    behind, which the rule would then report as a literal password.
    """
    rest = value.split("://", 1)[1]
    depth = 0
    for index, char in enumerate(rest):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "/" and depth == 0:
            return rest[:index]
    return rest


def _url_password(value: str) -> Optional[str]:
    """The password text of ``scheme://user:‹REDACTED›@host``, or ``None``.

    Parsed by hand: ``urlsplit`` reads the ``?`` inside
    ``${VAR:?message}`` as the query delimiter and hands back half a
    reference, which would be reported as a literal password in a file that
    has no password at all.
    """
    if "://" not in value:
        return None
    authority = _authority(value)
    if "@" not in authority:
        return None
    credentials = authority.rsplit("@", 1)[0]
    if ":" not in credentials:
        return None
    # The first colon separates user from password. Splitting on the last one
    # would read ``user:${VAR:?message}`` as a password called ``?message``.
    password = unquote(credentials.split(":", 1)[1])
    if password == "" or TEMPLATE_REFERENCE.match(password) or ENV_REFERENCE.match(password) \
            or PLACEHOLDER_VALUE.match(password):
        return None
    return password


def fallback_defaults(line: str) -> List[Tuple[str, str]]:
    """Credential-shaped ``(name, literal)`` pairs this line uses as a default.

    An empty default (``getenv("X", "")``) is not a credential and the patterns
    require at least one character for that reason. What this cannot see is a
    default built at run time -- ``getenv("X", build_password())`` is a call, not
    a literal, and stays outside its grammar like every other multi-line form.
    """
    found: List[Tuple[str, str]] = []
    for pattern in (ENV_GET_DEFAULT, OR_DEFAULT):
        for match in pattern.finditer(line):
            name, value = match.group(1), match.group(2)
            if not CREDENTIAL_NAME_SHAPE.search(name.upper()):
                continue
            if is_injected(value) or PLACEHOLDER_VALUE.match(value) or NUMERIC_VALUE.match(value):
                continue
            if (name, value) not in found:
                found.append((name, value))
    return found


def classify(key: str, raw_value: str) -> List[Tuple[str, int]]:
    """Findings for one settings-file assignment; empty means the value is safe.

    The URL rule runs whatever the key is called: the defect that started this
    was a password inside ``DATABASE_URL``, and ``URL`` is not a
    credential-shaped word.
    """
    value = effective_value(raw_value)
    kinds: List[Tuple[str, str]] = []
    if _url_password(value) is not None:
        kinds.append(("literal_url_password", digest(value)))
    if SECRET_KEY_SHAPE.search(key.upper()) and value \
            and not is_injected(value) and not NUMERIC_VALUE.match(value) \
            and not PLACEHOLDER_VALUE.match(value):
        kinds.append(("credential_literal", digest(value)))
    return kinds


def scan_file(path: str, text: str) -> List[Finding]:
    findings: List[Finding] = []
    settings = is_settings_file(path)
    for number, line in enumerate(text.splitlines(), 1):
        for name, pattern in PROVIDER_TOKEN_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(Finding(path, number, "<inline>",
                                        f"provider_token:{name}", digest(match.group(0))))
        for name, value in fallback_defaults(line):
            findings.append(Finding(path, number, name, "credential_default", digest(value)))
        if not settings:
            continue
        for key, raw_value in assignments(line):
            for kind, value_digest in classify(key, raw_value):
                findings.append(Finding(path, number, key, kind, value_digest))
    return findings


def scan(repo_root: Path, paths: Optional[Sequence[str]] = None,
         scope: str = "tracked") -> Analysis:
    """Scan a file set, where ``scope`` names the set only ``paths`` does not give.

    The scope is part of the result rather than a detail of the call, because
    "no findings" over the index and "no findings" over the index plus the
    untracked files a commit would carry are two different claims, and the
    second is the one a pre-commit gate is allowed to print.
    """
    if paths is not None:
        candidates = list(paths)
    elif scope == "commit":
        enumerated = commit_candidate_files(repo_root)
        if enumerated is None:
            # No enumeration is not a clean tree: report zero files so the exit
            # code is the "git could not answer" one, never the "matches the
            # ledger" one.
            return Analysis(files_scanned=0, settings_files=0, findings=(), scope=scope)
        candidates = [p for p in enumerated if is_text_file(p)]
    else:
        candidates = [p for p in tracked_files(repo_root) if is_text_file(p)]
    findings: List[Finding] = []
    scanned = 0
    settings = 0
    for relative in candidates:
        target = repo_root / relative
        try:
            if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
                continue
            text = target.read_text(errors="replace")
        except OSError:
            continue
        scanned += 1
        settings += 1 if is_settings_file(relative) else 0
        findings.extend(scan_file(relative, text))
    return Analysis(files_scanned=scanned, settings_files=settings,
                    findings=tuple(findings), scope=scope)


# Credential literals git still tracks, measured 2026-11-17. Each one is an
# owner decision that is still open (EXECUTION_RECORD_20261109.md §去敏紀錄 7
# and §5 of the earlier round: rotate the role password, decide the acceptance
# JWT literal). They are listed instead of hidden so that (a) anything not on
# this list fails the gate, (b) an entry that has been fixed fails the gate
# until the line is deleted, and (c) a count that moves fails the gate. Fixing
# one of these is not this tool's job and needs its own approval: rewriting a
# compose file changes how the acceptance stack starts.
#   - ``postgres`` (the published default superuser password) written twice,
#     once as POSTGRES_PASSWORD and once inside the matching DATABASE_URL, in
#     each compose file — 4 entries.
#   - the acceptance lane's HS256 signing key literal in
#     ``docker-compose.acceptance.yml:30``, one of the 8 tracked files / 10
#     occurrences counted in §去敏紀錄 7.
#   - four ``credential_default`` entries added by this round's fourth rule: the
#     same two acceptance passwords standing as the fallback literal of
#     ``process.env.ACCEPTANCE_PASSWORD_[AB] || …`` in three e2e specs. Two
#     distinct values, four positions, so a fifth copy of either is reported
#     against the position it sits at and a rotation anywhere breaks every entry
#     that binds the same digest. Their consequence differs from the compose
#     files: each of those four lines is read straight into the password field
#     the spec submits to ``/login``, so a browser run that forgets to inject the
#     variable still authenticates -- with text from the source tree.
# Not listed, because the rule cannot call it a credential:
#   - ``docker-compose.yml:28`` JWT_SECRET_KEY whose value says ``change-me…``.
#     It is a placeholder phrase, not a leaked secret — but compose applies it
#     whenever the variable is not injected, so it is a fail-open default and is
#     recorded for the deployment section instead of hidden here.
#   - ``os.environ.get("…", "")`` defaults (four of them, measured the same
#     way). An empty default is the honest "unset means unset", which is why the
#     pattern requires a non-empty literal.
TRACKED_DEBT: Tuple[Tuple[str, str, str, int, str], ...] = (
    # (path, key, kind, occurrences, content digest) -- the last field is what
    # makes each entry a statement about *this text* rather than a standing
    # exemption for this path and key name. Generate them with
    # ``python3 backend/scripts/check_env_hygiene.py --emit-ledger``; never type
    # a digest, and never widen an entry to accommodate a changed value.
    ('docker-compose.acceptance.yml', 'DATABASE_URL', 'literal_url_password', 1, '52565aee4105'),
    ('docker-compose.acceptance.yml', 'JWT_SECRET_KEY', 'credential_literal', 1, '5f377d5d7bfb'),
    ('docker-compose.acceptance.yml', 'POSTGRES_PASSWORD', 'credential_literal', 1, 'efa1e5ece6f7'),
    ('docker-compose.yml', 'DATABASE_URL', 'literal_url_password', 1, '2044b89d40d1'),
    ('docker-compose.yml', 'POSTGRES_PASSWORD', 'credential_literal', 1, 'efa1e5ece6f7'),
    ('frontend/e2e/method-review.spec.ts', 'ACCEPTANCE_PASSWORD_A', 'credential_default', 1, 'd260f025af64'),
    ('frontend/e2e/research-drafts.spec.ts', 'ACCEPTANCE_PASSWORD_A', 'credential_default', 1, 'd260f025af64'),
    ('frontend/e2e/research-drafts.spec.ts', 'ACCEPTANCE_PASSWORD_B', 'credential_default', 1, '15124160c6f1'),
    ('frontend/e2e/strategy-preflight-feedback.spec.ts', 'ACCEPTANCE_PASSWORD_A', 'credential_default', 1, 'd260f025af64'),
)


def emit_ledger(analysis: Analysis) -> str:
    """The ledger literal this scan implies, as text a maintainer pastes once."""
    counts: dict = {}
    for finding in analysis.findings:
        key = (finding.path, finding.key, finding.kind, finding.value_digest)
        counts[key] = counts.get(key, 0) + 1
    lines = ["TRACKED_DEBT: Tuple[Tuple[str, str, str, int, str], ...] = ("]
    for (path, key, kind, value_digest), count in sorted(counts.items()):
        lines.append(f"    ({path!r}, {key!r}, {kind!r}, {count}, {value_digest!r}),")
    lines.append(")")
    return "\n".join(lines)


def reconcile(analysis: Analysis,
              debt: Tuple[Tuple[str, str, str, int, str], ...] = TRACKED_DEBT) -> dict:
    """Compare a scan with the debt ledger. Four ways to fail, none of them silent."""
    seen: dict = {}
    for finding in analysis.findings:
        key = (finding.path, finding.key, finding.kind, finding.value_digest)
        seen[key] = seen.get(key, 0) + 1
    expected = {(path, key, kind, value_digest): count for path, key, kind, count, value_digest in debt}
    ledgered_positions = {(path, key, kind) for path, key, kind, _d in expected}
    new = sorted(set(seen) - set(expected))
    resolved = sorted(set(expected) - set(seen))
    counts = sorted(k for k in set(seen) & set(expected) if seen[k] != expected[k])
    # A position the ledger already names, holding text the ledger does not:
    # the old four-field ledger had no way to say this, so it said "clean".
    changed = [k for k in new if k[:3] in ledgered_positions]
    new = [k for k in new if k not in changed]
    return {
        "clean": not new and not resolved and not counts and not changed,
        "new": new,
        "changed": changed,
        "resolved": resolved,
        "count_mismatch": [(k, seen[k], expected[k]) for k in counts],
        "ledger_total": sum(expected.values()),
        "found_total": sum(seen.values()),
        # Carried through so the sentence below can say *which* file set was
        # found clean; a pre-commit gate that prints "tracked" while it scanned
        # the index plus the untracked half would name the wrong evidence.
        "scope": analysis.scope,
    }


def verdict_text(verdict: dict) -> str:
    scope = verdict.get("scope", "tracked")
    if verdict["clean"]:
        return (
            f"matches the ledger: {verdict['found_total']} {scope} literal(s), "
            f"{verdict['ledger_total']} expected, no new credential"
        )
    parts = []
    if verdict["new"]:
        parts.append("NEW literals not in the ledger: "
                     + ", ".join(f"{p}:{k}({t})={d}" for p, k, t, d in verdict["new"]))
    if verdict["changed"]:
        parts.append("ledgered position now holds different text (the exemption "
                     "covers that exact value, not that key name): "
                     + ", ".join(f"{p}:{k}({t})={d}" for p, k, t, d in verdict["changed"]))
    if verdict["resolved"]:
        parts.append("ledger entries that no longer exist (delete them here): "
                     + ", ".join(f"{p}:{k}({t})={d}" for p, k, t, d in verdict["resolved"]))
    if verdict["count_mismatch"]:
        parts.append("occurrence counts moved: "
                     + ", ".join(f"{p}:{k}({t})={d} found={f} ledger={e}"
                                 for (p, k, t, d), f, e in verdict["count_mismatch"]))
    return "REFUSED — " + "; ".join(parts)


def main(argv: Iterable[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    argv = list(argv)
    show_all = "--all" in argv
    # The default stays "tracked" so that a run without the flag keeps meaning
    # exactly what it meant before; --pre-commit is the scope that answers the
    # question a commit actually asks, and it is what the hook and CI pass.
    scope = "commit" if "--pre-commit" in argv else "tracked"
    analysis = scan(repo_root, scope=scope)
    if "--emit-ledger" in argv:
        # The ledger is generated from the scan so that the content bindings are
        # measurements. It prints the tree's current literals, which is a
        # disclosure act of its own: use it to replace the block, then leave the
        # file alone until an entry is genuinely fixed.
        print(emit_ledger(analysis))
        return 0
    verdict = reconcile(analysis)
    print(analysis.summary)
    print(verdict_text(verdict))
    shown = analysis.findings if show_all else analysis.findings[:40]
    for finding in shown:
        print(f"  {finding}")
    if len(analysis.findings) > len(shown):
        print(f"  ... {len(analysis.findings) - len(shown)} more (pass --all)")
    if not analysis.files_scanned:
        return 1
    return 0 if verdict["clean"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

---

## 8. `backend/scripts/check_migration_tail.py`

- 角色：gate；行 201；位元組 7726；SHA256（原檔）`387bc4c6606d8925acb55b1932755f363af2c9edaf70220c38a7b16488cfdc9d`；git **未追蹤**；遮罩 0 處。

```
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
* ``2`` - the tree is branched (no single head exists);
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

# The last four revisions, oldest to newest, as of 2026-11-16 (EXECUTION_RECORD
# round 14, and the 54-revision chain §9 of the approval package describes).
# Extending the tree means editing this line deliberately, in the same change
# that adds the migration - which is the review signal the derived value
# suppressed.
EXPECTED_TAIL = (
    "c9d0e1f2a3b4",
    "d0e1f2a3b4c5",
    "e1f2a3b4c5d6",
    "f2a3b4c5d6e7",
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
    heads = heads_of(tree)
    report["parsed_heads"] = heads
    if len(heads) != 1:
        return {"status": "BRANCHED_TREE", "detail": (
            f"{len(heads)} heads ({', '.join(heads)}) - a branched tree has no "
            "single 'head' to upgrade to, so the schema is undefined"
        ), **report}

    head = heads[0]
    report["parsed_head"] = head
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
        "NO_ALEMBIC": 4,
    }.get(report["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
```

---

## 9. `run_r2.sh`

- 角色：stack；行 742；位元組 33391；SHA256（原檔）`6ee8bfbe163ece518a08979121023e723cd463285049377324a46b094480be16`；git **未追蹤**；遮罩 4 處。
- 遮罩位置（原檔行號／形狀／鍵名）：391（key-literal，password）、396（key-literal，password）、489（key-literal，password）、493（key-literal，password）

```
#!/bin/bash
# R2 — PostgreSQL / restart / account-isolation close-loop for the research-run lane.
#
# Authored 2026-11-15. Only `guard` has been executed (2026-11-16, exit 0,
# $ROOT/.r2/evidence/guard.log): it installs nothing, starts nothing and makes
# no connection. Every stage that writes (install, provision, migrate, tests,
# api-loop) still needs its own approval.
#
# What the 2026-11-16 guard run changed about this package: the pgserver
# binaries are already on disk ($REPO/.local/pgvenv, PostgreSQL 16.2), so the
# install stage resolves them instead of pip-installing, and no install
# approval is needed. The cluster those binaries also served in 2026-11-08
# ($REPO/.local/pgdata-16, which holds ai_investment_lab) is not reused: the
# provision stage refuses any PGDATA under a .local/pgdata path.
#
# Preservation rules baked in (owner instruction, 2026-11-15):
#   * nothing here deletes PGDATA or the evidence directory, ever;
#   * no retry, no recreate-on-failure, no re-run loop: the first failure is the result;
#   * shutdown is `pg_ctl stop -m fast`, never `-m immediate`;
#   * no GRANT beyond this package's own role in this package's own cluster, and no
#     signal is sent to a process whose PID this script did not record;
#   * cleanup of PGDATA / evidence / role is a separate package.
#
# The target is not free. backend/test_safety_gate.py whitelists database names
# (:25-30), blacklists production names (:33-38), accepts only port 5432 on loopback
# (:41-45) and refuses superuser/CREATEDB/CREATEROLE/BYPASSRLS roles (:125-145), while
# are alethra_test and alethra_owner_test. The prefix rule is not one line: the pg lane
# exports ALETHRA_TEST_REQUIRED_DB_PREFIX=alethra_ (scripts/run_tests.sh:130), hands it to
# check_db_env.analyse (:111, :160) which rejects a name without it (scripts/check_db_env.py:116-119).
# `guard` proves all of that before anything installs.
set -euo pipefail

# Derived from where this script actually sits, because the file now lives in
# the repo: a hardcoded home directory made every stage describe a machine
# nobody else is on. REPO is the script's own directory, ROOT its parent, so
# the working set stays outside the checkout exactly as §21.5(a) requires.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$REPO")"
BACKEND="$REPO/backend"
[ -f "$BACKEND/app/main.py" ] || { echo "FAIL: $REPO has no backend/app/main.py; run this script from the alethra repo root" >&2; exit 1; }
BACKEND_PY="$BACKEND/.venv/bin/python"          # app / alembic / uvicorn; this package never installs into it
PKG="$ROOT/.r2-package"                          # this script plus the values it measured
WORK="$ROOT/.r2"                                 # runtime working set; repo-external, created here
PGDATA="$WORK/pgdata"
EVIDENCE="$WORK/evidence"
VENV="$WORK/venv"                                # pgserver's own interpreter

PG_BOOTSTRAP_USER="r2_bootstrap"                 # initdb superuser: never used to connect
PG_HOST="127.0.0.1"
PG_PORT="5432"                                   # ALLOWED_INSTANCES accepts nothing else
PG_DB="alethra_test"                             # whitelist ∩ `alethra_` prefix
PG_ROLE="alethra_r2"                             # LOGIN, non-superuser: what the gate accepts
API_HOST="127.0.0.1"
API_PORT="8010"
PGSERVER_PIN="${R2_PGSERVER_PIN:-}"              # empty = version UNKNOWN until resolved; never guessed

RUN_URL="postgresql://$PG_ROLE@$PG_HOST:$PG_PORT/$PG_DB"
BOOT_URL="postgresql://$PG_BOOTSTRAP_USER@$PG_HOST:$PG_PORT/postgres"
PGBIN_FILE="$PKG/pgbin"

stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
say() { printf '%s %s\n' "$(stamp)" "$1"; }
fail() { say "FAIL: $1" >&2; say "first evidence retained: $EVIDENCE" >&2; exit 1; }

require_absent() {
  # Refuse to write over anything that already exists.
  if [ -e "$1" ]; then fail "$1 already exists; this package will not overwrite or reuse it"; fi
}

record_pipbin() {
  if [ ! -f "$PGBIN_FILE" ]; then
    fail "no resolved postgres bin dir ($PGBIN_FILE): run the install stage first"
  fi
  PGBIN="$(cat "$PGBIN_FILE")"
  if [ ! -x "$PGBIN/initdb" ] || [ ! -x "$PGBIN/pg_ctl" ] || [ ! -x "$PGBIN/psql" ]; then
    fail "resolved bin dir has no initdb/pg_ctl/psql: $PGBIN"
  fi
}

# pgserver's PostgreSQL binaries may already be on disk: an earlier lane
# installed the wheel into $REPO/.local/pgvenv and its cluster data went to
# $REPO/.local/pgdata-16 (see ENVIRONMENT_VERIFICATION_REPORT.md, 2026-11-07).
# Reusing the *binaries* is read-only and needs no network, no new interpreter
# and no approval to install. Reusing that *data directory* is a different
# thing entirely and never happens here: stage_provision refuses it.
ON_DISK_PGBIN_SEARCH="$REPO/.local/pgvenv $ROOT/.local/pgvenv $VENV $BACKEND/.venv"

resolve_existing_initdb() {
  local dir found
  for dir in $ON_DISK_PGBIN_SEARCH; do
    [ -d "$dir" ] || continue
    found="$(find "$dir" -type f -name initdb -path '*/bin/*' 2>/dev/null | head -1 || true)"
    if [ -n "$found" ] && [ -x "$found" ]; then
      printf '%s\n' "$found"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------- guard ----
stage_guard() {
  # Read-only: proves the intended target is admissible before anything is installed,
  # and prints the environment that would otherwise surprise us later. No connection,
  # no install, no start.
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/guard.log"; : > "$OUT"
  {
    say "repo=$REPO"
    say "backend interpreter: $BACKEND_PY"
    say "postgres on PATH: $(command -v postgres || echo none)"
    say "postgres processes: $(pgrep -f '[p]ostgres' | wc -l | tr -d ' ')"
    say "port $PG_PORT listeners: $(lsof -nP -iTCP:$PG_PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
    say "port $API_PORT listeners: $(lsof -nP -iTCP:$API_PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
    say "PGDATA present: $([ -e "$PGDATA" ] && echo yes || echo no)"
    say "pgserver pin: ${PGSERVER_PIN:-UNKNOWN}"
    # Whether an install is needed at all is decided by what is already on disk,
    # so the guard prints that before anyone approves an install stage.
    say "initdb already on disk: $(resolve_existing_initdb || echo none)"
    say "2026-11-08 cluster data dir (this package never initialises in it): $([ -e "$REPO/.local/pgdata-16" ] && echo present || echo absent)"
  } 2>&1 | tee -a "$OUT"
  if [ ! -x "$BACKEND_PY" ]; then fail "backend/.venv interpreter missing"; fi
  if [ "$(lsof -nP -iTCP:$PG_PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')" != "0" ]; then
    fail "something already listens on 127.0.0.1:$PG_PORT — this package does not take over a foreign cluster"
  fi

  # The runner's own coherence check, dry: the four DB variables it derives from one URL.
  ( cd "$BACKEND" && "$BACKEND_PY" - "$RUN_URL" <<'R2PY' 2>&1 | tee -a "$OUT"
import sys
from scripts.check_db_env import analyse
url = sys.argv[1]
env = {n: url for n in ("DATABASE_URL", "ALETHRA_TEST_DB",
                       "ALETHRA_PG_DATABASE_URL", "ALETHRA_TEST_POSTGRES_URL")}
a = analyse(env, required_prefix="alethra_")
print("check_db_env.analyse ->", a.mode, "|", a.summary, "| errors:", a.errors or "none")
sys.exit(1 if a.errors else 0)
R2PY
  ) || fail "the intended target is not a coherent PG test environment"

  # The static half of the safety gate, applied to the URL this package will use.
  ( cd "$BACKEND" && "$BACKEND_PY" - "$RUN_URL" <<'R2PY' 2>&1 | tee -a "$OUT"
import sys
from urllib.parse import urlparse
from test_safety_gate import (ALLOWED_INSTANCES, ALLOWED_TEST_DATABASES,
                              FORBIDDEN_DATABASES)
parsed = urlparse(sys.argv[1])
db = parsed.path.lstrip("/")
print("whitelist        ", sorted(ALLOWED_TEST_DATABASES))
print("blacklist        ", sorted(FORBIDDEN_DATABASES))
print("allowed instances", sorted(ALLOWED_INSTANCES))
print("target           ", f"{parsed.hostname}:{parsed.port}/{db} as {parsed.username}")
assert db in ALLOWED_TEST_DATABASES, "database not whitelisted"
assert db not in FORBIDDEN_DATABASES, "database forbidden"
assert (parsed.hostname, parsed.port) in ALLOWED_INSTANCES, "host/port not allowed"
print("static gate: PASS")
R2PY
  ) || fail "target rejected by the static part of test_safety_gate"

  # The .env hazard: app.config reads env_file=".env" (config.py:430) relative to the
  # backend directory, and that file currently names a blacklisted database and turns on
  # live trading. Command-line variables win — measured 2026-11-15, not assumed.
  ( cd "$BACKEND" && "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
from app.config import get_settings
s = get_settings()
print(".effective settings with .env in force: database=%s provider=%s live_trading=%s "
      "learning_scheduler=%s auto_collection=%s auth_bypass=%s"
      % (s.database_url.split("@")[-1], s.data_provider, s.live_trading_enabled,
         s.enable_learning_scheduler, s.auto_collection_enabled,
         getattr(s, "research_auth_bypass_enabled", "n/a")))
R2PY
  )
  say "guard stage complete: no install, no start, no connection made. See $OUT"
}

# -------------------------------------------------------------- install ----
stage_install() {
  mkdir -p "$EVIDENCE" "$PKG"
  OUT="$EVIDENCE/install.log"; : > "$OUT"
  found="$(resolve_existing_initdb || true)"
  if [ -n "$found" ]; then
    say "resolved PostgreSQL binaries already on disk — no install, no venv, no network"
    say "  initdb: $found" | tee -a "$OUT"
    "$found" --version 2>&1 | tee -a "$OUT" || fail "the resolved initdb would not report a version"
  else
    # Nothing on disk: this is the only branch that may touch the network, and
    # it needs the approved pin. An empty pin stays an honest UNKNOWN.
    if [ -z "$PGSERVER_PIN" ]; then
      fail "no pgserver binaries on disk and R2_PGSERVER_PIN is empty: the version is UNKNOWN. Resolve it and approve that exact pin before any install."
    fi
    require_absent "$VENV"
    "$BACKEND_PY" -m venv "$VENV" 2>&1 | tee -a "$OUT" || fail "venv creation failed (backend/.venv untouched)"
    "$VENV/bin/python" -m pip install --disable-pip-version-check "pgserver==$PGSERVER_PIN" 2>&1 | tee -a "$OUT" \
      || fail "pip install pgserver==$PGSERVER_PIN failed"
    {
      "$VENV/bin/python" -m pip freeze 2>&1 | grep -i -E "pgserver|psycopg" || true
      "$VENV/bin/python" -c "import inspect, os, pgserver; print('module', os.path.dirname(inspect.getfile(pgserver)))"
    } >>"$OUT" 2>&1
    found="$(find "$VENV" -type f -name initdb -path '*/bin/*' 2>/dev/null | head -1 || true)"
    if [ -z "$found" ]; then
      say "initdb not found inside the wheel; the installed layout is recorded in $OUT"
      fail "postgres bin dir UNKNOWN — do not proceed on a guess"
    fi
    "$found" --version 2>&1 | tee -a "$OUT"
  fi
  dirname "$found" > "$PGBIN_FILE"
  record_pipbin
  say "resolved PGBIN=$(cat "$PGBIN_FILE")"
}

# ------------------------------------------------------------ provision ----
stage_provision() {
  record_pipbin
  # The binaries may be shared with the 2026-11-08 cluster; the data directory
  # may not. That cluster holds ai_investment_lab, which this package is
  # forbidden to touch, so any PGDATA under a .local/pgdata path is refused
  # before require_absent could even be asked about it.
  case "$PGDATA" in
    */.local/pgdata*) fail "PGDATA=$PGDATA is inside a .local/pgdata path — that is the 2026-11-08 cluster's location and this package never initialises over it" ;;
  esac
  require_absent "$PGDATA"
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/provision.log"; : > "$OUT"
  # The bootstrap superuser owns the cluster; the application never connects as it
  # (the gate refuses superuser connections, test_safety_gate.py:125-130).
  "$PGBIN/initdb" -D "$PGDATA" -U "$PG_BOOTSTRAP_USER" --auth=trust --no-sync 2>&1 | tee -a "$OUT" \
    || fail "initdb failed; PGDATA left in place for inspection"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$EVIDENCE/postgres.log" \
    -o "-p $PG_PORT -c listen_addresses=$PG_HOST -c unix_socket_directories=/tmp" \
    start 2>&1 | tee -a "$OUT" || fail "pg_ctl start failed; see $EVIDENCE/postgres.log"
  say "postmaster pid $(head -1 "$PGDATA/postmaster.pid") datadir $(sed -n 4p "$PGDATA/postmaster.pid")"
  "$PGBIN/psql" "$BOOT_URL" -v ON_ERROR_STOP=1 2>&1 <<SQL | tee -a "$OUT" \
    || fail "role/database creation failed (no retry, no drop)"
CREATE ROLE $PG_ROLE LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE $PG_DB OWNER $PG_ROLE;
SQL
  "$PGBIN/psql" "$BOOT_URL" -v ON_ERROR_STOP=1 -d "$PG_DB" \
    -c "GRANT ALL ON SCHEMA public TO $PG_ROLE;" 2>&1 | tee -a "$OUT" \
    || fail "schema grant failed"
  # Loopback-only, and the auth method is recorded rather than assumed.
  "$PGBIN/psql" "$BOOT_URL" -d "$PG_DB" -Atx \
    -c "SELECT current_database(), current_user, rolsuper FROM pg_roles WHERE rolname=current_user" \
    2>&1 | tee -a "$OUT"
  HBAF="$("$PGBIN/psql" "$BOOT_URL" -tAc 'SHOW hba_file')"
  grep -v '^[[:space:]]*#' "$HBAF" | grep -v '^[[:space:]]*$' | tee -a "$OUT"
  say "provisioned $PG_DB owned by $PG_ROLE (trust on loopback: no password exists)"
}

# ----------------------------------------------------- live gate check ----
stage_gate() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/gate_live.log"; : > "$OUT"
  ( cd "$BACKEND" && TEST_MODE=true DATABASE_URL="$RUN_URL" "$BACKEND_PY" -c \
      'from test_safety_gate import verify_test_database; print(verify_test_database())' ) 2>&1 | tee -a "$OUT" \
    || fail "verify_test_database() rejected the running cluster — stop here, do not migrate"
  say "the gate accepts this target; migration is a separate approval"
}

# ------------------------------------------------------------- migrate ----
stage_migrate() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/migrate.log"; : > "$OUT"
  # First structural write, against alethra_test only, using the existing revisions
  # (backend/migrations, 52 files measured; no new revision authored here).
  ( cd "$BACKEND" && PYTHONDONTWRITEBYTECODE=1 DATABASE_URL="$RUN_URL" \
      "$BACKEND_PY" -m alembic upgrade head ) 2>&1 | tee -a "$OUT" \
    || fail "alembic upgrade head failed; PGDATA and $OUT retained (no recreate, no retry)"
  ( cd "$BACKEND" && PYTHONDONTWRITEBYTECODE=1 DATABASE_URL="$RUN_URL" \
      "$BACKEND_PY" -m alembic current ) 2>&1 | tee -a "$OUT"
}

# --------------------------------------------------------------- tests ----
stage_tests() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/tests.log"; : > "$OUT"
  # run_tests.sh pg derives all four DB variables from ALETHRA_PG_TEST_URL (:85-90) and
  # runs the safety gate (:98-111) plus the connection probe (:112-129) before pytest.
  # Files 1-2 are the research lane's own judgment; file 3 is the paper-trading
  # isolation suite and is not research-lane evidence — the two must not be merged.
  ( cd "$REPO" && DATA_PROVIDER=demo ALETHRA_PYTHON="$BACKEND_PY" ALETHRA_PG_TEST_URL="$RUN_URL" \
      ./backend/scripts/run_tests.sh pg \
        tests/test_research_run_recovery.py \
        tests/test_two_phase_quota.py \
        tests/test_ownership_integration.py \
        -p no:warnings -q -rf ) 2>&1 | tee -a "$OUT" \
    || fail "PG layer failed; first trace is in $OUT (no re-run, no test edits to go green)"
}

# ------------------------------------------------------------ api loop ----
# Every variable is explicit because backend/.env carries DATA_PROVIDER=twelvedata,
# LIVE_TRADING_ENABLED=true and a DATABASE_URL naming the blacklisted ai_investment_lab.
# Nothing external is reachable from these stages: the demo provider is in-process
# synthetic data, schedulers stay off, payments/notifications/live trading off, and both
# test-only auth conveniences off, so the two accounts carry real JWTs.
API_ENV=(
  PYTHONDONTWRITEBYTECODE=1
  DATABASE_URL="$RUN_URL"
  DATA_PROVIDER=demo
  ENABLE_LEARNING_SCHEDULER=false
  AUTO_COLLECTION_ENABLED=false
  ENABLE_LIVE_PAYMENTS=false
  ENABLE_EXTERNAL_NOTIFICATIONS=false
  LIVE_TRADING_ENABLED=false
  RESEARCH_AUTH_BYPASS_ENABLED=false
  CANDIDATE_PASSWORDLESS_MODE=false
  RESEARCH_RUN_RECOVERY_ENABLED=true
  # The window is a production setting (config.py:125 default 900). Lowering it to 5s
  # here is what makes the post-kill scan observable in seconds instead of a quarter of
  # an hour; it is configuration, not a code change, and it is printed with the result.
  RESEARCH_RUN_STALE_AFTER_SECONDS=5
)

api_start() {
  if [ -f "$PKG/uvicorn.pid" ] && ps -p "$(cat "$PKG/uvicorn.pid")" >/dev/null 2>&1; then
    say "uvicorn already running as pid $(cat "$PKG/uvicorn.pid")"
    return 0
  fi
  ( cd "$BACKEND" && env "${API_ENV[@]}" "$BACKEND_PY" -m uvicorn app.main:app \
      --host "$API_HOST" --port "$API_PORT" --log-level info \
      >>"$EVIDENCE/uvicorn.log" 2>&1 & echo $! > "$PKG/uvicorn.pid" )
  say "uvicorn pid $(cat "$PKG/uvicorn.pid") (the only pid this package may signal)"
}

api_stop_recorded() {
  if [ -f "$PKG/uvicorn.pid" ]; then
    pid="$(cat "$PKG/uvicorn.pid")"
    if ps -p "$pid" -o command= 2>/dev/null | grep -q "uvicorn app.main:app"; then
      kill -TERM "$pid" || true
      say "stopped the API process this script started (pid $pid)"
    else
      say "pid $pid is not this package's uvicorn — left alone"
    fi
    rm -f "$PKG/uvicorn.pid"
  fi
}

stage_timeprobe() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/timeprobe.log"; : > "$OUT"
  if [ ! -f "$PKG/uvicorn.pid" ]; then api_start; fi
  RUN_URL="$RUN_URL" API_BASE="http://$API_HOST:$API_PORT" \
    "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
# Feasibility gate for a real SIGKILL: is a run row observable in `running` from a
# second connection at all? Demo data is 3 assets x 30 trade dates, so the honest
# answer has to be measured rather than assumed.
import json
import os
import time
import urllib.request

import psycopg2

from app.data.demo_data import TRADE_DATES

BASE = os.environ["API_BASE"]
RUN_URL = os.environ["RUN_URL"]
SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": ["DEMO-ALPHA"],
    "frequency": "1d",
    "start_date": str(min(TRADE_DATES)),
    "end_date": str(max(TRADE_DATES)),
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}


def call(method, path, body=None, token=None, boot_wait=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    for _ in range(200 if boot_wait else 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("API never answered")


boot = time.perf_counter()
status, _ = call("GET", "/", boot_wait=True)
print("api reachable in %.3fs (%s)" % (time.perf_counter() - boot, status))

call("POST", "/api/auth/register", {
    "risk_acknowledged": True, "email": "r2-timeprobe@alethra.test",
    "password""‹REDACTED›", "display_name": "R2 timeprobe",
    "user_type": "individual",
}, boot_wait=True)
status, body = call("POST", "/api/auth/login",
                    {"email": "r2-timeprobe@alethra.test",
                     "password""‹REDACTED›"})
assert status == 200, body
token_a = json.loads(body)["access_token"]

status, body = call("POST", "/api/v1/experiments/", {
    "title": "R2 timeprobe", "hypothesis": "Is a run observable mid-flight?",
    "strategy_spec": SPEC,
}, token=token_a)
assert status == 201, body
version_id = json.loads(body)["current_version_id"]

durations, polls = [], []
for i in range(3):
    conn = psycopg2.connect(RUN_URL)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    start = time.perf_counter()
    status, body = call("POST", "/api/v1/versions/%s/runs?data_source=provider&provider_id=demo"
                        % version_id, None, token_a)
    elapsed = time.perf_counter() - start
    assert status == 201, "%s %s" % (status, body[:400])
    run_id = json.loads(body)["id"]
    observed = 0
    final = None
    while True:
        cur.execute("SELECT status FROM research_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if row is None or row[0] in ("completed", "failed"):
            final = row[0] if row else "missing"
            break
        observed += 1
        time.sleep(0.001)
    conn.close()
    durations.append(elapsed)
    polls.append(observed)
    print("run %d: request=%.4fs final=%s poll-saw-running=%d"
          % (i, elapsed, final, observed))

median = sorted(durations)[len(durations) // 2]
print("median request %.4fs; runs observed mid-flight: %d/%d"
      % (median, sum(1 for p in polls if p > 0), len(polls)))
observable = all(p > 0 for p in polls)
print("verdict:", "KILL_WINDOW_OBSERVABLE" if observable else
      "INSUFFICIENT_EVIDENCE: no in-flight window on this workload; the real-kill layer "
      "needs B1 (dataset import through the formal route) or B3 (worker/heartbeat), "
      "not a hand-seeded row")
raise SystemExit(0 if observable else 3)
R2PY
}

stage_api_loop() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/api_loop.log"; : > "$OUT"
  if [ ! -f "$PKG/uvicorn.pid" ]; then api_start; fi
  RUN_URL="$RUN_URL" API_BASE="http://$API_HOST:$API_PORT" \
  PIDFILE="$PKG/uvicorn.pid" KILLED_FILE="$PKG/killed_run_id" \
    "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
# Run this only after timeprobe exited 0; it also refuses to kill when the row is never
# observed running. The pid it signals is the one api_start recorded.
import json
import os
import signal
import threading
import time
import urllib.request

import psycopg2

from app.data.demo_data import TRADE_DATES

BASE = os.environ["API_BASE"]
RUN_URL = os.environ["RUN_URL"]
PID = int(open(os.environ["PIDFILE"]).read().strip())
KILLED_FILE = os.environ["KILLED_FILE"]


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except Exception as exc:
        return 0, type(exc).__name__


def register(email, name):
    call("POST", "/api/auth/register", {
        "risk_acknowledged": True, "email": email, "password""‹REDACTED›",
        "display_name": name, "user_type": "individual",
    })
    status, body = call("POST", "/api/auth/login",
                        {"email": email, "password""‹REDACTED›"})
    assert status == 200, body
    return json.loads(body)["access_token"]


token_a = register("r2-owner@alethra.test", "Owner A")
token_b = register("r2-other@alethra.test", "Other B")

spec = {
    "template_type": "buy_and_hold", "asset_ids": ["DEMO-ALPHA"], "frequency": "1d",
    "start_date": str(min(TRADE_DATES)), "end_date": str(max(TRADE_DATES)),
    "initial_cash": 100000.0, "commission_rate": 0.001, "slippage_rate": 0.0,
}
status, body = call("POST", "/api/v1/experiments/", {
    "title": "R2 kill loop", "hypothesis": "Recovery after a real kill.",
    "strategy_spec": spec,
}, token=token_a)
assert status == 201, body
version_id = json.loads(body)["current_version_id"]
run_path = ("/api/v1/versions/%s/runs?data_source=provider&provider_id=demo" % version_id)

conn = psycopg2.connect(RUN_URL)
conn.set_session(autocommit=True)
cur = conn.cursor()

# Scoped to the owner of this experiment: an unscoped scalar subquery over
# quota_counters raises "more than one row returned" the moment a second account
# ever gets a counter, and an unscoped reservation sum would report B's holds as
# if they were A's.
cur.execute("""
    SELECT e.owner_id FROM experiment_versions v
      JOIN experiments e ON e.id = v.experiment_id
     WHERE v.id = %s
""", (version_id,))
owner_id = str(cur.fetchone()[0])

QUOTA_SQL = """
    SELECT COALESCE((SELECT used FROM quota_counters
                     WHERE quota_type='research_run' AND period='lifetime'
                       AND user_id = %s), 0),
           COALESCE((SELECT sum(amount) FROM quota_reservations
                     WHERE quota_type='research_run' AND status='reserved'
                       AND user_id = %s), 0),
           (SELECT count(*) FROM research_runs WHERE experiment_version_id = %s),
           (SELECT count(*) FROM audit_events
             WHERE entity_type='run' AND event_type='research_run_failed'
               AND actor_id = %s)
"""


def quota():
    cur.execute(QUOTA_SQL, (owner_id, owner_id, version_id, owner_id))
    used, reserved, rows, failed_events = cur.fetchone()
    return dict(used=used, reserved=reserved or 0, run_rows=rows,
                failed_events=failed_events)


print("before:", quota())

# A finished run first, so there is a real run id for the cross-account read to target.
status, body = call("POST", run_path, None, token_a)
assert status == 201, body
baseline_run_id = json.loads(body)["id"]
print("baseline run (A, completed):", baseline_run_id)
print("after baseline:", quota())

# Isolation over HTTP on the real routes, with the codes the code actually produces:
# submitting to another account's version is refused by the ownership check
# (api/runs.py:73) with 403, and reading another account's run is answered 404
# (api/runs.py:705-706). 403 and 404 are different answers; this stage records both.
status_read, _ = call("GET", "/api/v1/runs/%s" % baseline_run_id, None, token_b)
status_submit, body_submit = call("POST", run_path, None, token_b)
print("B GET on A's run ->", status_read, "| B POST on A's version ->", status_submit,
      "| A's run id leaked:", baseline_run_id in body_submit)
assert status_read == 404, "a cross-account read must not answer 200"
assert status_submit == 403, body_submit[:300]

# Kill while a row is observably running. The request runs in its own thread so the
# polling connection is not blocked behind its own statement.
outcome = {}


def submit():
    outcome["result"] = call("POST", run_path, None, token_a)


thread = threading.Thread(target=submit)
thread.start()
killed = None
deadline = time.perf_counter() + 30
while time.perf_counter() < deadline and thread.is_alive():
    cur.execute("""
        SELECT id, status FROM research_runs
         WHERE experiment_version_id = %s AND status IN ('pending','running')
         ORDER BY started_at DESC NULLS LAST LIMIT 1
    """, (version_id,))
    row = cur.fetchone()
    if row:
        os.kill(PID, signal.SIGKILL)
        killed = str(row[0])
        open(KILLED_FILE, "w").write(killed)
        print("SIGKILL -> pid %s while run %s was %s" % (PID, killed, row[1]))
        break
    time.sleep(0.001)
if killed is None:
    thread.join(timeout=60)
    print("verdict: INSUFFICIENT_EVIDENCE - never observed a running row; nothing killed")
    print("request outcome:", outcome.get("result", ("no response", "")))
    raise SystemExit(3)
thread.join(timeout=60)
print("request ended with:", outcome.get("result", ("connection dropped by the kill", "")))
print("after kill (API down):", quota())
conn.close()
R2PY
  say "killed run id recorded in $PKG/killed_run_id; next approved step: $0 restart-verify"
}

stage_restart_verify() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/restart_verify.log"; : > "$OUT"
  if [ ! -f "$PKG/killed_run_id" ]; then fail "no $PKG/killed_run_id: run api-loop first"; fi
  STALE="$("$BACKEND_PY" -c 'import sys; sys.path.insert(0, "'"$BACKEND"'"); from app.config import get_settings; print(get_settings().research_run_stale_after_seconds)' 2>/dev/null || echo UNKNOWN)"
  say "relaunching the API so the startup scan (main.py:78-108) runs; stale window ${STALE}s"
  rm -f "$PKG/uvicorn.pid"
  api_start
  RUN_URL="$RUN_URL" API_BASE="http://$API_HOST:$API_PORT" \
  KILLED="$(cat "$PKG/killed_run_id")" STALE="$STALE" \
    "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
import json
import os
import time
import urllib.request

import psycopg2

BASE = os.environ["API_BASE"]
RUN_URL = os.environ["RUN_URL"]
KILLED = os.environ["KILLED"]
STALE = os.environ["STALE"]


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    for _ in range(200):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("API never answered")


status, _ = call("GET", "/")
print("api up:", status)

conn = psycopg2.connect(RUN_URL)
conn.set_session(autocommit=True)
cur = conn.cursor()
# The startup scan only closes rows older than the stale window, so a freshly killed
# row is still `running` here. Both readings are printed: immediately after restart,
# and after the POST that has to wait out the window (or a second restart once it is
# past it). Nothing is asserted that the scan did not do.
# Every count is scoped to the owner of the killed row: counting across all accounts
# would let account B's open reservation answer for account A's, and an unscoped
# counter read can match several rows and silently report one of them.
cur.execute("""
    SELECT r.status, r.started_at, r.completed_at, r.error_details,
           e.owner_id,
           (SELECT count(*) FROM quota_reservations q
             WHERE q.quota_type='research_run' AND q.status='reserved'
               AND q.user_id = e.owner_id),
           (SELECT count(*) FROM audit_events a
             WHERE a.entity_type='run' AND a.entity_id = r.id::text)
      FROM research_runs r
      JOIN experiment_versions v ON v.id = r.experiment_version_id
      JOIN experiments e ON e.id = v.experiment_id
     WHERE r.id = %s
""", (KILLED,))
row = cur.fetchone()
if row is None:
    raise SystemExit("killed run %s is not in research_runs: the write never committed")
cur.execute("""
    SELECT used FROM quota_counters
     WHERE quota_type='research_run' AND period='lifetime' AND user_id = %s
""", (str(row[4]),))
counters = cur.fetchall()
cur.execute("""
    SELECT count(*) FROM audit_events
     WHERE entity_type='run' AND event_type='research_run_failed'
""")
failed_events = cur.fetchone()[0]
print(json.dumps(dict(
    killed_run=KILLED, status=row[0], started_at=str(row[1]),
    completed_at=str(row[2]), error_head=(row[3] or "")[:60],
    owner=str(row[4]), open_reservations_same_owner=row[5],
    audit_events_for_this_run=row[6],
    owner_lifetime_used=counters[0][0] if len(counters) == 1 else "ROWS=%d" % len(counters),
    research_run_failed_events_all_accounts=failed_events,
    stale_after_seconds=STALE,
), ensure_ascii=False, indent=2))
conn.close()
R2PY
  say "acceptance is the printed row; the terminal state appears once the row is past"
  say "${STALE}s and a scan runs (startup, or the next POST to that version)"
}

# ---------------------------------------------------------------- stop ----
stage_stop() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/stop.log"; : > "$OUT"
  api_stop_recorded
  if [ -f "$PGDATA/postmaster.pid" ]; then
    record_pipbin
    "$PGBIN/pg_ctl" -D "$PGDATA" stop -m fast 2>&1 | tee -a "$OUT" \
      || fail "pg_ctl stop -m fast failed (never -m immediate; no signal to foreign backends)"
  fi
  say "PGDATA and evidence retained: $PGDATA / $EVIDENCE — cleanup is a separate package"
}

stage_status() {
  printf 'working set   %s\n' "$WORK"
  printf 'pgdata        %s (%s)\n' "$PGDATA" "$([ -d "$PGDATA" ] && echo exists || echo absent)"
  printf 'evidence      %s\n' "$(ls -1 "$EVIDENCE" 2>/dev/null | tr '\n' ' ')"
  printf 'resolved bins %s\n' "$(cat "$PGBIN_FILE" 2>/dev/null || echo 'UNKNOWN (install not run)')"
  printf 'uvicorn pid   %s\n' "$(cat "$PKG/uvicorn.pid" 2>/dev/null || echo 'not started')"
  printf 'postmaster    %s\n' "$(head -1 "$PGDATA/postmaster.pid" 2>/dev/null || echo 'not running')"
}

case "${1:-}" in
  guard)            stage_guard ;;
  install)          stage_install ;;
  provision)        stage_provision ;;
  gate)             stage_gate ;;
  migrate)          stage_migrate ;;
  tests)            stage_tests ;;
  timeprobe)        stage_timeprobe ;;
  api-loop)         stage_api_loop ;;
  restart-verify)   stage_restart_verify ;;
  stop)             stage_stop ;;
  status)           stage_status ;;
  *) echo "usage: $0 {guard|install|provision|gate|migrate|tests|timeprobe|api-loop|restart-verify|stop|status}" >&2
     echo "only the read-only guard stage has ever run (2026-11-16); every writing stage still needs its own approval." >&2
     exit 2 ;;
esac
```

---

## 機密處理

- 產生器對九支檔一律做**規則式**遮罩，不列已知值清單：鍵名含 `PASSWORD`／`PASSWD`／`SECRET`／`TOKEN`／`API_KEY`／`ACCESS_KEY`／`PRIVATE_KEY` 的字面值，
  以及連線字串中 `user:口令@host` 這種內嵌形狀，都換成 `‹REDACTED›`。明天新增一條同形狀的字面值會被同一條規則遮掉，不需要有人先發現再把規則放寬。
- 本檔產生的實測遮罩總數：**7 處**（4 key-literal、3 url），逐檔與逐行位置見上方索引與各節標頭。
- 規則是**刻意過遮**（over-mask）的：它只看形狀，不判斷該處是秘密還是說明文字裡的範例——「哪個才算真秘密」這個判斷正是上一版缺陷的來源，所以這裡不放判斷。本檔實測 7 處之中，`run_r2.sh` 的 4 處是腳本裡帶引號的口令字面值；`check_env_hygiene.py` 的 3 處落在該檔說明字串對 URL 內嵌口令形狀的舉例上（`user:` 之後、`@` 之前那一段被換掉），該檔的正則式定義本身未被遮，仍可逐字審。
- 遮罩標記在本檔出現的次數 = 遮罩處數 + 1（多的那一次來自本節開頭那條規則說明把標記本身寫了出來）。這個等式由產生器在寫檔前用程式核對，不符就拒絕寫入；所以拿 `grep -c` 比對的讀者不會看到一個無法解釋的數字。
- SHA256 全部量自磁碟原檔，所以「摘要對應的是未遮罩的內容」；要做逐位元組比對時，
  比對物件是原檔，不是本附件的圍欄內容。
- 值本身、值的骨架、值的指紋（含低熵值的前綴）一律不寫進本檔。
- 產生器在寫檔**之前**以同一組規則重掃渲染結果，只要還有未遮罩的憑證形狀就拒絕寫入
  （RC=1，不留下半成品）；本檔能存在，代表那次重掃為零。
- 交付後仍以 `python3 backend/scripts/check_env_hygiene.py --pre-commit` 復掃本檔；
  該 scope 含未追蹤檔，所以這個新檔案一定在掃描範圍內。

