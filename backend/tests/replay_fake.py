"""Query-dispatching stand-in used by the mocked replay unit tests.

Replaces the call-number-indexed fakes these tests grew up with. Two properties
matter here:

* Answers are selected by SQL content, so a query added or reordered in
  ``execute_historical_replay`` cannot silently shift an assertion onto a
  different statement (that is how the previous fakes stopped testing the
  trades query while still reporting green input data).
* An unrecognised ``SELECT`` raises instead of returning a ``MagicMock``: a new
  read on the replay path has to be declared by the test that depends on it.
  Writes (INSERT/UPDATE/DELETE) stay no-ops, because these tests assert on the
  replay's in-memory result, not on stored rows.
* The session double is specced against ``sqlalchemy.orm.Session``, so an
  attribute the real session does not have cannot be invented by a test, and
  every ORM entry point (``query``/``get``/``scalars``/``add``/``flush`` …)
  refuses: the replay is a raw-statement path, and an ORM read would answer
  itself with another mock instead of being declared.

The column list below is cross-checked against the SELECT the service actually
issues, in ``test_the_expected_columns_are_the_ones_the_formal_query_selects``,
so the fake's expectations cannot drift from the formal query contract. The
same node checks the poll row's arity, because the two rows the replay reads
are not shaped alike: the poll selects two columns and unpacks them
positionally, while the terminal-status read selects three.
"""

from collections import namedtuple
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session

RUN_LOOKUP = "SELECT * FROM paper_trading_runs WHERE id"
CANCEL_POLL = "SELECT cancel_requested, status"
PORTFOLIO_OWNER = "SELECT owner_id FROM paper_portfolios"
TRADES_QUERY = "FROM strategy_study_trades"
ACQUIRE_EXECUTION = "SET status = 'running', updated_at = CURRENT_TIMESTAMP"
FINAL_STATUS_LOCK = "FOR UPDATE"
TERMINAL_UPDATE = "RETURNING id, status, cancel_requested"
TERMINAL_COMPLETION = "SET status = 'completed'"
# Both worker-side cancel writes (the in-loop stop and the post-lock stop)
# spell the assignment the same way, so one marker covers both sites. The
# service reads rowcount on both; a test that wants only one of them to match
# narrows the trade count instead of narrowing the marker.
CANCEL_WRITE = "SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP"

_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE")

# The replay touches the session through exactly these; the rest of the session
# surface is refused below rather than quietly answered by a mock.
_REFUSED_SESSION_METHODS = (
    "query", "get", "scalars", "scalar", "add", "add_all", "delete", "flush",
    "merge", "expunge", "expunge_all", "refresh", "bulk_save_objects",
    "bulk_insert_mappings", "bulk_update_mappings",
)


# SQLAlchemy Rows answer positional *and* attribute reads, and the replay path
# uses both shapes on the same rows: the trade row is unpacked column-by-column
# at app/services/paper_trading.py:377 and read as ``trade.direction`` at :283,
# while the terminal-status row is read as ``locked_run.cancel_requested`` at :885
# and as ``row[1], row[2]`` at :1030. Named tuples reproduce that dual access; a
# plain tuple fails the attribute read and a plain object fails the unpack.
TRADE_COLUMNS = (
    "source_trade_id",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "net_pnl",
    "direction",
    "quantity",
    "exit_reason",
    "source_run_id",
    "source_rule_id",
    "source_exit_id",
    "source_data_version",
)

TRADE_REQUIRED = ("entry_time", "exit_time", "entry_price", "exit_price",
                  "net_pnl", "direction", "quantity")

TradeRow = namedtuple("TradeRow", TRADE_COLUMNS)

StatusRow = namedtuple("StatusRow", ("id", "status", "cancel_requested"))

# The cancel poll selects two columns and unpacks them positionally
# (``cancel_requested, status = result`` at the top of check_cancel_requested),
# in the opposite order from the three-column terminal-status row above. Sharing
# one row shape between the two reads made the poll raise
# "too many values to unpack" for any test that actually drove it.
PollRow = namedtuple("PollRow", ("cancel_requested", "status"))

RUN_COLUMNS = ("id", "portfolio_id", "status", "skill", "instrument_id",
               "interval_minutes", "initial_capital")

RunRow = namedtuple("RunRow", RUN_COLUMNS)


