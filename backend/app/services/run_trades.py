"""Per-fill export for a formal research run.

The durable lane already stores every fill it made — ``result_json.transactions``
is written when the run publishes its outcome. What was missing is an entry that
hands those fills to the account that ran them in a shape a spreadsheet opens,
without inventing a column the engine never wrote and without renaming one it
did. Two rules govern everything below.

1. The columns are the stored fill's own keys — what
   ``app/engine/strategies.py::generate_transaction`` writes and what
   ``app/engine/attribution.py::annotate_and_attribute`` stamps on the same row
   before the run stores it. An export that renamed ``net_cash_change`` into a
   journal-lane name would make two lanes with different cash conventions look
   like the same number.
2. An empty file is produced only when the record says the run filled nothing.
   A run that failed, was cancelled, is still computing, or was published
   without a transaction record is refused with the reason named, because those
   four states would otherwise all read back as "this strategy made no trades".

Both ``/runs/{run_id}/trades`` and ``/runs/{run_id}/trades.csv`` go through
:func:`build_run_trades_export`, so the basis decision — and therefore what an
empty result means — cannot differ between the two routes.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus

# What ``generate_transaction`` writes on the leg itself, in the order it
# assembles them.
_FILL_KEYS = (
    "transaction_id",
    "trade_date",
    "signal_date",
    "execution_date",
    "symbol",
    "side",
    "quantity",
    "observed_price",
    "execution_price_before_costs",
    "slippage_amount",
    "commission_amount",
    "gross_amount",
    "net_cash_change",
    "cash_after",
    "shares_after",
    "rationale",
    "exit_reason",
)

# What ``annotate_and_attribute`` adds to the same row before the run stores it:
# who produced the fill, the spec hash it was executed against, the declared
# risk plan and the FIFO-matched result of this leg.
_ATTRIBUTION_KEYS = (
    "actor",
    "strategy_config_hash",
    "stop_price",
    "target_price",
    "planned_risk",
    "planned_r",
    "realized_pnl",
    "realized_r",
)

# The export's canonical fill columns: exactly the keys the stored record
# carries. Renaming one into a journal-lane name would make two lanes with
# different cash conventions look like the same number; losing one would
# silently drop money from a file the customer takes as evidence.
ENGINE_TRANSACTION_KEYS = _FILL_KEYS + _ATTRIBUTION_KEYS

# Stamped on every exported row, so a file that leaves the platform still says
# whose it is, which locked version produced it, which data it priced off and
# which engine and cost configuration computed it.
PROVENANCE_KEYS = (
    "run_id",
    "owner_id",
    "experiment_id",
    "experiment_version_id",
    "version_number",
    "run_status",
    "export_basis",
    "config_hash",
    "engine_version",
    "dataset_checksum",
    "window_start",
    "window_end",
    "data_nature",
    "data_provider",
    "feed_mode",
    "bars_used",
)

BASIS_WITH_TRADES = "completed_with_trades"
BASIS_WITHOUT_TRADES = "completed_without_trades"

_BASIS_NOTES = {
    BASIS_WITH_TRADES: (
        "Every fill this run recorded is listed, with the run's own data and "
        "configuration identity repeated on each row."
    ),
    BASIS_WITHOUT_TRADES: (
        "This run completed and recorded zero fills. The list is empty because "
        "nothing was filled — not because the run failed, was cancelled, or "
        "lost its result."
    ),
}

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class RunNotExportable(Exception):
    """The run's own record cannot support a trade export, and says why.

    ``code`` is the stable name of the state, ``run_status`` is what the row
    actually says, and ``note`` is the server's sentence for what cannot be
    exported and what would be wrongly implied if it exported nothing.
    """

    def __init__(self, code: str, run_status: str, note: str) -> None:
        super().__init__(note)
        self.code = code
        self.run_status = run_status
        self.note = note

    def detail(self) -> Dict[str, Any]:
        return {
            "error": self.code,
            "run_status": self.run_status,
            "message": self.note,
        }


def _csv_cell(value: Any) -> str:
    """Same guard the journal lane applies: no cell re-types the spreadsheet."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return format(Decimal(str(value)), "f")
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def get_owned_run(
    db: Session,
    *,
    run_id: UUID,
    user_id: UUID,
) -> Optional[Tuple[ResearchRun, ExperimentVersion, Experiment]]:
    """The run plus the version and experiment it belongs to, or nothing.

    Ownership is decided by the same join the read and cancel entries use
    (``experiment.owner_id``), so a run id this account does not own and a run
    id that does not exist leave the same answer at the route above.
    """
    run = (
        db.query(ResearchRun)
        .join(
            ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
        )
        .join(Experiment, ExperimentVersion.experiment_id == Experiment.id)
        .filter(
            ResearchRun.id == run_id,
            Experiment.owner_id == user_id,
        )
        .first()
    )
    if not run:
        return None
    version = (
        db.query(ExperimentVersion)
        .filter(ExperimentVersion.id == run.experiment_version_id)
        .first()
    )
    if not version:
        return None
    experiment = (
        db.query(Experiment).filter(Experiment.id == version.experiment_id).first()
    )
    if not experiment:
        return None
    return run, version, experiment


