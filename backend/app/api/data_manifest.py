"""What the stored data actually is, before a run pays for it.

Preflight says whether a spec matches the contract; it cannot say whether the
bars exist. This surface answers the data half of the same decision, and it does
so by calling the engine's own read path rather than restating it:

* ``_load_stored_bars`` — the same call, with the same multi-provider refusal,
  that ``data_source="stored"`` and ``data_source="auto"`` both make;
* ``_compute_stored_checksum`` — the identical function whose output a run
  persists as ``dataset_checksum``, so a manifest and a run can be compared
  instead of leaving two numbers that look contradictory;
* the engine's own ``dataset_coverage`` key names, so one row of this answer
  lines up with one entry of the record the run leaves behind;
* ``trading_days_between`` and ``MarketStructure.trading_day_status`` for
  sessions, with absence reported only where the calendar is authoritative.

Read-only: it stores no row, reserves no quota, opens no replay session, calls
no provider over the network and authorizes nothing.
"""
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.schemas import StrategySpec
from app.api.strategy_templates import SCOPE_NOTE, check_spec_contract
from app.auth.jwt import get_current_user
from app.database import get_db
from app.engine.backtest import (
    _ambiguous_dataset_error,
    _compute_stored_checksum,
    _load_stored_bars,
)
from app.models.market_data import MarketDataBar
from app.models.user import User
from app.providers.instruments import (
    get_market_structure,
    parse_instrument_id,
    trading_days_between,
)
from app.services.market_data_storage import MarketDataStorage

router = APIRouter(tags=["strategy-specs"])

# The rows the engine trades are identified by (instrument, provider, grain,
# timestamp), so the same bar_date can legitimately hold two rows — one per
# grain. run_backtest does not filter by grain, so it hands both to the
# template as two sessions on one date. That is a contradiction inside the
# window, not a matter of interpretation, so it blocks.
DUPLICATE_DATES = "duplicate_dates"
MULTIPLE_PROVIDERS = "multiple_providers"

_MANIFEST_SCOPE_NOTE = (
    SCOPE_NOTE
    + " The manifest describes stored rows only: it lists no traded instrument,"
    " provider entitlement or execution authorization, and a 'ready' verdict is"
    " not permission to run."
)

_CANNOT_PROVE = [
    "A matching checksum identifies these bytes, not their market reality. The"
    " only provenance carrier storage holds is MarketDataMetadata.data_nature,"
    " scoped to (instrument, provider) and not to a window, so a 'live' label"
    " here does not prove the rows in this window arrived from a live feed.",
    "The engine's stored read does not filter by interval_minutes, so the grains"
    " listed here are the labels on the rows rather than a check that the rows"
    " match the timeframe the spec declares. The per-bar replay lane does filter"
    " by grain; the batch backtest does not.",
    "Dates outside the market calendar's declared coverage cannot be told apart"
    " from closures, so an absent session there is reported as unknown, not as"
    " missing data.",
    "No provider was contacted and no fetch was performed. Storage is described"
    " as stored, including where it is empty.",
    "Nothing here establishes that a run using this dataset would be correct"
    " arithmetic, only which rows it would price off.",
]


class GrainFact(BaseModel):
    interval_minutes: Optional[int]
    label: str
    bar_count: int


class CalendarFact(BaseModel):
    market: Optional[str] = None
    reason: Optional[str] = None
    coverage_start: Optional[str] = None
    coverage_end: Optional[str] = None
    expected_known_open_sessions: int = 0
    sessions_the_calendar_cannot_judge: int = 0
    missing_known_open_sessions: List[str] = []
    missing_known_open_count: int = 0
    bars_on_days_the_calendar_closes: List[str] = []
    note: str


class InstrumentRow(BaseModel):
    instrument_id: str
    role: str
    status: str
    source: Optional[str] = None
    provider_id: Optional[str] = None
    provider_ids: List[str] = []
    data_nature: Optional[str] = None
    bar_count: int = 0
    first_bar_date: Optional[str] = None
    last_bar_date: Optional[str] = None
    grains: List[GrainFact] = []
    duplicate_dates: List[str] = []
    calendar: Optional[CalendarFact] = None