def make_run_row(**overrides):
    """The paper_trading_runs row, listing every column the replay reads by name.

    A named tuple rather than a MagicMock: the service reads six attributes off
    this row (paper_trading.py:215-229 and the INSERT parameters that reuse it),
    and a Mock answers an attribute nobody declared with another Mock instead of
    failing. Here a newly-read column raises AttributeError in the test that
    depends on it.
    """
    row = {
        "id": str(uuid4()),
        "portfolio_id": str(uuid4()),
        "status": "queued",
        "skill": "momentum",
        "instrument_id": "BTC",
        "interval_minutes": 60,
        "initial_capital": Decimal("100000"),
    }
    unknown = [name for name in overrides if name not in RUN_COLUMNS]
    assert not unknown, f"unknown run column(s): {unknown}"
    row.update(overrides)
    return RunRow(**row)


def portfolio_owner_row(owner_id):
    """The paper_portfolios row the ownership check reads (paper_trading.py:213)."""
    return SimpleNamespace(owner_id=owner_id)


def trade_row(**fields):
    """One stored trade, in the column order the replay SELECT emits."""
    missing = [name for name in TRADE_REQUIRED if name not in fields]
    assert not missing, f"trade row is missing {missing}"
    unknown = [name for name in fields if name not in TRADE_COLUMNS]
    assert not unknown, f"unknown trade column(s): {unknown}"
    row = {
        "source_trade_id": str(uuid4()),
        "exit_reason": "exit",
        "source_run_id": None,
        "source_rule_id": None,
        "source_exit_id": None,
        "source_data_version": None,
    }
    row.update(fields)
    return TradeRow(**row)


def make_replay_db(
    *, run, owner_id, trades, execution_acquired=True, cancel_requested=False,
    completion_rowcount=1, cancel_rowcount=1,
):
    """A session double that answers the replay's reads by statement content.

    ``db.calls`` is the statement log; ``db.journal`` is the same log with the
    transaction boundaries interleaved, so a test can say which side of which
    boundary a statement fell — a refusal that rolls back and then writes a
    terminal state is a different claim from one that commits over the rollback.

    ``completion_rowcount`` and ``cancel_rowcount`` are the two conditional
    terminal writes' answers. Every other write reports one row, because the
    tests that do not care about ownership are not the ones that would notice a
    zero here.
    """
    calls = []
    journal = []

    def execute(statement, parameters=None):
        sql = " ".join(str(statement).split())
        params = dict(parameters or {})
        calls.append((sql, params))
        journal.append(("stmt", sql))
        result = MagicMock()
        result.rowcount = 0
        if RUN_LOOKUP in sql:
            result.fetchone.return_value = run
        elif CANCEL_POLL in sql:
            result.fetchone.return_value = PollRow(
                cancel_requested=cancel_requested, status="running"
            )
        elif PORTFOLIO_OWNER in sql:
            result.fetchone.return_value = portfolio_owner_row(owner_id)
        elif TRADES_QUERY in sql:
            result.fetchall.return_value = trades
        elif TERMINAL_UPDATE in sql:
            status = "cancelled" if cancel_requested else "failed"
            result.fetchone.return_value = StatusRow(
                id=params.get("id"), status=status, cancel_requested=cancel_requested
            )
        elif FINAL_STATUS_LOCK in sql:
            result.fetchone.return_value = StatusRow(
                id=params.get("id"), status="running", cancel_requested=cancel_requested
            )
        elif ACQUIRE_EXECUTION in sql:
            result.fetchone.return_value = (
                SimpleNamespace(id=params.get("id")) if execution_acquired else None
            )
        elif TERMINAL_COMPLETION in sql:
            # The completion write is conditional: zero matched rows is the
            # service's only signal that the run left 'running' underneath it.
            result.rowcount = completion_rowcount
        elif CANCEL_WRITE in sql:
            result.rowcount = cancel_rowcount
        elif sql.upper().startswith(_WRITE_PREFIXES):
            result.rowcount = 1
        elif sql.upper().startswith("SELECT"):
            raise AssertionError(f"replay issued an undeclared SELECT: {sql[:160]}")
        return result

    def refused(name):
        def fail(*args, **kwargs):
            raise AssertionError(
                f"replay_fake: the replay reached the session through db.{name}(). "
                "This path issues raw statements, so an ORM read or write has to "
                "be declared by statement content instead of being answered by a "
                "mock that returns another mock."
            )

        return fail

    def boundary(name):
        def record(*args, **kwargs):
            journal.append(("txn", name))

        return record

    db = MagicMock(spec=Session)
    db.execute.side_effect = execute
    for name in ("commit", "rollback", "close"):
        getattr(db, name).side_effect = boundary(name)
    db.calls = calls
    db.journal = journal
    for name in _REFUSED_SESSION_METHODS:
        getattr(db, name).side_effect = refused(name)
    return db


def params_of(db, marker):
    """Bind parameters of the recorded statement containing ``marker``."""
    matches = [params for sql, params in db.calls if marker in sql]
    assert len(matches) == 1, f"expected exactly one statement with {marker!r}, got {len(matches)}"
    return matches[0]
