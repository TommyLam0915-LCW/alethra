"""Say whether a finished run's stored series can still be read back today.

``research_runs.dataset_checksum`` is a claim about rows that used to be in
``market_data_bars``. It does not age, and it does not say so when the rows
it describes are gone. Two completed runs in the evidence lane make the cost
of that silence concrete: the same instrument, provider, window and 184-bar
count, two different digests, and no storage behind either one — the provider
they read has zero rows in the table now, and no recipe reproduces either
recorded digest. Their ``dataset_snapshot_id`` is NULL because they finished
before the registry existed.

This module reads the table and answers the question the row cannot answer
for itself. It is deliberately a *derivation*, not a column: reproducibility
is a property of what storage holds at the moment of asking, so a value
written at completion time would be a guess about the future, and a value
written later — a snapshot minted to close the gap — would be exactly the
backfill that makes an unverifiable history look verified. Nothing here
writes. An absent series stays absent, and the run stays what it is.

The other deliberate choice is direction of error. A run is called
reproducible only when re-running the engine's own checksum recipe over the
rows still stored under the run's recorded provider and window returns the
digest the run recorded. Anything short of that equality — no recorded
coverage, no provider, a different row count, a different digest — is
reported as unverifiable or not reproducible. When the two readings are
possible, the one that admits the gap is the one that keeps a stale number
out of a performance claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.market_data import MarketDataBar
from app.models.research_run import ResearchRun

REPRODUCIBLE = "reproducible"
NOT_REPRODUCIBLE = "not_reproducible"
UNVERIFIABLE = "unverifiable"

#: Only a run that priced off rows in ``market_data_bars`` has a series that
#: could still be there. A fetched read took its prices from the venue's
#: rolling window, which this database never held.
STORED_SOURCE = "stored"

# Reasons are vocabulary, not prose: they are what a caller branches on and
# what an evidence record cites. The sentence is derived from them.
NO_RESULT_PAYLOAD = "no_result_payload"
NOT_READ_FROM_STORAGE = "not_read_from_storage"
NO_RECORDED_CHECKSUM = "no_recorded_checksum"
NO_RECORDED_COVERAGE = "no_recorded_coverage"
NO_RECORDED_WINDOW = "no_recorded_window"
NO_RECORDED_PROVIDER = "no_recorded_provider"
STORED_SERIES_ABSENT = "stored_series_absent"
STORED_ROW_COUNT_CHANGED = "stored_row_count_changed"
STORED_SERIES_CHANGED = "stored_series_changed"
STORED_SERIES_REPRODUCED = "stored_series_reproduced"
LEGS_DISAGREE = "legs_disagree"

_REASON_TEXT = {
    NO_RESULT_PAYLOAD: "run has no result payload to name its series",
    NOT_READ_FROM_STORAGE: "run did not price off stored bars, so no stored "
                           "series could reproduce it",
    NO_RECORDED_CHECKSUM: "run recorded no dataset checksum to compare against",
    NO_RECORDED_COVERAGE: "run recorded no per-instrument coverage",
    NO_RECORDED_WINDOW: "run recorded a window this build cannot read as dates",
    NO_RECORDED_PROVIDER: "run recorded no provider for its stored rows",
    STORED_SERIES_ABSENT: "no rows remain stored for the series this run read",
    STORED_ROW_COUNT_CHANGED: "the stored series no longer has the row count "
                              "this run read",
    STORED_SERIES_CHANGED: "the stored series re-hashes to a different digest "
                           "than the one this run recorded",
    STORED_SERIES_REPRODUCED: "the stored series re-hashes to the digest this "
                              "run recorded",
    LEGS_DISAGREE: "the run's instruments do not all reproduce",
}


@dataclass(frozen=True)
class SeriesVerdict:
    """The measured state of one instrument's series, as stored today."""

    instrument_id: str
    provider_ids: Tuple[str, ...]
    start_date: str
    end_date: str
    status: str
    reason: str
    recorded_rows: Optional[int] = None
    stored_rows: int = 0
    recorded_checksum: Optional[str] = None
    recomputed_checksum: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "provider_ids": list(self.provider_ids),
            "window": [self.start_date, self.end_date],
            "status": self.status,
            "reason": self.reason,
            "recorded_rows": self.recorded_rows,
            "stored_rows": self.stored_rows,
            "recorded_checksum": self.recorded_checksum,
            "recomputed_checksum": self.recomputed_checksum,
        }