class ManifestIssue(BaseModel):
    code: str
    instrument_id: Optional[str] = None
    message: str
    remedy: Optional[str] = None


class DataManifestResponse(BaseModel):
    status: str
    template_type: Optional[str] = None
    window: Dict[str, Optional[str]]
    provider_id_bound: Optional[str] = None
    instruments: List[InstrumentRow]
    dataset_checksum: Optional[str] = None
    checksum_basis: str
    bars_loaded: int
    blockers: List[ManifestIssue] = []
    warnings: List[ManifestIssue] = []
    run_behavior: Dict[str, str]
    engine_basis: List[str]
    scope_note: str
    cannot_prove: List[str]


# Each entry states the number and the comparison it comes from, in the engine's
# own words, so a shortfall here is the same shortfall the template warns about
# rather than a second opinion invented by this module.
def _min_bars_required(template_type: str, signal_parameters: Dict[str, Any]) -> Tuple[int, str]:
    if template_type == "buy_and_hold":
        return 2, "run_buy_and_hold returns 'need at least 2 trading days' below 2 rows"
    if template_type == "sma_crossover":
        long_window = int(signal_parameters["long_window"])
        return (
            long_window + 1,
            f"run_sma_crossover warns when the series is shorter than long_window + 1 ({long_window} + 1)",
        )
    lookback_days = int(signal_parameters["lookback_days"])
    return (
        lookback_days + 1,
        f"run_price_change_fixed_hold starts its scan at index lookback_days ({lookback_days}), "
        "so a shorter series yields no signal at all",
    )


def _grain_facts(rows: List[MarketDataBar]) -> List[GrainFact]:
    counts: Dict[Optional[int], int] = {}
    for row in rows:
        counts[row.interval_minutes] = counts.get(row.interval_minutes, 0) + 1
    facts = []
    # Labelled grains first, unlabelled last: NULL is not a grain of zero, and
    # sorting it among the numbers would present it as one.
    for grain in sorted(counts, key=lambda g: (g is None, g if g is not None else 0)):
        facts.append(
            GrainFact(
                interval_minutes=grain,
                label=(
                    "unlabelled — written before the grain was recorded"
                    if grain is None
                    else f"{grain} minutes" + (" (daily)" if grain == 1440 else "")
                ),
                bar_count=counts[grain],
            )
        )
    return facts


def _duplicate_dates(rows: List[MarketDataBar]) -> List[str]:
    per_date: Dict[str, int] = {}
    for row in rows:
        key = row.bar_date.isoformat()
        per_date[key] = per_date.get(key, 0) + 1
    return sorted(key for key, count in per_date.items() if count > 1)


def _market_of(instrument_id: str):
    """The market a calendar can be looked up in, or None for a demo fixture."""
    try:
        market, _symbol = parse_instrument_id(instrument_id)
    except ValueError:
        return None
    return market