def _provenance(
    run: ResearchRun,
    version: ExperimentVersion,
    experiment: Experiment,
    result: Dict[str, Any],
    export_basis: str,
) -> Dict[str, Any]:
    checksum = run.dataset_checksum
    if checksum is None:
        checksum = result.get("dataset_checksum")
    return {
        "run_id": str(run.id),
        "owner_id": str(experiment.owner_id),
        "experiment_id": str(experiment.id),
        "experiment_version_id": str(version.id),
        "version_number": version.version_number,
        "run_status": run.status.value if run.status else None,
        "export_basis": export_basis,
        "config_hash": run.config_hash,
        "engine_version": run.engine_version,
        "dataset_checksum": checksum,
        "window_start": result.get("start_date"),
        "window_end": result.get("end_date"),
        "data_nature": result.get("data_nature"),
        "data_provider": result.get("data_provider"),
        "feed_mode": result.get("feed_mode"),
        "bars_used": result.get("bars_used"),
    }


def build_run_trades_export(
    run: ResearchRun,
    version: ExperimentVersion,
    experiment: Experiment,
) -> Dict[str, Any]:
    """The run's fills with their identity attached, or a refusal naming the state.

    ``export_basis`` is the part a caller cannot infer from a row count: the
    difference between a run that filled nothing and a run whose record cannot
    answer the question at all.
    """
    status_value = run.status.value if run.status else None

    if run.status == RunStatus.failed:
        raise RunNotExportable(
            "run_failed",
            status_value,
            "This run did not produce a result, so it has no fills to export. An "
            "empty export here would read as evidence about the strategy.",
        )
    if run.status == RunStatus.cancelled:
        raise RunNotExportable(
            "run_cancelled",
            status_value,
            "This run was closed by a decision at POST /api/v1/runs/"
            f"{run.id}/cancel, so its fills were never published. An empty "
            "export here would read as evidence about the strategy.",
        )
    if run.status in (RunStatus.pending, RunStatus.running):
        raise RunNotExportable(
            "run_not_finished",
            status_value,
            "This run has not published an outcome yet. The fills are written "
            "with the outcome, so there is nothing to export while it is "
            f"{'queued' if run.status == RunStatus.pending else 'computing'}.",
        )

    result = run.result_json or {}
    transactions = result.get("transactions")
    if transactions is None:
        raise RunNotExportable(
            "result_not_recorded",
            status_value,
            "This run is marked completed but its stored result carries no "
            "transaction record, so an empty export would claim the strategy "
            "filled nothing on evidence the record does not contain.",
        )

    basis = BASIS_WITH_TRADES if transactions else BASIS_WITHOUT_TRADES
    provenance = _provenance(run, version, experiment, result, basis)

    keys = list(ENGINE_TRANSACTION_KEYS)
    for row in transactions:
        for key in row:
            if key not in keys:
                keys.append(key)
    columns = keys + list(PROVENANCE_KEYS)

    trades = [
        {**{key: row.get(key) for key in keys}, **provenance}
        for row in transactions
    ]

    return {
        "run_id": run.id,
        "experiment_id": experiment.id,
        "experiment_version_id": version.id,
        "version_number": version.version_number,
        "owner_id": experiment.owner_id,
        "status": status_value,
        "export_basis": basis,
        "basis_note": _BASIS_NOTES[basis],
        "provenance": provenance,
        "columns": columns,
        "trade_count": len(trades),
        "trades": trades,
    }


def run_trades_csv_content(payload: Dict[str, Any]) -> str:
    """Serialize an export payload. The columns come from the payload itself."""
    columns = payload["columns"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(columns)
    for row in payload["trades"]:
        writer.writerow([_csv_cell(row.get(column)) for column in columns])
    return buffer.getvalue()