@dataclass(frozen=True)
class RunReproducibility:
    status: str
    reason: str
    snapshot_registered: bool
    legs: Tuple[SeriesVerdict, ...] = ()

    @property
    def detail(self) -> str:
        if self.legs and self.reason == LEGS_DISAGREE:
            worst = next(leg for leg in self.legs if leg.status != REPRODUCIBLE)
            return (
                f"{_REASON_TEXT[worst.reason]} "
                f"({worst.instrument_id}: {worst.stored_rows} rows stored, "
                f"{worst.recorded_rows} read)"
            )
        if self.legs:
            leg = self.legs[0]
            return (
                f"{_REASON_TEXT[self.reason]} "
                f"({leg.instrument_id}: {leg.stored_rows} rows stored, "
                f"{leg.recorded_rows} read)"
            )
        return _REASON_TEXT[self.reason]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "snapshot_registered": self.snapshot_registered,
            "legs": [leg.as_dict() for leg in self.legs],
        }


def _run_verdict(
    run: ResearchRun, reason: str, *, snapshot_registered: bool
) -> RunReproducibility:
    """A whole-run answer with nothing measured per instrument.

    These are the cases where the run cannot be checked at all, so they carry
    no legs: there is no recorded series to go looking for. None of them is
    ever a green light — an unchecked run and a verified one are different
    claims, and only the second one belongs in a performance surface.
    """
    return RunReproducibility(
        status=UNVERIFIABLE, reason=reason, snapshot_registered=snapshot_registered
    )


def _window(result: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """The run's own period, as the engine received it.

    The engine filtered its read by these two values, so they are what a
    re-derivation has to filter by. Per-instrument ``first_bar_date`` and
    ``last_bar_date`` say where the rows landed, which is a different
    quantity: a window can be right while the series inside it is not.
    """
    start, end = result.get("start_date"), result.get("end_date")
    if not start or not end:
        return None
    try:
        date.fromisoformat(str(start))
        date.fromisoformat(str(end))
    except ValueError:
        return None
    return str(start), str(end)


def _providers(entry: Dict[str, Any]) -> Tuple[str, ...]:
    recorded = entry.get("provider_ids") or []
    if not recorded and entry.get("provider_id"):
        recorded = [entry["provider_id"]]
    return tuple(sorted({str(p) for p in recorded if p}))


def _stored_bars(
    db: Session, instrument_id: str, providers: Sequence[str], start: str, end: str
) -> List[Any]:
    """Every row still stored for one instrument of one run, across grains.

    No grain is bound here on purpose. The run's digest was taken over whatever
    its read returned, and a grain filter chosen now would be a claim about a
    read this module did not witness — the same substitution that let two grains
    share a midnight and produce one 184-bar count and two digests.
    """
    return (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == instrument_id,
            MarketDataBar.provider_id.in_(list(providers)),
            MarketDataBar.bar_date >= date.fromisoformat(start),
            MarketDataBar.bar_date <= date.fromisoformat(end),
        )
        .all()
    )