def _calendar_fact(
    instrument_id: str,
    rows: List[MarketDataBar],
    start: date,
    end: date,
) -> Optional[CalendarFact]:
    """Sessions the calendar can judge, and the absences it can prove.

    Returns None when the id carries no market, because there is no market
    structure to be wrong about; the caller says so in a warning instead of
    leaving the slot empty and unexplained.
    """
    market = _market_of(instrument_id)
    if market is None:
        return None

    structure = get_market_structure(market)
    expected = trading_days_between(market, start, end)
    known_open = [d for d in expected if structure.trading_day_status(d) == "open"]
    unjudgeable = [d for d in expected if structure.trading_day_status(d) == "calendar_unknown"]
    present = {row.bar_date for row in rows}

    missing = [d.isoformat() for d in known_open if d not in present]
    closed_hits = sorted(
        d.isoformat()
        for d in present
        if structure.trading_day_status(d) in ("weekend", "holiday")
    )
    coverage = structure.calendar_coverage

    if structure.always_open:
        note = (
            "This market trades every calendar day, so every day in the window is"
            " an expected session and a gap here is a real gap."
        )
    elif coverage is None:
        note = "The platform declares no holiday table for this market."
    else:
        note = (
            f"The holiday table is authoritative from {coverage[0].isoformat()} to"
            f" {coverage[1].isoformat()}. Outside it, a weekday with no bar is not"
            " proof of missing data — it may have been a closure the platform has"
            " not recorded."
        )

    return CalendarFact(
        market=market.value,
        coverage_start=coverage[0].isoformat() if coverage else None,
        coverage_end=coverage[1].isoformat() if coverage else None,
        expected_known_open_sessions=len(known_open),
        sessions_the_calendar_cannot_judge=len(unjudgeable),
        missing_known_open_sessions=missing[:20],
        missing_known_open_count=len(missing),
        bars_on_days_the_calendar_closes=closed_hits[:20],
        note=note,
    )


def _roles(spec: StrategySpec, all_asset_ids: List[str]) -> Dict[str, str]:
    roles: Dict[str, str] = {}
    for index, aid in enumerate(spec.asset_ids):
        roles[aid] = "traded" if index == 0 else "loaded_not_traded"
    if spec.benchmark_id:
        roles.setdefault(spec.benchmark_id, "benchmark")
    return {aid: roles.get(aid, "benchmark") for aid in all_asset_ids}


def _run_behavior(
    absent: List[str],
    ambiguous: Dict[str, List[str]],
    window: Tuple[str, str],
    blocked: bool,
) -> Dict[str, str]:
    """What each data_source mode does with exactly this dataset.

    ``stored`` checks every asset id, not only the first, so an instrument the
    template never trades still refuses a stored-mode run when it is missing.
    """
    start, end = window
    names = ", ".join(absent)
    refusal = (
        "Refuses before any trade and before any fallback fetch, because more than"
        " one provider holds rows in this window."
        if ambiguous
        else None
    )
    stored = refusal or (
        f'Refuses with "No stored data for {names} in range {start} to {end}.'
        ' Fetch from provider first." and records no result.'
        if absent
        else "Prices off exactly the rows listed here and records the dataset_checksum"
        " this manifest reports."
    )
    auto = refusal or (
        "Fetches "
        + names
        + " from the configured provider — or from the built-in demo feed, which is"
        " deterministic synthetic data — and its checksum then covers the stored rows"
        " plus the fetched ones, so it is not the value reported here."
        if absent
        else "Identical to 'stored' for this window: nothing is missing, so no provider"
        " is contacted and the run records data_source_used='stored'."
    )
    return {
        "data_source=stored": stored,
        "data_source=auto": auto,
        "data_source=provider": (
            "Ignores the stored rows entirely, so this manifest describes a dataset"
            " the run would not price off."
        ),
        "verdict": (
            "No mode runs this window while a blocker stands: 'blocked' means the"
            " stored rows contradict each other, not that they are merely thin."
            if blocked
            else "No blockers, so the mode the caller asks for decides the rest"
            + (" apart from the instruments listed as absent." if absent else ".")
        ),
    }