def _assess_leg(
    db: Session,
    *,
    instrument_id: str,
    entry: Dict[str, Any],
    recorded_checksum: str,
    window: Tuple[str, str],
) -> SeriesVerdict:
    providers = _providers(entry)
    start, end = window
    common = {
        "instrument_id": instrument_id,
        "provider_ids": providers,
        "start_date": start,
        "end_date": end,
        "recorded_checksum": recorded_checksum,
        "recorded_rows": entry.get("bar_count"),
    }
    if not providers:
        return SeriesVerdict(status=UNVERIFIABLE, reason=NO_RECORDED_PROVIDER, **common)

    # The engine's own converter and its own digest, imported rather than
    # restated: a second copy of the recipe would let this module certify a
    # series the engine would hash differently.
    from app.engine.backtest import _compute_stored_checksum, _stored_bars_to_dataframe

    rows = _stored_bars(db, instrument_id, providers, start, end)
    if not rows:
        return SeriesVerdict(
            status=NOT_REPRODUCIBLE, reason=STORED_SERIES_ABSENT, stored_rows=0, **common
        )
    recomputed = _compute_stored_checksum(
        {instrument_id: _stored_bars_to_dataframe(rows)}, [instrument_id], start, end
    )
    if common["recorded_rows"] is not None and len(rows) != common["recorded_rows"]:
        return SeriesVerdict(
            status=NOT_REPRODUCIBLE,
            reason=STORED_ROW_COUNT_CHANGED,
            stored_rows=len(rows),
            recomputed_checksum=recomputed,
            **common,
        )
    if recomputed != recorded_checksum:
        return SeriesVerdict(
            status=NOT_REPRODUCIBLE,
            reason=STORED_SERIES_CHANGED,
            stored_rows=len(rows),
            recomputed_checksum=recomputed,
            **common,
        )
    return SeriesVerdict(
        status=REPRODUCIBLE,
        reason=STORED_SERIES_REPRODUCED,
        stored_rows=len(rows),
        recomputed_checksum=recomputed,
        **common,
    )


def assess_run_reproducibility(db: Session, run: ResearchRun) -> RunReproducibility:
    """Derive one run's reproducibility from the rows stored right now."""
    snapshot_registered = getattr(run, "dataset_snapshot_id", None) is not None
    result = run.result_json or {}
    if not result:
        return _run_verdict(
            run, NO_RESULT_PAYLOAD, snapshot_registered=snapshot_registered
        )
    if result.get("data_source_used") != STORED_SOURCE:
        return _run_verdict(
            run, NOT_READ_FROM_STORAGE, snapshot_registered=snapshot_registered
        )
    recorded_checksum = run.dataset_checksum or result.get("dataset_checksum")
    if not recorded_checksum:
        return _run_verdict(
            run, NO_RECORDED_CHECKSUM, snapshot_registered=snapshot_registered
        )
    window = _window(result)
    if window is None:
        return _run_verdict(
            run, NO_RECORDED_WINDOW, snapshot_registered=snapshot_registered
        )
    coverage = result.get("dataset_coverage") or {}
    if not coverage:
        return _run_verdict(
            run, NO_RECORDED_COVERAGE, snapshot_registered=snapshot_registered
        )

    legs = tuple(
        _assess_leg(
            db,
            instrument_id=str(instrument_id),
            entry=entry or {},
            recorded_checksum=str(recorded_checksum),
            window=window,
        )
        for instrument_id, entry in sorted(coverage.items())
    )

    if all(leg.status == REPRODUCIBLE for leg in legs):
        return RunReproducibility(
            status=REPRODUCIBLE,
            reason=STORED_SERIES_REPRODUCED,
            snapshot_registered=snapshot_registered,
            legs=legs,
        )
    if len(legs) == 1:
        leg = legs[0]
        return RunReproducibility(
            status=leg.status,
            reason=leg.reason,
            snapshot_registered=snapshot_registered,
            legs=legs,
        )
    # A run is only as recomputable as its least verifiable leg: an instrument
    # whose rows are all gone outranks one that merely changed, and neither
    # outranks one this build could not check at all.
    statuses = {leg.status for leg in legs}
    status = (
        UNVERIFIABLE
        if UNVERIFIABLE in statuses
        else NOT_REPRODUCIBLE
    )
    return RunReproducibility(
        status=status,
        reason=LEGS_DISAGREE,
        snapshot_registered=snapshot_registered,
        legs=legs,
    )