@router.post(
    "/strategy-specs/data-manifest",
    response_model=DataManifestResponse,
    summary="Describe the stored dataset a spec would price off",
    description=(
        "Read-only answer to the data half of 'can this spec run': for every "
        "instrument in the window it reports which stored provider holds rows, the "
        "resolved data_nature, the bar count and span, the grains actually labelled "
        "on those rows, dates holding more than one row, and the sessions the market "
        "calendar can prove are missing. The checksum is the one function that "
        "produces a run's dataset_checksum. Status is 'ready', 'partial' (an "
        "instrument would have to be fetched), 'blocked' (the stored rows contradict "
        "each other, so no mode runs this window) or 'invalid_spec'. It stores no "
        "row, reserves no quota and calls no provider over the network."
    ),
)
def build_data_manifest(
    spec: Dict[str, Any] = Body(
        ...,
        description=(
            "A StrategySpec-shaped object, validated by the same gate preflight "
            "uses. It is described rather than used, so it may be incomplete."
        ),
    ),
    provider_id: Optional[str] = Query(
        None,
        max_length=50,
        description=(
            "Bind one stored dataset, exactly as POST /runs/{version_id} does. "
            "Without it, an instrument held by two providers is reported as a "
            "blocker rather than merged."
        ),
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    status, template_type, errors, reason, parsed = check_spec_contract(spec)
    if status != "valid":
        code = "invalid_spec" if status == "invalid" else "unsupported"
        return DataManifestResponse(
            status="invalid_spec",
            template_type=template_type,
            window={"start_date": None, "end_date": None},
            provider_id_bound=provider_id,
            instruments=[],
            dataset_checksum=None,
            checksum_basis=(
                "No dataset was read: the spec does not pass the contract gate, so"
                " there is no window to resolve."
            ),
            bars_loaded=0,
            blockers=[
                ManifestIssue(
                    code=code,
                    instrument_id=None,
                    message=reason or "; ".join(issue["message"] for issue in errors),
                    remedy=(
                        "Fix the fields listed by preflight and ask again."
                        if status == "invalid"
                        else "This path does not execute that capability; preflight names the route that does."
                    ),
                )
            ],
            warnings=[
                ManifestIssue(
                    code="spec_field",
                    instrument_id=None,
                    message=f"{issue['field']}: {issue['message']}",
                )
                for issue in errors
            ],
            run_behavior={
                name: "Not reached: the spec is not executable, so no mode runs it."
                for name in (
                    "data_source=stored",
                    "data_source=auto",
                    "data_source=provider",
                    "blocked",
                )
            },
            engine_basis=["app/api/strategy_templates.py :: check_spec_contract"],
            scope_note=_MANIFEST_SCOPE_NOTE,
            cannot_prove=_CANNOT_PROVE,
        )

    assert parsed is not None
    start = date.fromisoformat(parsed.start_date)
    end = date.fromisoformat(parsed.end_date)

    # The same union the engine builds (run_backtest), so a benchmark that is
    # also tradable cannot be described as one dataset here and another there.
    all_asset_ids = sorted(set(parsed.asset_ids) | ({parsed.benchmark_id} if parsed.benchmark_id else set()))
    data, natures, ambiguous, providers = _load_stored_bars(
        db, all_asset_ids, parsed.start_date, parsed.end_date, provider_id=provider_id
    )
    storage = MarketDataStorage(db)

    blockers: List[ManifestIssue] = []
    warnings: List[ManifestIssue] = []
    rows_out: List[InstrumentRow] = []
    roles = _roles(parsed, all_asset_ids)
    absent: List[str] = []

    if ambiguous:
        blockers.append(
            ManifestIssue(
                code=MULTIPLE_PROVIDERS,
                instrument_id=None,
                # The engine's own refusal sentence, not a paraphrase of it.
                message=_ambiguous_dataset_error(ambiguous, all_asset_ids) or "",
                remedy=(
                    "Pass provider_id to bind one dataset, exactly as creating a"
                    " replay session requires."
                ),
            )
        )

    for aid in all_asset_ids:
        role = roles[aid]
        if aid in ambiguous:
            rows_out.append(
                InstrumentRow(
                    instrument_id=aid,
                    role=role,
                    status="ambiguous",
                    provider_ids=ambiguous[aid],
                )
            )
            continue
        if aid not in data:
            if role != "benchmark":
                absent.append(aid)
            if role == "benchmark":
                message = (
                    f"No stored rows for {aid} between {parsed.start_date} and"
                    f" {parsed.end_date}. The benchmark is the one instrument a run"
                    " continues without: the engine warns and leaves the benchmark"
                    " curve empty rather than filling it in."
                )
                code = "benchmark_absent_warns"
            else:
                message = (
                    f"No stored rows for {aid} between {parsed.start_date} and"
                    f" {parsed.end_date}, so a 'stored' run refuses and an 'auto'"
                    " run must fetch it from a provider."
                )
                code = "absent_from_storage"
            warnings.append(
                ManifestIssue(
                    code=code,
                    instrument_id=aid,
                    message=message,
                    remedy=(
                        "Ingest the window first, or accept that this run will be"
                        " priced partly from a live fetch."
                    ),
                )
            )
            # No calendar block for an absent instrument: every session would
            # read as missing, which restates "there is no series here" as
            # though it were a hole inside one.
            rows_out.append(
                InstrumentRow(
                    instrument_id=aid,
                    role=role,
                    status="absent",
                )
            )
            if _market_of(aid) is None:
                warnings.append(
                    ManifestIssue(
                        code="calendar_not_applicable",
                        instrument_id=aid,
                        message=(
                            f"{aid} carries no market prefix, so the platform has no"
                            " trading calendar for it and cannot say which sessions"
                            " were expected."
                        ),
                    )
                )
            continue

        # The detail read is the same predicate _load_stored_bars just used, so
        # it sees the same rows; it exists because grain labels and same-date
        # collisions are questions the engine's read path does not answer, and
        # giving it a grain filter would change what a run trades.
        detail = storage.get_bars(
            instrument_id=aid,
            provider_id=providers[aid][0],
            start_date=start,
            end_date=end,
        )
        df = data[aid]
        duplicates = _duplicate_dates(detail)
        grains = _grain_facts(detail)
        if duplicates:
            blockers.append(
                ManifestIssue(
                    code=DUPLICATE_DATES,
                    instrument_id=aid,
                    message=(
                        f"{len(duplicates)} date(s) in this window hold more than one"
                        f" row from provider {providers[aid][0]} ({', '.join(duplicates[:5])}"
                        f"{' …' if len(duplicates) > 5 else ''}). The batch backtest"
                        " filters by instrument, provider and date only, so it trades"
                        " every one of those rows as a separate session."
                    ),
                    remedy=(
                        "Re-import the window with one grain per date, or bind"
                        " provider_id and the grain the rows carry."
                    ),
                )
            )
        calendar = _calendar_fact(aid, detail, start, end)
        if calendar is None:
            warnings.append(
                ManifestIssue(
                    code="calendar_not_applicable",
                    instrument_id=aid,
                    message=(
                        f"{aid} carries no market prefix, so the platform has no"
                        " trading calendar for it and cannot say which sessions were"
                        " expected."
                    ),
                )
            )
        elif calendar.missing_known_open_count:
            warnings.append(
                ManifestIssue(
                    code="missing_sessions",
                    instrument_id=aid,
                    message=(
                        f"{calendar.missing_known_open_count} session(s) the calendar"
                        f" lists as open have no bar (first {len(calendar.missing_known_open_sessions)}"
                        f" named). A window with holes changes what a lookback of N"
                        " sessions means: N sessions here is not N market days."
                    ),
                )
            )
        if calendar and calendar.bars_on_days_the_calendar_closes:
            warnings.append(
                ManifestIssue(
                    code="bars_on_closed_days",
                    instrument_id=aid,
                    message=(
                        f"{len(calendar.bars_on_days_the_calendar_closes)} bar date(s)"
                        " fall on a weekend or a holiday the calendar lists. The market"
                        " was not open on them, so the series is not a market calendar."
                    ),
                )
            )
        nature = natures.get(aid)
        if nature == "synthetic":
            warnings.append(
                ManifestIssue(
                    code="synthetic_nature",
                    instrument_id=aid,
                    message=(
                        f"The provider backing {aid} is labelled synthetic: results"
                        " from these rows are not based on real market prices."
                    ),
                )
            )
        elif nature == "unknown":
            warnings.append(
                ManifestIssue(
                    code="unlabelled_nature",
                    instrument_id=aid,
                    message=(
                        f"No MarketDataMetadata row labels the provider backing {aid};"
                        " its nature is unknown rather than absent."
                    ),
                )
            )
        if any(grain.interval_minutes is None for grain in grains):
            unlabelled = next(
                grain.bar_count for grain in grains if grain.interval_minutes is None
            )
            warnings.append(
                ManifestIssue(
                    code="unlabelled_grain",
                    instrument_id=aid,
                    message=(
                        f"{unlabelled} of these rows carry no interval_minutes. They"
                        " are reported as stored, not relabelled as daily: a daily bar"
                        " stamped 00:00 and a four-hour bar stamped 00:00 cannot be"
                        " told apart by timestamp alone."
                    ),
                )
            )

        rows_out.append(
            InstrumentRow(
                instrument_id=aid,
                role=role,
                status="stored",
                source="stored",
                provider_id=providers[aid][0],
                provider_ids=providers[aid],
                data_nature=nature,
                bar_count=int(len(df)),
                first_bar_date=df["date"].iloc[0].strftime("%Y-%m-%d"),
                last_bar_date=df["date"].iloc[-1].strftime("%Y-%m-%d"),
                grains=grains,
                duplicate_dates=duplicates,
                calendar=calendar,
            )
        )

    min_bars, min_basis = _min_bars_required(parsed.template_type, parsed.signal_parameters)
    traded_row = next((row for row in rows_out if row.role == "traded"), None)
    if traded_row is not None and traded_row.status == "stored" and traded_row.bar_count < min_bars:
        warnings.append(
            ManifestIssue(
                code="insufficient_bars_for_template",
                instrument_id=traded_row.instrument_id,
                message=(
                    f"{traded_row.bar_count} bar(s) loaded, the template needs at least"
                    f" {min_bars}: {min_basis}. The run reports no trades rather than"
                    " refusing, which reads as a verdict on the strategy when it is a"
                    " verdict on the window."
                ),
                remedy="Widen the window or lower the declared parameter.",
            )
        )

    checksum = (
        _compute_stored_checksum(data, all_asset_ids, parsed.start_date, parsed.end_date)
        if data
        else None
    )
    blocked = bool(blockers)
    partial = bool(absent)
    overall = (
        "blocked" if blocked else "partial" if partial else "ready"
    )

    return DataManifestResponse(
        status=overall,
        template_type=parsed.template_type,
        window={"start_date": parsed.start_date, "end_date": parsed.end_date},
        provider_id_bound=provider_id,
        instruments=rows_out,
        dataset_checksum=checksum,
        checksum_basis=(
            "sha256 over 'instrument:YYYY-MM-DD:open:high:low:close' joined by '|',"
            " computed by app/engine/backtest.py :: _compute_stored_checksum — the same"
            " call whose output a run persists as dataset_checksum. It covers the"
            " instruments that have stored rows here; a run that has to fetch any"
            " instrument records a different value because its checksum also covers the"
            " fetched rows."
        ),
        bars_loaded=sum(row.bar_count for row in rows_out),
        blockers=blockers,
        warnings=warnings,
        run_behavior=_run_behavior(
            absent, ambiguous, (parsed.start_date, parsed.end_date), blocked
        ),
        engine_basis=[
            "app/engine/backtest.py :: _load_stored_bars — the read this manifest reuses",
            "app/engine/backtest.py :: _ambiguous_dataset_error — the refusal sentence above",
            "app/engine/backtest.py :: _compute_stored_checksum — the checksum above",
            "app/engine/backtest.py :: run_backtest dataset_coverage — the per-row key names",
            "app/engine/strategies.py :: run_buy_and_hold / run_sma_crossover /"
            " run_price_change_fixed_hold — the bar-count minimum quoted above",
            "app/providers/instruments.py :: trading_days_between /"
            " MarketStructure.trading_day_status — the session counts above",
        ],
        scope_note=_MANIFEST_SCOPE_NOTE,
        cannot_prove=_CANNOT_PROVE,
    )
