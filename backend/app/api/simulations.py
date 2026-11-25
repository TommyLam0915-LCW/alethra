from datetime import date, datetime, timezone
from importlib import import_module
from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion, VersionStatus
from app.models.simulation import SimulationSession, SimulationDecision, SimulationStatus, DecisionType
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.api.human_decisions import _derive_server_context
from app.api.conflicts import PAPER_ORDER_IDEMPOTENCY, expected_unique_violation
from app.api.schemas import (
    SimulationSessionCreate,
    SimulationSessionResponse,
    SimulationDecisionResponse,
    SimulationAdvanceResponse,
    DailyServiceRun,
    DailyServiceStatus,
    ManualDecisionRequest,
    ManualDecisionResponse,
    PaperOrderResponse,
    PaperOrderLifecycleResponse,
    ReflectionUpdateRequest,
    ReflectionUpdateResponse,
)
from app.engine.simulation import ForwardSimulator, SIMULATION_ENGINE_VERSION, StepResult
from app.engine.intraday import (
    INTRADAY_TIMEFRAMES,
    INTRADAY_ENGINE_VERSION,
    OpeningRangeForwardReplay,
    parse_intraday_config,
)
from app.engine.backtest import ENGINE_VERSION, compute_config_hash
from app.engine.fill_math import round_quantity
from app.models.paper_portfolio import (
    ConfidenceLevel,
    DecisionRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    OutcomeStatus,
    PaperDecisionActorType,
    PaperDecisionType,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PortfolioStatus,
    PositionStatus,
    RiskCheckStatus,
    decision_actor_type,
)
from app.services import emit_event
from app.services.decision_lineage import validate_linked_inference
from app.services.order_lifecycle import (
    LifecycleError,
    execute_due_orders,
    execution_view,
    expire_pending_orders,
    find_existing_by_key,
    resolve_session_portfolio,
    submit_pending,
)
from app.services.paper_execution import apply_rule_decision
from app.services.risk_policy import (
    RiskInputs,
    count_entries,
    evaluate_risk,
    value_open_positions,
)
from app.services.market_data_storage import DAILY_INTERVAL_MINUTES, MarketDataStorage
from app.models.market_data import MarketDataBar
from app.providers import get_data_provider
from sqlalchemy import distinct, func
import pandas as pd

router = APIRouter(prefix="/simulations", tags=["simulations"])

# How many of this account's replay-tick rows the daily-service read scans for
# the session's last outcome. One row per day, so this is roughly a month.
DAILY_SERVICE_LEDGER_SCAN = 30


def _get_version_with_ownership(version_id: UUID, db: Session, current_user: User) -> ExperimentVersion:
    version = db.query(ExperimentVersion).filter(ExperimentVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    experiment = db.query(Experiment).filter(
        Experiment.id == version.experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=403, detail="Not authorized")
    return version


def _get_session_with_ownership(session_id: UUID, db: Session, current_user: User) -> SimulationSession:
    session = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Simulation session not found")
    if session.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return session


def _load_stored_ohlcv(symbol: str, start_date: str, end_date: str, db: Session, provider_id: str = None, *, interval_minutes: int = None) -> pd.DataFrame:
    """Load OHLCV bars from stored market data.
    
    Args:
        symbol: Instrument ID (e.g., 'US:AAPL')
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        db: Database session
        provider_id: Provider filter (e.g., 'twelvedata', 'demo'). If None, loads all providers.
        interval_minutes: Read only bars written with this grain. A daily
            series shares midnight with four-hour bars, so without this the
            frame would mix two frequencies. None keeps whatever is stored,
            which is what a session whose spec never declared a grain reads.
    """
    query = db.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == symbol,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= end_date,
    )
    
    if provider_id:
        query = query.filter(MarketDataBar.provider_id == provider_id)

    if interval_minutes is not None:
        query = query.filter(MarketDataBar.interval_minutes == interval_minutes)
    
    bars = query.order_by(MarketDataBar.bar_date).all()
    
    if not bars:
        return pd.DataFrame()
    
    data = []
    for bar in bars:
        data.append({
            'date': bar.bar_date.isoformat() if hasattr(bar.bar_date, 'isoformat') else str(bar.bar_date),
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            # A crypto row keeps its exact amount in ``volume_decimal`` and
            # leaves the integer ``volume`` NULL. Turning that NULL into 0
            # would report a fabricated zero-volume trade.
            'volume': bar.volume if bar.volume is not None else bar.volume_decimal,
        })
    
    df = pd.DataFrame(data)
    df.set_index('date', inplace=True)
    return df


def _get_stored_trade_dates(symbol: str, start_date: str, end_date: str, db: Session, provider_id: str = None) -> list:
    """Get available trade dates from stored market data.
    
    Args:
        symbol: Instrument ID (e.g., 'US:AAPL')
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        db: Database session
        provider_id: Provider filter (e.g., 'twelvedata', 'demo'). If None, loads all providers.
    """
    query = db.query(MarketDataBar.bar_date).filter(
        MarketDataBar.instrument_id == symbol,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= end_date,
    )
    
    if provider_id:
        query = query.filter(MarketDataBar.provider_id == provider_id)
    
    bars = query.order_by(MarketDataBar.bar_date).distinct().all()
    
    return [bar.bar_date.isoformat() if hasattr(bar.bar_date, 'isoformat') else str(bar.bar_date) for bar in bars]


def _replay_engine_version(session: SimulationSession) -> str:
    """The engine version that prices this session's decisions.

    Both the market-clock advance and a manual intent are stamped with this so
    one session never reports two engines. ``simulation_policy_version`` is a
    separate, session-level field and is not this value.
    """
    if _is_intraday_timeframe(session.timeframe):
        return INTRADAY_ENGINE_VERSION
    return ENGINE_VERSION


def _build_simulator(session: SimulationSession, version: ExperimentVersion) -> ForwardSimulator:
    spec = version.strategy_spec
    return ForwardSimulator(
        strategy_spec=spec,
        initial_cash=session.initial_virtual_cash,
        commission_rate=spec.get("commission_rate", 0.0),
        slippage_rate=spec.get("slippage_rate", 0.0),
        dataset_cutoff_at=session.data_cutoff_at,
        engine_version=_replay_engine_version(session),
    )


def _frame_upto(ohlcv_df: pd.DataFrame, upto_date: str, start_date: str) -> pd.DataFrame:
    """The session's stored bars from ``start_date`` through ``upto_date``, inclusive.

    ``_load_stored_ohlcv`` indexes by ISO date string, so the engine gets the same
    shape it got from the demo fixture: chronological rows whose ``iloc[-1]`` is the
    bar being observed. ISO dates sort lexicographically, so the bounds need no
    parsing.
    """
    if ohlcv_df.empty:
        return ohlcv_df
    window = ohlcv_df.loc[(ohlcv_df.index >= start_date) & (ohlcv_df.index <= upto_date)]
    return window.reset_index(drop=True)


def _rebuild_state(
    session: SimulationSession,
    version: ExperimentVersion,
    simulator: ForwardSimulator,
    ohlcv_df: pd.DataFrame,
):
    decisions = session.decisions
    start_date = version.strategy_spec["start_date"]

    for d in decisions:
        ohlcv = _frame_upto(ohlcv_df, d.snapshot_date, start_date)
        if not ohlcv.empty:
            simulator.step(ohlcv, d.snapshot_date)


def _get_next_stored_snapshot_date(ohlcv_df: pd.DataFrame, current_snapshot_date: str, start_date: str, end_date: str) -> str:
    """Get next snapshot date from stored data."""
    all_dates = sorted(ohlcv_df.index.tolist())
    eligible = [d for d in all_dates if d > current_snapshot_date and start_date <= d <= end_date]
    if not eligible:
        return None
    return eligible[0]


def _session_timeframe(version: ExperimentVersion) -> str:
    """The timeframe a version's spec declares. Absent/'1d' means the daily path."""
    return str((version.strategy_spec or {}).get("timeframe", "1d"))


def _declared_daily_interval_minutes(strategy_spec: Optional[Dict[str, Any]]) -> Optional[int]:
    """The strict daily grain, but only for a spec that declares ``1d`` itself.

    ``_session_timeframe`` reports '1d' as a *default* for versions written
    before the key existed, and their stored bars carry no grain. Asking those
    for ``interval_minutes = 1440`` would empty a replay that works today, so
    an absent or unrecognised declaration keeps the unlabelled read rather than
    inventing a grain the writer never stamped.
    """
    raw = (strategy_spec or {}).get("timeframe")
    if isinstance(raw, str) and raw.strip().lower() == "1d":
        return DAILY_INTERVAL_MINUTES
    return None


def _is_intraday_timeframe(timeframe: Optional[str]) -> bool:
    return timeframe in INTRADAY_TIMEFRAMES


def _load_bound_intraday_bars(
    db: Session, *, symbol: str, provider_id: str, start_date: str, end_date: str
) -> List[Dict[str, Any]]:
    """The one dataset an intraday session is bound to, in chronological order.

    Filtered by instrument + provider_id + date range and ordered by bar_timestamp,
    so create-time validation and per-bar advance read literally the same rows: a
    session can never validate against stored bars and then silently replay a
    different fixture. provider_id is the timeframe isolation key (MarketDataBar
    has no timeframe column), so an intraday provider's bars never mix with a daily
    provider's for the same instrument.

    Each bar keeps its real datetime timestamp — never reduced to bar_date — so a
    daily bar cannot be mistaken for a 5-minute bar and ordering/time survive.
    """
    rows = (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == symbol,
            MarketDataBar.provider_id == provider_id,
            MarketDataBar.bar_date >= start_date,
            MarketDataBar.bar_date <= end_date,
        )
        .order_by(MarketDataBar.bar_timestamp)
        .all()
    )
    return [
        {
            "timestamp": r.bar_timestamp,
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume or 0),
        }
        for r in rows
    ]


def _assert_intraday_granularity(bars: List[Dict[str, Any]], timeframe: str) -> None:
    """Refuse to replay daily bars as an intraday timeframe.

    A bound set that carries only one bar per calendar date (or every timestamp at
    midnight) is daily data; feeding it to a 5-minute engine would fabricate an
    intraday session that never existed.
    """
    if not bars:
        return
    per_date: Dict[Any, int] = {}
    for b in bars:
        per_date[b["timestamp"].date()] = per_date.get(b["timestamp"].date(), 0) + 1
    max_bars_in_a_day = max(per_date.values())
    all_midnight = all(
        (b["timestamp"].hour, b["timestamp"].minute, b["timestamp"].second) == (0, 0, 0)
        for b in bars
    )
    if max_bars_in_a_day <= 1 or all_midnight:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Bound stored data for this session is daily (max {max_bars_in_a_day} "
                f"bar/date), not '{timeframe}'. Intraday replay requires intraday bars "
                f"from a provider seeded at '{timeframe}' granularity."
            ),
        )


def _serialized_quantity(value: Any) -> Any:
    """Whole units as ints; a fractionally traded position keeps its decimals."""
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _adapt_intraday_txn(
    txn: Dict[str, Any], bar: Dict[str, Any], dataset_cutoff_at: str, timeframe: str
) -> StepResult:
    """Map one intraday engine transaction into the daily StepResult contract.

    Reusing StepResult means the intraday path books into the paper portfolio
    through the exact same apply_rule_decision/_book_rule_trade code the daily path
    uses — one fill convention, no second implementation to drift.
    """
    side = txn["side"]
    exit_reason = txn.get("exit_reason")
    if side == "BUY":
        decision_type = "simulated_entry"
    elif exit_reason in ("session_end", "end_of_data"):
        decision_type = "forced_exit"
    else:
        decision_type = "simulated_exit"

    close = float(bar["close"])
    shares_after = _serialized_quantity(txn["shares_after"])
    cash_after = float(txn["cash_after"])
    exec_price = round(float(txn["execution_price_before_costs"]), 4)

    position = None
    if shares_after > 0:
        position = {
            "symbol": txn["symbol"],
            "shares": shares_after,
            "entry_price": exec_price,
            "entry_date": bar["timestamp"].date().isoformat(),
            "unrealized_pnl": round((close - exec_price) * shares_after, 4),
        }

    detail = {
        "symbol": txn["symbol"],
        "side": side,
        "quantity": _serialized_quantity(txn["quantity"]),
        "observed_price": round(float(txn["observed_price"]), 4),
        "execution_price": exec_price,
        "slippage_amount": txn.get("slippage_amount"),
        "commission": txn.get("commission_amount"),
        "signal_date": txn.get("signal_date"),
        "rationale": txn.get("rationale", ""),
        "exit_reason": exit_reason,
        "bar_timestamp": bar["timestamp"].isoformat(),
        "timeframe": timeframe,
    }

    return StepResult(
        decision_type=decision_type,
        decision_detail=detail,
        paper_cash=cash_after,
        paper_position=position,
        paper_equity=round(cash_after + shares_after * close, 4),
        warnings=[],
        snapshot_date=bar["timestamp"].date().isoformat(),
        data_cutoff_at=dataset_cutoff_at,
        engine_version=INTRADAY_ENGINE_VERSION,
    )


def _resolve_session_provider(session: SimulationSession, db: Session) -> str:
    """Resolve the provider_id for a session.
    
    For sessions with explicit provider_id, use that.
    For legacy sessions without provider_id, infer from available stored data.
    """
    if session.provider_id:
        return session.provider_id
    
    # Legacy session: infer provider from stored data
    # Use the provider that has data for the session's date range
    spec = session.experiment_version.strategy_spec
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]
    end_date = session.data_cutoff_at
    
    # Query for available providers
    from sqlalchemy import distinct
    providers = db.query(distinct(MarketDataBar.provider_id)).filter(
        MarketDataBar.instrument_id == symbol,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= end_date,
    ).all()
    
    providers = [p[0] for p in providers]
    
    # Prefer twelvedata over demo if both exist
    if 'twelvedata' in providers:
        return 'twelvedata'
    elif 'demo' in providers:
        return 'demo'
    elif providers:
        return providers[0]
    else:
        # No stored data; return None to let caller handle
        return None


@router.get("/versions/{version_id}/available-providers")
def get_available_providers(
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get available stored-data providers for an experiment version.
    
    Returns a list of providers that have stored data for the instrument
    in the version's date range, along with coverage information.
    """
    version = _get_version_with_ownership(version_id, db, current_user)
    
    spec = version.strategy_spec
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]
    end_date = spec.get("end_date", version.dataset_cutoff_at)
    
    # Query for available providers with coverage info
    providers = db.query(
        MarketDataBar.provider_id,
        func.count(MarketDataBar.id).label('bar_count'),
        func.min(MarketDataBar.bar_date).label('min_date'),
        func.max(MarketDataBar.bar_date).label('max_date'),
    ).filter(
        MarketDataBar.instrument_id == symbol,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= end_date,
    ).group_by(MarketDataBar.provider_id).all()
    
    available = []
    for p in providers:
        available.append({
            'provider_id': p.provider_id,
            'bar_count': p.bar_count,
            'min_date': p.min_date.isoformat() if hasattr(p.min_date, 'isoformat') else str(p.min_date),
            'max_date': p.max_date.isoformat() if hasattr(p.max_date, 'isoformat') else str(p.max_date),
            'requested_start': start_date,
            'requested_end': end_date,
        })
    
    return {
        'symbol': symbol,
        'requested_start': start_date,
        'requested_end': end_date,
        'available_providers': available,
    }


@router.get("/versions/{version_id}/sessions", response_model=List[SimulationSessionResponse])
def list_sessions_for_version(
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the caller's replay sessions for one experiment version, newest first."""
    _get_version_with_ownership(version_id, db, current_user)

    return (
        db.query(SimulationSession)
        .filter(
            SimulationSession.experiment_version_id == version_id,
            SimulationSession.owner_id == current_user.id,
        )
        .order_by(SimulationSession.created_at.desc())
        .all()
    )


@router.post("/versions/{version_id}", response_model=SimulationSessionResponse, status_code=status.HTTP_201_CREATED)
def create_simulation(
    version_id: UUID,
    payload: SimulationSessionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    version = _get_version_with_ownership(version_id, db, current_user)

    if version.locked_at is None:
        raise HTTPException(status_code=400, detail="Version must be locked before starting simulation")

    spec = version.strategy_spec
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]
    cutoff = spec.get("end_date", version.dataset_cutoff_at)
    timeframe = _session_timeframe(version)

    # Query for available providers with stored data
    providers = db.query(
        MarketDataBar.provider_id,
        func.count(MarketDataBar.id).label('bar_count'),
        func.min(MarketDataBar.bar_date).label('min_date'),
        func.max(MarketDataBar.bar_date).label('max_date'),
    ).filter(
        MarketDataBar.instrument_id == symbol,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= cutoff,
    ).group_by(MarketDataBar.provider_id).all()

    if not providers:
        raise HTTPException(
            status_code=400,
            detail=f"No stored data available for {symbol} in range {start_date} to {cutoff}"
        )

    # Determine which provider to use
    provider_id = payload.provider_id
    
    if provider_id:
        # Explicit provider specified: validate it has data
        provider_data = next((p for p in providers if p.provider_id == provider_id), None)
        if not provider_data:
            available = [p.provider_id for p in providers]
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{provider_id}' has no stored data for {symbol}. Available: {available}"
            )
    else:
        # No explicit provider: if only one available, use it; otherwise require selection
        if len(providers) == 1:
            provider_id = providers[0].provider_id
        else:
            available = [p.provider_id for p in providers]
            raise HTTPException(
                status_code=400,
                detail=f"Multiple providers have stored data for {symbol}: {available}. "
                       f"Please specify provider_id in request."
            )

    # An intraday session binds to exactly one provider's stored bars at creation
    # time; the same query the advance path reads. Validate here so a session can
    # never be created that would later replay daily bars as if they were 5-minute.
    if _is_intraday_timeframe(timeframe):
        bound = _load_bound_intraday_bars(
            db, symbol=symbol, provider_id=provider_id, start_date=start_date, end_date=cutoff
        )
        if not bound:
            raise HTTPException(
                status_code=400,
                detail=f"No stored '{timeframe}' bars for {symbol} from provider '{provider_id}' "
                       f"in range {start_date} to {cutoff}.",
            )
        _assert_intraday_granularity(bound, timeframe)

    session = SimulationSession(
        experiment_version_id=version.id,
        owner_id=current_user.id,
        initial_virtual_cash=payload.initial_virtual_cash,
        data_cutoff_at=cutoff,
        status=SimulationStatus.pending,
        simulation_policy_version=SIMULATION_ENGINE_VERSION,
        provider_id=provider_id,
        provider_confirmed=True,  # Explicit selection confirmed
        paper_equity=payload.initial_virtual_cash,
        total_decisions=0,
        observed_days=0,
        timeframe=timeframe,
    )
    db.add(session)
    db.flush()

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_created", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "version_id": str(version.id),
            "initial_virtual_cash": payload.initial_virtual_cash,
            "data_cutoff_at": cutoff,
            "provider_id": provider_id,
        },
        rules_locked=True,
        data_cutoff_at=cutoff,
    )

    db.commit()
    db.refresh(session)
    return session


@router.get("/{session_id}", response_model=SimulationSessionResponse)
def get_simulation(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_session_with_ownership(session_id, db, current_user)


def _book_rule_trade(
    db: Session,
    *,
    session: SimulationSession,
    version: ExperimentVersion,
    result,
    step_number: int,
    actor_id: UUID,
    bar_timestamp=None,
) -> None:
    """Mirror one replay step into the paper portfolio linked to this session.

    The simulation decision is the replay's own record; the paper portfolio is
    the account. Booking both from the same step is what lets a rule fire and
    move cash, position and journal without anyone placing the order by hand.
    A session with no linked portfolio stays a pure replay.
    """
    portfolio = (
        db.query(PaperPortfolio)
        .filter(
            PaperPortfolio.simulation_session_id == session.id,
            PaperPortfolio.owner_id == session.owner_id,
        )
        .first()
    )
    if portfolio is None:
        return

    spec = version.strategy_spec or {}
    applied = apply_rule_decision(
        db,
        portfolio=portfolio,
        session_id=session.id,
        step_number=step_number,
        strategy_version_id=version.id,
        result=result,
        # The same rate the simulator priced the decision with, so the account
        # and the replay cannot disagree about what the trade cost.
        commission_rate=spec.get("commission_rate", 0.0),
        bar_timestamp=bar_timestamp,
        # The account is bound to the locked spec's instruments; the shared
        # policy may narrow that further but no lane may widen it.
        allowed_instruments=spec.get("asset_ids"),
        provider_id=session.provider_id,
    )
    if applied is None:
        return

    filled = applied.get("status") == "filled"
    emit_event(
        db,
        entity_type="paper_portfolio",
        entity_id=portfolio.id,
        event_type="rule_order_executed" if filled else "rule_order_blocked",
        actor_id=actor_id,
        actor_type="system",
        rules_locked=True,
        data_cutoff_at=getattr(result, "snapshot_date", None),
        payload={
            "simulation_session_id": str(session.id),
            "step_number": step_number,
            "applied": applied,
        },
    )


def _held_quantity(
    db: Session, portfolio: PaperPortfolio, asset_id: str
) -> float:
    """What the account actually holds of one asset right now."""
    position = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.portfolio_id == portfolio.id,
            PaperPosition.asset_id == asset_id,
            PaperPosition.status == PositionStatus.open,
        )
        .first()
    )
    return float(position.quantity) if position else 0.0


def _execute_manual_orders(
    db: Session,
    *,
    session: SimulationSession,
    version: ExperimentVersion,
    decision: SimulationDecision,
    simulator: ForwardSimulator,
    execution_date: str,
    observed_open: float,
    close_price: float,
    provider_id: Optional[str],
    actor_id: UUID,
    bar_timestamp=None,
) -> List[Dict[str, Any]]:
    """Run this session's pending manual orders against the bar just reached.

    The replay advancing onto a bar IS the market opening, so a manual order
    submitted at an earlier cutoff fills here at this bar's open — never in the
    request that placed it. Each fill moves both books on the same bar and at the
    same price: the account through ``book_fill``, and the replay's own simulator
    here, so a pending order cannot inflate the account while the strategy book
    ignores it, and an executed one cannot appear in one book only.

    A session with no linked portfolio stays a pure replay, exactly like the rule
    path: there is no account to hold an intent.
    """
    portfolio = _session_portfolio(db, session)
    if portfolio is None:
        return []

    spec = version.strategy_spec or {}
    results = execute_due_orders(
        db,
        portfolio=portfolio,
        instrument_id=spec["asset_ids"][0],
        execution_date=execution_date,
        observed_open=observed_open,
        commission_rate=float(spec.get("commission_rate", 0.0)),
        slippage_rate=float(spec.get("slippage_rate", 0.0)),
        provider_id=provider_id,
        bar_timestamp=bar_timestamp,
        actor_id=actor_id,
    )

    executed = [r for r in results if r.get("executed")]
    if executed:
        _apply_manual_fills(
            simulator,
            [
                {
                    "side": r["side"],
                    "quantity": r["quantity"],
                    "price": r["execution_price"],
                    "net_cash_change": r["net_cash_change"],
                    "execution_date": r["execution_date"],
                }
                for r in executed
            ],
        )
        decision.paper_cash = round(float(simulator.cash), 4)
        decision.paper_position = simulator._position_dict(close_price)
        decision.paper_equity = round(float(simulator._current_equity(close_price)), 4)
        decision.decision_detail = {
            **(decision.decision_detail or {}),
            "manual_fills": executed,
        }
        session.paper_equity = decision.paper_equity
        session.current_position_json = decision.paper_position

        emit_event(
            db,
            entity_type="simulation",
            entity_id=session.id,
            event_type="manual_orders_executed",
            actor_id=actor_id,
            actor_type="system",
            rules_locked=True,
            data_cutoff_at=execution_date,
            payload={
                "execution_date": execution_date,
                "fills": [
                    {
                        "order_id": r["order_id"],
                        "fill_id": r.get("fill_id"),
                        "side": r["side"],
                        "quantity": r["quantity"],
                        "execution_price": r["execution_price"],
                        "settlement_date": r["settlement_date"],
                    }
                    for r in executed
                ],
            },
        )

    blocked = [r for r in results if not r.get("executed")]
    if blocked:
        emit_event(
            db,
            entity_type="simulation",
            entity_id=session.id,
            event_type="manual_orders_blocked",
            actor_id=actor_id,
            actor_type="system",
            rules_locked=True,
            data_cutoff_at=execution_date,
            payload={"execution_date": execution_date, "blocked": blocked},
        )

    return results


def _apply_manual_fills(
    simulator: ForwardSimulator,
    fills: List[Dict[str, Any]],
) -> None:
    """Apply already-booked manual fills to the replay's own book.

    The cash movement is the account's own figure, not recomputed here, so the
    two books cannot drift. Position averaging mirrors the ledger's, and each
    fill carries the date it filled on.
    """
    for fill in fills:
        quantity = float(fill["quantity"])
        price = float(fill["price"])
        execution_date = fill["execution_date"]
        if fill["side"] == OrderSide.BUY.value:
            held = float(simulator.shares)
            if held > 0 and simulator.entry_price is not None:
                simulator.entry_price = round(
                    (float(simulator.entry_price) * held + price * quantity)
                    / (held + quantity),
                    4,
                )
            else:
                simulator.entry_price = price
                simulator.entry_date = execution_date
            simulator.shares = round_quantity(
                held + quantity, simulator.quantity_precision
            )
            simulator._bought = simulator.shares > 0
        else:
            remaining = round_quantity(
                float(simulator.shares) - quantity, simulator.quantity_precision
            )
            simulator.shares = remaining if remaining > 0 else 0
            simulator._bought = simulator.shares > 0
            if simulator.shares == 0:
                simulator.entry_price = None
                simulator.entry_date = None
        simulator.cash = round(float(simulator.cash) + float(fill["net_cash_change"]), 4)


def _session_portfolio(
    db: Session, session: SimulationSession
) -> Optional[PaperPortfolio]:
    """The account bound to a session, or None when the session is a pure replay."""
    return (
        db.query(PaperPortfolio)
        .filter(
            PaperPortfolio.simulation_session_id == session.id,
            PaperPortfolio.owner_id == session.owner_id,
        )
        .first()
    )


MANUAL_FILL_REASONS = ("manual_buy", "manual_sell")


def _manual_fill_rows(
    db: Session, portfolio: Optional[PaperPortfolio]
) -> List[Dict[str, Any]]:
    """This account's executed manual fills, oldest first.

    ``execution_date`` sorts before ``bar_timestamp`` on purpose: a daily fill
    has no bar timestamp, and NULL placement is dialect-dependent, so the one
    order both databases agree on is date first, bar second.
    """
    if portfolio is None:
        return []
    rows = (
        db.query(PaperFill, PaperOrder)
        .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperFill.simulated_fill_reason.in_(MANUAL_FILL_REASONS),
        )
        .order_by(
            PaperFill.execution_date,
            PaperFill.bar_timestamp,
            PaperFill.paper_order_id,
        )
        .all()
    )
    return [
        {
            "side": order.side.value,
            "quantity": float(fill.quantity),
            "price": float(fill.execution_price_before_costs),
            "net_cash_change": float(fill.net_cash_change),
            "execution_date": fill.execution_date,
            "bar_timestamp": fill.bar_timestamp,
        }
        for fill, order in rows
    ]


def _manual_fills_by_bar(
    db: Session, portfolio: Optional[PaperPortfolio]
) -> Dict[str, List[Dict[str, Any]]]:
    """Executed manual fills keyed by the intraday bar they filled on.

    An intraday replay rebuilds itself from bar zero on every advance, so the
    fills the account already booked have to be re-adopted in the same order they
    happened or the replay would forget trades the account remembers.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for fill in _manual_fill_rows(db, portfolio):
        if fill["bar_timestamp"] is None:
            continue
        grouped.setdefault(fill["bar_timestamp"].isoformat(), []).append(fill)
    return grouped


def _manual_fills_before(
    db: Session, portfolio: Optional[PaperPortfolio], before_date: str
) -> List[Dict[str, Any]]:
    """Executed manual fills the account booked on bars before ``before_date``.

    The daily replay rebuilds its simulator from the rule history only, so a
    position the account bought two bars ago has to be re-adopted here as well —
    otherwise the replay would show cash that the ledger already spent.
    """
    return [f for f in _manual_fill_rows(db, portfolio) if f["execution_date"] < before_date]


def _adopt_manual_fills(
    replay: OpeningRangeForwardReplay,
    grouped: Dict[str, List[Dict[str, Any]]],
    bar_timestamp,
) -> None:
    for fill in grouped.get(bar_timestamp.isoformat(), []):
        replay.adopt_fill(
            side=fill["side"],
            quantity=fill["quantity"],
            price=fill["price"],
            net_cash_change=fill["net_cash_change"],
            at=bar_timestamp,
        )


def _intraday_book(
    replay: OpeningRangeForwardReplay, *, symbol: str, target_date: str, close: float
):
    """The replay's cash, shares and position dict as of the bar just observed."""
    shares = _serialized_quantity(replay.shares)
    cash = round(float(replay.cash), 4)
    position = None
    if shares > 0:
        entry_price = replay.entry_price
        entry_ts = replay.entry_ts
        position = {
            "symbol": symbol,
            "shares": shares,
            "entry_price": entry_price,
            "entry_date": entry_ts.date().isoformat() if entry_ts else target_date,
            "unrealized_pnl": round((close - float(entry_price)) * shares, 4) if entry_price else 0.0,
        }
    return cash, shares, position


def _execute_intraday_manual_orders(
    db: Session,
    *,
    session: SimulationSession,
    version: ExperimentVersion,
    replay: OpeningRangeForwardReplay,
    portfolio: Optional[PaperPortfolio],
    execution_date: str,
    observed_open: float,
    bar_timestamp,
    provider_id: Optional[str],
    actor_id: UUID,
) -> List[Dict[str, Any]]:
    """Execute the pending manual orders whose window is this bar, then adopt
    what filled into the replay's own book so both books move on the same bar."""
    if portfolio is None:
        return []
    spec = version.strategy_spec or {}
    results = execute_due_orders(
        db,
        portfolio=portfolio,
        instrument_id=spec["asset_ids"][0],
        execution_date=execution_date,
        observed_open=observed_open,
        commission_rate=float(spec.get("commission_rate", 0.0)),
        slippage_rate=float(spec.get("slippage_rate", 0.0)),
        provider_id=provider_id,
        bar_timestamp=bar_timestamp,
        actor_id=actor_id,
    )
    executed = [r for r in results if r.get("executed")]
    for fill in executed:
        replay.adopt_fill(
            side=fill["side"],
            quantity=fill["quantity"],
            price=fill["execution_price"],
            net_cash_change=fill["net_cash_change"],
            at=bar_timestamp,
        )
    if executed:
        emit_event(
            db,
            entity_type="simulation",
            entity_id=session.id,
            event_type="manual_orders_executed",
            actor_id=actor_id,
            actor_type="system",
            rules_locked=True,
            data_cutoff_at=execution_date,
            payload={
                "execution_date": execution_date,
                "bar_timestamp": bar_timestamp.isoformat() if bar_timestamp else None,
                "fills": [
                    {
                        "order_id": r["order_id"],
                        "fill_id": r.get("fill_id"),
                        "side": r["side"],
                        "quantity": r["quantity"],
                        "execution_price": r["execution_price"],
                        "settlement_date": r["settlement_date"],
                    }
                    for r in executed
                ],
            },
        )
    return results


def _expire_session_pending_orders(
    db: Session,
    *,
    session: SimulationSession,
    actor_id: UUID,
    reason: str,
) -> List[Dict[str, Any]]:
    """Expire the manual orders this session's clock will never reach.

    Once the bound dataset is exhausted there is no next bar, so a pending order
    has no execution window left in this replay. Silently leaving it pending —
    or filling it at the last price the user already saw — would both invent a
    trade and strand an intent that can never settle.
    """
    portfolios = (
        db.query(PaperPortfolio)
        .filter(
            PaperPortfolio.simulation_session_id == session.id,
            PaperPortfolio.owner_id == session.owner_id,
        )
        .all()
    )
    expired: List[Dict[str, Any]] = []
    for portfolio in portfolios:
        expired.extend(
            expire_pending_orders(
                db, portfolio=portfolio, actor_id=actor_id, reason=reason
            )
        )
    return expired


def _advance_intraday(
    db: Session,
    *,
    session: SimulationSession,
    version: ExperimentVersion,
    current_user: User,
    actor_type: str = "unknown",
) -> SimulationAdvanceResponse:
    """Advance an intraday session by exactly one stored bar.

    Reads the SAME bound dataset (instrument + provider_id + date range, ordered
    by bar_timestamp) that create_simulation validated, so a session can never
    validate against stored bars and then replay a different fixture. State is
    rebuilt deterministically from initial_virtual_cash over every bar strictly
    before the target, then the target bar is pushed with its lookahead next bar
    so the session-end rule can tell whether it closes the day. Each transaction
    the engine books on the target bar becomes one SimulationDecision and is
    mirrored into the linked paper portfolio through the same _book_rule_trade
    path the daily replay uses — one fill convention, no second implementation.
    """
    spec = version.strategy_spec or {}
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]
    cutoff = session.data_cutoff_at or spec.get("end_date", version.dataset_cutoff_at)

    provider_id = session.provider_id or _resolve_session_provider(session, db)
    if not provider_id:
        raise HTTPException(status_code=400, detail=f"No provider bound to this '{session.timeframe}' session")

    bars = _load_bound_intraday_bars(
        db, symbol=symbol, provider_id=provider_id, start_date=start_date, end_date=cutoff
    )
    if not bars:
        raise HTTPException(
            status_code=400,
            detail=f"No stored '{session.timeframe}' bars for {symbol} from provider '{provider_id}' "
                   f"in range {start_date} to {cutoff}.",
        )
    _assert_intraday_granularity(bars, session.timeframe)

    cursor = session.current_bar_timestamp
    if cursor is None:
        target_idx = 0
    else:
        target_idx = next((i for i, b in enumerate(bars) if b["timestamp"] > cursor), None)

    if target_idx is None:
        _expire_session_pending_orders(
            db, session=session, actor_id=current_user.id, reason="dataset_exhausted"
        )
        session.status = SimulationStatus.completed
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        emit_event(
            db,
            entity_type="simulation", entity_id=session.id,
            event_type="simulation_completed", actor_id=current_user.id,
            actor_type=actor_type,
            payload={"total_decisions": session.total_decisions, "observed_days": session.observed_days},
            rules_locked=True,
            data_cutoff_at=session.data_cutoff_at,
        )
        db.commit()
        db.refresh(session)
        raise HTTPException(status_code=400, detail="No more bars available — simulation complete")

    config = parse_intraday_config(spec)
    commission_rate = spec.get("commission_rate", 0.0)
    slippage_rate = spec.get("slippage_rate", 0.0)

    replay = OpeningRangeForwardReplay(
        symbol=symbol,
        config=config,
        initial_cash=session.initial_virtual_cash,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )
    portfolio = _session_portfolio(db, session)
    # Deterministically rebuild state over every bar before the target. Their
    # transactions were already booked on prior advances, so they are discarded
    # here; only the target bar's transactions are new. Manual fills rebuild the
    # same way: the account kept them, so each one is re-adopted on the bar that
    # filled it, or the replay would forget a trade the ledger still shows.
    manual_by_bar = _manual_fills_by_bar(db, portfolio)
    for j in range(target_idx):
        replay.push_bar(bars[j], bars[j + 1])
        _adopt_manual_fills(replay, manual_by_bar, bars[j]["timestamp"])

    target = bars[target_idx]
    next_bar = bars[target_idx + 1] if target_idx + 1 < len(bars) else None
    new_txns = replay.push_bar(target, next_bar)

    if session.status == SimulationStatus.pending:
        session.status = SimulationStatus.active
        session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    target_ts = target["timestamp"]
    target_date = target_ts.date().isoformat()
    close = float(target["close"])

    step_number = session.total_decisions
    last_decision = None

    if not new_txns:
        # Observed a bar with no rule action: still record it so the replay cursor
        # and the journal advance, mirroring the daily no_action step.
        step_number += 1
        cash, shares, position = _intraday_book(
            replay, symbol=symbol, target_date=target_date, close=close
        )
        result = StepResult(
            decision_type="no_action",
            decision_detail={
                "symbol": symbol,
                "side": "HOLD",
                "rationale": "No opening-range signal on this bar",
                "bar_timestamp": target_ts.isoformat(),
                "timeframe": session.timeframe,
            },
            paper_cash=cash,
            paper_position=position,
            paper_equity=round(cash + shares * close, 4),
            warnings=list(replay.warnings),
            snapshot_date=target_date,
            data_cutoff_at=session.data_cutoff_at,
            engine_version=INTRADAY_ENGINE_VERSION,
        )
        last_decision = _record_intraday_decision(
            db, session=session, result=result, step_number=step_number, bar_timestamp=target_ts
        )
        _book_rule_trade(
            db, session=session, version=version, result=result,
            step_number=step_number, actor_id=current_user.id, bar_timestamp=target_ts,
        )
        session.paper_equity = result.paper_equity
        session.current_position_json = position
    else:
        for txn in new_txns:
            step_number += 1
            result = _adapt_intraday_txn(txn, target, session.data_cutoff_at, session.timeframe)
            last_decision = _record_intraday_decision(
                db, session=session, result=result, step_number=step_number, bar_timestamp=target_ts
            )
            _book_rule_trade(
                db, session=session, version=version, result=result,
                step_number=step_number, actor_id=current_user.id, bar_timestamp=target_ts,
            )
        cash, shares, position = _intraday_book(
            replay, symbol=symbol, target_date=target_date, close=close
        )
        session.paper_equity = round(cash + shares * close, 4)
        session.current_position_json = position

    # The market clock for manual orders: reaching this bar IS the opening of the
    # first window after an earlier cutoff, so a pending manual trade fills here
    # at this bar's open — not in the request that submitted it.
    manual_results = _execute_intraday_manual_orders(
        db,
        session=session,
        version=version,
        replay=replay,
        portfolio=portfolio,
        execution_date=target_date,
        observed_open=float(target["open"]),
        bar_timestamp=target_ts,
        provider_id=provider_id,
        actor_id=current_user.id,
    )
    executed_manual = [r for r in manual_results if r.get("executed")]
    if executed_manual:
        cash, shares, position = _intraday_book(
            replay, symbol=symbol, target_date=target_date, close=close
        )
        session.paper_equity = round(cash + shares * close, 4)
        session.current_position_json = position
        if last_decision is not None:
            last_decision.paper_cash = cash
            last_decision.paper_position = position
            last_decision.paper_equity = session.paper_equity
            last_decision.decision_detail = {
                **(last_decision.decision_detail or {}),
                "manual_fills": executed_manual,
            }

    session.current_bar_timestamp = target_ts
    session.current_snapshot_date = target_date
    session.total_decisions = step_number
    session.observed_days = session.observed_days + 1
    if replay.warnings:
        session.warnings = (session.warnings or []) + [w for w in replay.warnings if w not in (session.warnings or [])]
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_advanced", actor_id=current_user.id,
        actor_type=actor_type,
        payload={
            "step_number": step_number,
            "snapshot_date": target_date,
            "bar_timestamp": target_ts.isoformat(),
            "timeframe": session.timeframe,
            "transactions": len(new_txns),
            "paper_equity": session.paper_equity,
        },
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )

    db.commit()
    db.refresh(session)
    db.refresh(last_decision)
    return SimulationAdvanceResponse(session=session, decision=last_decision)


def _record_intraday_decision(
    db: Session, *, session: SimulationSession, result: StepResult, step_number: int, bar_timestamp: datetime
) -> SimulationDecision:
    decision = SimulationDecision(
        session_id=session.id,
        step_number=step_number,
        snapshot_date=result.snapshot_date,
        bar_timestamp=bar_timestamp,
        data_cutoff_at=result.data_cutoff_at,
        engine_version=result.engine_version,
        decision_type=DecisionType(result.decision_type),
        decision_detail=result.decision_detail,
        paper_cash=result.paper_cash,
        paper_position=result.paper_position,
        paper_equity=result.paper_equity,
        warnings=result.warnings if result.warnings else None,
    )
    db.add(decision)
    db.flush()
    return decision


def advance_daily_step(
    db: Session,
    *,
    session: SimulationSession,
    version: ExperimentVersion,
    actor_id: UUID,
    actor_type: str,
) -> dict:
    """Step one daily session forward, and say which of the two things happened.

    This is the daily branch of ``POST /{session_id}/advance`` moved out
    verbatim - same dataset, same cursor rule, same fills, same audit event -
    so that a background tick and the user's own button cannot drift into two
    replay implementations. It takes the actor as data instead of reading a
    ``Request``: a tick has no credential to verify, and the actor type it
    passes is the claim its caller can actually support.

    ``outcome`` is ``"advanced"`` when the cursor moved onto a stored bar and
    ``"completed"`` when the bound dataset ran out - the second is a state the
    session keeps, not a refusal, so a caller does not have to parse detail
    text to tell them apart. The refusals that really are refusals still raise,
    with the codes and wording the HTTP caller has always been given.
    ``decision`` is the row just written, or ``None`` when exhaustion closed the
    session with no open position to force-exit.
    """
    spec = version.strategy_spec
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]
    end_date = spec.get("end_date", version.dataset_cutoff_at)

    # One dataset for the whole loop: the workspace chart plots these rows, the
    # replay cursor walks them, and manual decisions already price against them.
    # Advancing off a separate fixture would fill a rule on bars the user never
    # saw, and a catalog instrument (US:AAPL, HK:0700) has no fixture rows at all,
    # so its replay would silently walk a different series' calendar.
    provider_id = session.provider_id or _resolve_session_provider(session, db)
    interval_minutes = _declared_daily_interval_minutes(spec)
    stored = (
        _load_stored_ohlcv(
            symbol,
            start_date,
            end_date,
            db,
            provider_id=provider_id,
            interval_minutes=interval_minutes,
        )
        if provider_id
        else pd.DataFrame()
    )
    if stored.empty:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Replay cannot find the bound dataset: {symbol} (provider "
                f"{provider_id or 'unbound'}) has no stored bars between "
                f"{start_date} and {end_date}. The chart, cursor and fills must all "
                "read the same dataset, so no other data source is substituted."
            ),
        )

    current_snapshot = session.current_snapshot_date
    if current_snapshot is None:
        next_date = stored.index.min()
    else:
        next_date = _get_next_stored_snapshot_date(stored, current_snapshot, start_date, end_date)

    if next_date is None:
        # Force-exit any open position at the last observed close
        if session.current_position_json is not None:
            last_snapshot = session.current_snapshot_date
            if last_snapshot:
                ohlcv = _frame_upto(stored, last_snapshot, start_date)
                if not ohlcv.empty:
                    simulator = _build_simulator(session, version)
                    _rebuild_state(session, version, simulator, stored)
                    result = simulator.force_exit(ohlcv, last_snapshot)

                    step_number = session.total_decisions + 1
                    decision = SimulationDecision(
                        session_id=session.id,
                        step_number=step_number,
                        snapshot_date=result.snapshot_date,
                        data_cutoff_at=result.data_cutoff_at,
                        engine_version=result.engine_version,
                        decision_type=DecisionType(result.decision_type),
                        decision_detail=result.decision_detail,
                        paper_cash=result.paper_cash,
                        paper_position=result.paper_position,
                        paper_equity=result.paper_equity,
                        warnings=result.warnings if result.warnings else None,
                    )
                    db.add(decision)

                    session.paper_equity = result.paper_equity
                    session.current_position_json = None
                    session.total_decisions = step_number
                    _book_rule_trade(
                        db,
                        session=session,
                        version=version,
                        result=result,
                        step_number=step_number,
                        actor_id=actor_id,
                    )
                    if result.warnings:
                        existing = session.warnings or []
                        session.warnings = existing + result.warnings

        # No next bar exists in the bound dataset, so any pending manual order
        # has lost its only window. Say so on the record rather than leaving it
        # pending forever or filling it at a price the user already watched.
        _expire_session_pending_orders(
            db, session=session, actor_id=actor_id, reason="dataset_exhausted"
        )

        session.status = SimulationStatus.completed
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        emit_event(
            db,
            entity_type="simulation", entity_id=session.id,
            event_type="simulation_completed", actor_id=actor_id,
            actor_type=actor_type,
            payload={"total_decisions": session.total_decisions, "observed_days": session.observed_days},
            rules_locked=True,
            data_cutoff_at=session.data_cutoff_at,
        )

        db.commit()
        db.refresh(session)
        return {
            "outcome": "completed",
            "step_number": session.total_decisions,
            "snapshot_date": session.current_snapshot_date,
            "decision": None,
        }

    ohlcv = _frame_upto(stored, next_date, start_date)
    if ohlcv.empty:
        raise HTTPException(
            status_code=400,
            detail=f"No stored bar for {symbol} (provider {provider_id}) up to {next_date}",
        )

    if session.status == SimulationStatus.pending:
        session.status = SimulationStatus.active
        session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    simulator = _build_simulator(session, version)
    _rebuild_state(session, version, simulator, stored)
    # The rule rebuild only replays rules. Manual fills the account booked on
    # earlier bars have to be re-adopted onto the same book, or the replay would
    # show cash it never spent while the ledger holds the position.
    _apply_manual_fills(
        simulator,
        _manual_fills_before(db, _session_portfolio(db, session), next_date),
    )

    result = simulator.step(ohlcv, next_date)

    step_number = session.total_decisions + 1
    decision = SimulationDecision(
        session_id=session.id,
        step_number=step_number,
        snapshot_date=result.snapshot_date,
        data_cutoff_at=result.data_cutoff_at,
        engine_version=result.engine_version,
        decision_type=DecisionType(result.decision_type),
        decision_detail=result.decision_detail,
        paper_cash=result.paper_cash,
        paper_position=result.paper_position,
        paper_equity=result.paper_equity,
        warnings=result.warnings if result.warnings else None,
    )
    db.add(decision)

    session.current_snapshot_date = next_date
    session.paper_equity = result.paper_equity
    session.current_position_json = result.paper_position
    session.total_decisions = step_number
    session.observed_days = session.observed_days + 1
    _book_rule_trade(
        db,
        session=session,
        version=version,
        result=result,
        step_number=step_number,
        actor_id=actor_id,
    )
    # The market clock: this bar's open is the first window after any earlier
    # cutoff, so manual orders placed while the user was watching an earlier bar
    # are executed here rather than in the request that submitted them.
    _execute_manual_orders(
        db,
        session=session,
        version=version,
        decision=decision,
        simulator=simulator,
        execution_date=next_date,
        observed_open=float(ohlcv.iloc[-1]["open"]),
        close_price=float(ohlcv.iloc[-1]["close"]),
        provider_id=provider_id,
        actor_id=actor_id,
    )
    if result.warnings:
        existing = session.warnings or []
        session.warnings = existing + result.warnings
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_advanced", actor_id=actor_id,
        actor_type=actor_type,
        payload={
            "step_number": step_number,
            "snapshot_date": next_date,
            "decision_type": result.decision_type,
            "paper_equity": result.paper_equity,
        },
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )

    db.commit()
    db.refresh(session)
    db.refresh(decision)

    return {
        "outcome": "advanced",
        "step_number": step_number,
        "snapshot_date": next_date,
        "decision": decision,
    }


@router.post("/{session_id}/advance", response_model=SimulationAdvanceResponse)
def advance_simulation(
    session_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Advance the session by exactly one step of its own bound dataset.

    A daily session moves to the next stored snapshot date — the next trading
    session present in the bound dataset, not necessarily the next calendar day.
    An intraday session moves to the next stored bar of its declared timeframe.
    Each call appends exactly one SimulationDecision and advances the cursor for
    good; no endpoint steps backward, so "one call = one step" is assertable and
    a retried call is a second step, not a replay.

    Status: `pending` becomes `active` on the first advance. Any status other
    than `pending` or `active` returns 400 and changes nothing.

    Dataset exhaustion also returns 400, but it is a state change, not a
    rejection: the session becomes `completed`, an open position is force-exited
    at the last observed close (daily) and pending manual orders are expired
    because their only window has passed. The HTTP layer has to keep reporting
    that on the code the client already handles, so this endpoint turns the
    `advance_daily_step` outcome back into a 400; a programmatic caller reads
    the structured outcome instead of the detail text.
    """
    session = _get_session_with_ownership(session_id, db, current_user)

    if session.status not in (SimulationStatus.pending, SimulationStatus.active):
        raise HTTPException(status_code=400, detail=f"Cannot advance session in '{getattr(session.status, 'value', session.status)}' status")

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == session.experiment_version_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Associated version not found")

    # Explicit timeframe branch: an intraday session advances one bar at a time
    # over its own bound stored dataset. The daily path below advances one calendar
    # day, also over the session's own stored dataset — a session's stored
    # timeframe, set at creation, decides which path runs, so "推進一日" is never
    # silently turned into "推進一根".
    if _is_intraday_timeframe(session.timeframe):
        return _advance_intraday(
            db,
            session=session,
            version=version,
            current_user=current_user,
            actor_type=request_actor_provenance(request),
        )

    outcome = advance_daily_step(
        db,
        session=session,
        version=version,
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
    )
    if outcome["outcome"] == "completed":
        raise HTTPException(
            status_code=400,
            detail="No more snapshots available — simulation complete",
        )

    return SimulationAdvanceResponse(session=session, decision=outcome["decision"])


@router.get("/{session_id}/decisions", response_model=List[SimulationDecisionResponse])
def list_decisions(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(session_id, db, current_user)
    return session.decisions


@router.get("/{session_id}/orders", response_model=List[PaperOrderLifecycleResponse])
def list_session_orders(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """This replay's paper orders, each with the window it is waiting for.

    A pending order is the result of a submission now, so the workspace has to
    show it — with the bar it will execute on, or the reason it cannot — before
    ``advance`` turns it into a fill.
    """
    session = _get_session_with_ownership(session_id, db, current_user)
    portfolio = _session_portfolio(db, session)
    if portfolio is None:
        return []

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == session.experiment_version_id
    ).first()
    spec = (version.strategy_spec or {}) if version else {}
    symbol = (spec.get("asset_ids") or [None])[0]
    provider_id = session.provider_id or _resolve_session_provider(session, db)

    orders = (
        db.query(PaperOrder)
        .filter(PaperOrder.portfolio_id == portfolio.id)
        .order_by(PaperOrder.created_at, PaperOrder.id)
        .all()
    )
    views: List[PaperOrderLifecycleResponse] = []
    for order in orders:
        execution = None
        if symbol:
            execution = execution_view(
                db,
                order=order,
                portfolio=portfolio,
                instrument_id=symbol,
                provider_id=provider_id,
                start_date=spec.get("start_date") or order.data_cutoff_at or "",
                end_date=spec.get("end_date") or "9999-12-31",
                market_clock_date=session.current_snapshot_date,
            )
        views.append(
            PaperOrderLifecycleResponse(
                order=PaperOrderResponse.model_validate(order),
                execution=execution,
            )
        )
    return views


@router.get("/{session_id}/daily-service", response_model=DailyServiceStatus)
def get_daily_service_status(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What the background replay service will do with this session, and what it did last.

    Every field is read from the two places that already decide the behaviour —
    the session's own row and the ``simulation_daily`` ledger — so this cannot
    drift from the tick and start promising a service that is not running. The
    scheduler facts describe *this process* only: an API worker that did not
    start the interval service reports it honestly rather than implying the
    deployment has no background runner.
    """
    # Imported on call, as the worker imports ``advance_daily_step`` on call:
    # the scheduler and the app factory both sit above this module, and a
    # module-level import here would be the cycle the lazy import exists to avoid.
    from app.models.kill_switch import KillSwitchScope
    from app.models.learning_job import LearningJob
    from app.services.kill_switch import is_kill_switch_active
    from app.workers import learning_scheduler as ls

    served = ls.SIMULATION_SERVED_STATUSES

    session = _get_session_with_ownership(session_id, db, current_user)

    status_value = session.status.value if session.status else None
    timeframe = session.timeframe or "1d"

    enrolled = True
    reason = None
    if status_value is None:
        enrolled, reason = False, "status_unreadable"
    elif session.status not in served:
        enrolled, reason = False, status_value
    elif timeframe in INTRADAY_TIMEFRAMES:
        enrolled, reason = False, "intraday_timeframe_needs_a_bar_clock"

    queue_position = None
    if enrolled:
        # Counted the way the tick selects: same status filter, same
        # (created_at, id) order, no timeframe filter — an intraday session
        # takes a slot in the tick's first 20 and is then skipped, so the
        # position this reports is the position the worker actually used.
        ahead = (
            db.query(func.count(SimulationSession.id))
            .filter(
                SimulationSession.owner_id == current_user.id,
                SimulationSession.status.in_(served),
                (
                    (SimulationSession.created_at < session.created_at)
                    | (
                        (SimulationSession.created_at == session.created_at)
                        & (SimulationSession.id < session.id)
                    )
                ),
            )
        ).scalar()
        queue_position = int(ahead or 0)

    try:
        kill_switch = (
            "active"
            if is_kill_switch_active(db, KillSwitchScope.execution)
            else "inactive"
        )
    except Exception:
        kill_switch = "unreadable"

    scheduler = getattr(import_module("app.main"), "_learning_scheduler", None)
    running = bool(scheduler is not None and getattr(scheduler, "running", False))
    next_run_at = None
    if running:
        job = next(
            (j for j in scheduler.get_jobs() if j.id == "daily_simulation"), None
        )
        if job is not None and job.next_run_time is not None:
            next_run_at = job.next_run_time.isoformat()

    rows = (
        db.query(LearningJob)
        .filter(
            LearningJob.user_id == current_user.id,
            LearningJob.job_type == ls.SIMULATION_JOB_TYPE,
        )
        .order_by(LearningJob.created_at.desc(), LearningJob.id.desc())
        .limit(DAILY_SERVICE_LEDGER_SCAN)
        .all()
    )
    wanted = str(session_id)
    in_flight = any(row.status == "running" for row in rows)
    last_run = None
    for row in rows:
        # A row still ``running`` has no outcome to report yet; the newest row
        # that names this session wins, and a row that names nothing for it is
        # not evidence about this replay.
        if row.result_json is None:
            continue
        result = row.result_json
        entry = next(
            (
                r
                for r in (result.get("results") or [])
                if r.get("session_id") == wanted
            ),
            None,
        )
        detail = None
        if entry is not None:
            outcome = entry.get("outcome")
        else:
            skipped_entry = next(
                (
                    r
                    for r in (result.get("skipped_detail") or [])
                    if r.get("session_id") == wanted
                ),
                None,
            )
            failure = next(
                (
                    e
                    for e in (result.get("errors") or [])
                    if e.startswith(f"{wanted}: ")
                ),
                None,
            )
            if skipped_entry is not None:
                outcome, detail = "skipped", skipped_entry.get("reason")
            elif failure is not None:
                outcome, detail = "failed", failure[len(wanted) + 2 :]
            else:
                # This tick ran and wrote its counts, but never reached the
                # session: the row is evidence the service exists, not that it
                # stepped this replay.
                continue
        last_run = DailyServiceRun(
            job_id=row.id,
            job_status=row.status,
            scope=(row.config_json or {}).get("scope"),
            scheduled_for=(row.config_json or {}).get("scheduled_for"),
            completed_at=(
                row.completed_at.isoformat() if row.completed_at else None
            ),
            outcome=outcome,
            step_number=(entry or {}).get("step_number"),
            snapshot_date=(entry or {}).get("snapshot_date"),
            detail=detail,
        )
        break

    return DailyServiceStatus(
        session_id=session.id,
        session_status=status_value,
        timeframe=timeframe,
        cadence_hour_utc=ls.SIMULATION_HOUR_UTC,
        cadence_timezone=ls.SERVICE_TIMEZONE,
        job_type=ls.SIMULATION_JOB_TYPE,
        enrolled=enrolled,
        enrolment_reason=reason,
        queue_position=queue_position,
        batch_limit=ls.SIMULATION_SESSIONS_PER_OWNER,
        in_next_batch=(
            queue_position is not None
            and queue_position < ls.SIMULATION_SESSIONS_PER_OWNER
        ),
        kill_switch=kill_switch,
        runs_in_this_process=running,
        next_run_at=next_run_at,
        in_flight=in_flight,
        last_run=last_run,
        statuses_served=[s.value for s in served],
    )


@router.post("/{session_id}/pause", response_model=SimulationSessionResponse)
def pause_simulation(
    session_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(session_id, db, current_user)
    if session.status != SimulationStatus.active:
        raise HTTPException(status_code=400, detail="Can only pause an active session")

    session.status = SimulationStatus.paused
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_paused", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"observed_days": session.observed_days},
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )

    db.commit()
    db.refresh(session)
    return session


@router.post("/{session_id}/resume", response_model=SimulationSessionResponse)
def resume_simulation(
    session_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(session_id, db, current_user)
    if session.status != SimulationStatus.paused:
        raise HTTPException(status_code=400, detail="Can only resume a paused session")

    session.status = SimulationStatus.active
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_resumed", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"observed_days": session.observed_days},
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )

    db.commit()
    db.refresh(session)
    return session


@router.post("/{session_id}/retire", response_model=SimulationSessionResponse)
def retire_simulation(
    session_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Close a session as retired and keep everything it recorded.

    Retiring is a status change, not a liquidation: no position is sold, no
    pending order is expired, and paper equity is left exactly as it stood. The
    decisions, orders and fills already stored stay readable, so a retired
    session remains usable evidence.

    A session already `completed` or `retired` returns 400 and changes nothing.
    Retirement is final on this API surface: there is no un-retire endpoint, and
    `resume` only accepts a `paused` session. An agent can therefore assert that
    the first retire succeeds, and every later retire on the same session is a
    no-op refusal (still 400), rather than a second transition.
    """
    session = _get_session_with_ownership(session_id, db, current_user)
    if session.status in (SimulationStatus.completed, SimulationStatus.retired):
        raise HTTPException(status_code=400, detail=f"Cannot retire a '{getattr(session.status, 'value', session.status)}' session")

    session.status = SimulationStatus.retired
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_retired", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "total_decisions": session.total_decisions,
            "observed_days": session.observed_days,
            "final_paper_equity": session.paper_equity,
        },
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )

    db.commit()
    db.refresh(session)
    return session


def _observed_bar_close(
    db: Session,
    *,
    session: SimulationSession,
    symbol: str,
    provider_id: Optional[str],
    cutoff: str,
    interval_minutes: Optional[int] = None,
) -> Optional[float]:
    """The close of the bar the user was watching, read from the bound dataset.

    Only used to *estimate* what an order is likely to cost. It never prices a
    trade: the executable price is the next bar's open, taken by the executor.
    Returns None when the bound dataset has no such row, so the caller says the
    estimate is unavailable instead of assuming zero.

    ``interval_minutes`` keeps that estimate on the same daily series the
    replay cursor is walking; one date can hold a daily and a four-hour row at
    the same midnight, and picking between them by sort order is not a rule.
    """
    query = db.query(MarketDataBar).filter(MarketDataBar.instrument_id == symbol)
    if provider_id:
        query = query.filter(MarketDataBar.provider_id == provider_id)
    if _is_intraday_timeframe(session.timeframe) and session.current_bar_timestamp is not None:
        query = query.filter(MarketDataBar.bar_timestamp == session.current_bar_timestamp)
    else:
        query = query.filter(MarketDataBar.bar_date == date.fromisoformat(cutoff))
        if interval_minutes is not None:
            query = query.filter(MarketDataBar.interval_minutes == interval_minutes)

    bar = query.order_by(MarketDataBar.bar_date, MarketDataBar.bar_timestamp).first()
    return float(bar.close) if bar is not None else None


def _submit_manual_order(
    db: Session,
    *,
    session: SimulationSession,
    version: ExperimentVersion,
    payload: ManualDecisionRequest,
    action: str,
    symbol: str,
    cutoff: str,
    spec: Dict[str, Any],
    actor_id: UUID,
    actor_type: str = "unknown",
):
    """Turn one human intent into a pending order on the account this session settles into.

    Returns ``(decision_type, rejection, order, execution, warnings)``. Two
    rejections are answered at submit because the account's holdings are known
    now — an order to buy a second position, or to sell nothing, can never be
    honoured. Affordability is *not* known now: the cash that matters is what the
    account holds when the next bar opens, so the submit path only estimates and
    warns, and the executor rejects for real if the money is not there.

    ``DecisionType.blocked_action`` is reused rather than a new enum member
    because the column is a native PostgreSQL ENUM; the rejection reason
    ``awaiting_execution_window`` says the intent is queued, not refused.
    """
    try:
        portfolio = resolve_session_portfolio(db, session, owner_id=actor_id)
    except LifecycleError as exc:
        raise HTTPException(status_code=400, detail=exc.message)

    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        raise HTTPException(
            status_code=400,
            detail=(
                f"The bound paper portfolio is '{portfolio.status.value}' and cannot "
                "accept new orders. Bind a portfolio that is still active."
            ),
        )

    # The lineage the caller claims is verified before anything is read or
    # written: an inference that is not this owner's, not a personal-model
    # prediction, or not generated for this account cannot be recorded as the
    # origin of this order's decision. Same refusals as the paper desk's
    # ``POST /{portfolio_id}/decisions``, from one function.
    #
    # The version this door compares against is the session's own
    # ``experiment_version_id`` — the same identity written into the decision row
    # below as ``linked_experiment_version_id`` and into the order as
    # ``model_or_strategy_version_id``. It is never the model version, the
    # account's current selection, an alias or anything the request declares,
    # because those are all quantities that can move while the replay is running,
    # and a lineage stamped from a moving value would not describe the strategy
    # the user was actually watching.
    if payload.linked_inference_id is not None:
        validate_linked_inference(
            db,
            owner_id=actor_id,
            inference_id=payload.linked_inference_id,
            portfolio_id=portfolio.id,
            strategy_version_id=version.id,
        )

    commission_rate = float(spec.get("commission_rate", 0.0))
    slippage_rate = float(spec.get("slippage_rate", 0.0))
    held = _held_quantity(db, portfolio, symbol)
    side = OrderSide.BUY if action == "buy" else OrderSide.SELL

    if side is OrderSide.BUY and held > 0:
        return (
            DecisionType.blocked_action,
            {
                'reason': 'already_in_position',
                'current_shares': int(held),
                'intent_preserved': True,
                'booked_at_submit': False,
            },
            None,
            None,
            [],
        )
    if side is OrderSide.SELL and held <= 0:
        return (
            DecisionType.blocked_action,
            {
                'reason': 'no_position_to_sell',
                'intent_preserved': True,
                'booked_at_submit': False,
            },
            None,
            None,
            [],
        )

    if side is OrderSide.SELL and payload.quantity is not None:
        requested = float(payload.quantity)
        if requested - held > 1e-9:
            return (
                DecisionType.blocked_action,
                {
                    'reason': 'quantity_exceeds_position',
                    'requested_quantity': requested,
                    'current_shares': int(held),
                    'intent_preserved': True,
                    'booked_at_submit': False,
                },
                None,
                None,
                [],
            )

    quantity = float(payload.quantity or 1) if side is OrderSide.BUY else (
        float(payload.quantity) if payload.quantity else held
    )

    if find_existing_by_key(db, portfolio=portfolio, idempotency_key=payload.idempotency_key):
        raise HTTPException(
            status_code=409,
            detail=(
                "This idempotency_key already has an order in the same paper "
                "portfolio; a second order is not submitted. Refresh the order "
                "list to see the existing one."
            ),
        )

    provider_id = session.provider_id or _resolve_session_provider(session, db)
    warnings: List[str] = []
    observed_close = _observed_bar_close(
        db,
        session=session,
        symbol=symbol,
        provider_id=provider_id,
        cutoff=cutoff,
        interval_minutes=_declared_daily_interval_minutes(spec),
    )

    # The same policy the other lanes ask, answered before an intent exists: a
    # hard violation at submit must not leave a pending order the executor will
    # later fill. The budget is counted on this decision's own bar date, so a
    # replay of a 2024 bar gets that bar's budget rather than today's.
    open_value, unpriced = value_open_positions(
        db,
        portfolio,
        price_by_asset=(
            {symbol: observed_close} if observed_close is not None else {}
        ),
        on_or_before=cutoff,
        provider_id=provider_id,
    )
    risk = evaluate_risk(
        RiskInputs(
            lane="session",
            side=side.value,
            asset_id=symbol,
            quantity=quantity,
            portfolio_status=portfolio.status.value,
            portfolio_cash=float(portfolio.current_virtual_cash),
            held_quantity=held,
            price=observed_close,
            open_positions_value=None if unpriced else open_value,
            unpriced_assets=unpriced,
            entries_on_date=count_entries(db, portfolio, market_date=cutoff),
            decision_date=cutoff,
        ),
        lane_allowed_instruments=spec.get("asset_ids"),
    )
    if not risk.allowed:
        return (
            DecisionType.blocked_action,
            {
                'reason': risk.code,
                'message': risk.message,
                'intent_preserved': False,
                'booked_at_submit': False,
                'risk_check': risk.as_dict(),
            },
            None,
            None,
            [],
        )

    estimate: Optional[Dict[str, Any]] = None
    if observed_close is None:
        warnings.append(
            f"The bound dataset has no readable close at {cutoff}, so cost cannot "
            "be estimated at submission. The cash required is set at the execution "
            "bar's open; if it is insufficient the order is rejected, never "
            "partially filled."
        )
    elif side is OrderSide.BUY:
        needed = round(quantity * observed_close * (1 + slippage_rate) * (1 + commission_rate), 4)
        cash_now = round(float(portfolio.current_virtual_cash), 4)
        estimate = {
            "basis": "observed_close_estimate_only",
            "observed_close": round(observed_close, 4),
            "quantity": quantity,
            "estimated_cost": needed,
            "account_cash": cash_now,
        }
        if needed > cash_now:
            warnings.append(
                f"Estimated from the observed close of {round(observed_close, 4)}, "
                f"this buy needs about {needed} against the account's current cash "
                f"of {cash_now}. The actual amount is set by the execution bar's "
                "open; if cash is insufficient the order is rejected, never "
                "partially filled."
            )

    decision = DecisionRecord(
        owner_id=portfolio.owner_id,
        paper_portfolio_id=portfolio.id,
        simulation_session_id=session.id,
        actor_type=decision_actor_type(actor_type),
        decision_type=PaperDecisionType.entry if side is OrderSide.BUY else PaperDecisionType.exit,
        data_cutoff_at=cutoff,
        linked_experiment_version_id=version.id,
        linked_inference_id=payload.linked_inference_id,
        rationale=payload.rationale,
        self_reported_confidence=ConfidenceLevel.not_provided,
        outcome_status=OutcomeStatus.pending,
        risk_flags={
            "manual_submission": True,
            "auto_execution": False,
            "booked_at_submit": False,
            "cost_estimate": estimate,
            "risk_check": risk.as_dict(),
        },
    )
    order = PaperOrder(
        portfolio_id=portfolio.id,
        asset_id=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.market,
        status=OrderStatus.pending,
        model_or_strategy_version_id=version.id,
        data_cutoff_at=cutoff,
        rationale=payload.rationale,
        risk_check_status=RiskCheckStatus.passed,
        idempotency_key=payload.idempotency_key,
        actor_type=actor_type,
    )
    try:
        submit_pending(db, portfolio=portfolio, decision=decision, order=order, data_cutoff_at=cutoff)
    except IntegrityError as exc:
        # The key check above is a read, not a lock: two concurrent submissions
        # can both pass it, and the unique index on (portfolio_id,
        # idempotency_key) is what actually stops the second order — here, at
        # flush time, earlier than the commit guard in ``manual_decision``.
        # Rolled back and answered as the same conflict only when that index is
        # the one that fired; any other integrity failure keeps its own shape,
        # because telling a caller their retry collided is a claim about the
        # caller, not a place to park a broken foreign key.
        db.rollback()
        if expected_unique_violation(exc) != PAPER_ORDER_IDEMPOTENCY:
            raise
        raise HTTPException(
            status_code=409,
            detail=(
                "This idempotency_key was already written by another request; a "
                "second order is not submitted. Refresh the order list to see the "
                "existing one."
            ),
        )

    execution_info = execution_view(
        db,
        order=order,
        portfolio=portfolio,
        instrument_id=symbol,
        provider_id=provider_id,
        start_date=spec["start_date"],
        end_date=spec["end_date"],
    )
    order_info = {
        "order_id": str(order.id),
        "decision_id": str(decision.id),
        "asset_id": order.asset_id,
        "side": order.side.value,
        "quantity": float(order.quantity),
        "status": order.status.value,
        "actor_type": order.actor_type,
        "decided_on_cutoff": order.data_cutoff_at,
        "order_created_at_utc": order.created_at.isoformat() if order.created_at else None,
        "idempotency_key": order.idempotency_key,
        "booked_at_submit": False,
    }
    rejection = {
        "reason": "awaiting_execution_window",
        "intent_preserved": True,
        "booked_at_submit": False,
        "order_id": str(order.id),
        "message": (
            "Order submitted and not yet filled. It will execute at the open of "
            f"the first bar in the bound dataset after {cutoff}; until then it is "
            "not counted as a holding and does not move cash."
        ),
    }
    return (DecisionType.blocked_action, rejection, order_info, execution_info, warnings)


@router.post("/{session_id}/manual-decision", response_model=ManualDecisionResponse)
def manual_decision(
    session_id: UUID,
    payload: ManualDecisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record one human decision against the bar the user has already watched.

    A BUY or SELL here is a *pending order*, not a fill. The request books no
    cash, no position and no fill row — the market clock (``advance`` stepping
    onto the next stored bar) is what executes it, at that bar's open. HOLD is
    recorded as a decision and changes nothing.

    The submission also never moves the replay cursor: ``advance`` owns the
    cursor, so the decision timestamp is the last bar observed and the execution
    timestamp is whichever bar the market reaches next. Those are two different
    quantities and are recorded separately.
    """
    session = _get_session_with_ownership(session_id, db, current_user)

    # Idempotency check FIRST - before mutable state checks
    # This allows replays of historical decisions even if session state has changed
    existing_decision = db.query(SimulationDecision).filter(
        SimulationDecision.idempotency_key == payload.idempotency_key,
    ).first()

    if existing_decision:
        # Verify ownership
        if existing_decision.session_id != session.id:
            raise HTTPException(status_code=403, detail="Idempotency key conflict")

        # Compare meaningful payload fields
        existing_detail = existing_decision.decision_detail or {}
        existing_action = existing_detail.get('action')
        # ``quantity`` in the detail is what the request itself carried, so its
        # presence — not its truthiness — decides the comparison: a sell-everything
        # request has quantity None, while the order it produced holds a number.
        # Rows written before the intent was stored fall back to the derived one.
        if 'quantity' in existing_detail:
            existing_quantity = existing_detail.get('quantity')
        else:
            existing_quantity = (
                (existing_detail.get('order') or {}).get('quantity')
                or (existing_detail.get('fill') or {}).get('quantity')
            )
        existing_rationale = existing_detail.get('rationale')
        # The claimed inference is part of the payload: the same key replayed
        # against a different origin is a conflict, not a quiet return of the
        # row written under the first one.
        existing_inference = existing_detail.get('linked_inference_id')
        requested_inference = (
            str(payload.linked_inference_id) if payload.linked_inference_id else None
        )

        def normalize(val):
            return val if val not in (None, '') else None

        payload_matches = (
            normalize(existing_action) == normalize(payload.action) and
            normalize(existing_quantity) == normalize(payload.quantity) and
            normalize(existing_rationale) == normalize(payload.rationale) and
            normalize(existing_inference) == normalize(requested_inference)
        )

        if not payload_matches:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key conflict: same key with different payload"
            )

        # Same key, same payload: return original decision (idempotent replay)
        return ManualDecisionResponse(
            session=session,
            decision=existing_decision,
            fill=existing_detail.get('fill'),
            rejection=existing_detail.get('rejection'),
            order=existing_detail.get('order'),
            execution=existing_detail.get('execution'),
        )

    # The replay decision also becomes a training-label candidate on the
    # human-decision ledger, in the same transaction. A key already used by a
    # different ledger event is a conflict rather than a silent relabel.
    recorded_action = HumanDecisionAction(payload.action.lower())
    recorded_quantity = None if recorded_action == HumanDecisionAction.hold else payload.quantity
    existing_event = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.owner_id == current_user.id,
        HumanDecisionEvent.simulation_session_id == session.id,
        HumanDecisionEvent.idempotency_key == payload.idempotency_key,
    ).first()
    if existing_event is not None:
        if (
            existing_event.action != recorded_action
            or existing_event.quantity != recorded_quantity
            or (existing_event.rationale or None) != (payload.rationale or None)
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Idempotency key conflict: a different human-decision event "
                    "already uses this key"
                ),
            )

    actor_type = request_actor_provenance(request)

    # For NEW decisions: check mutable session state
    if session.status not in (SimulationStatus.pending, SimulationStatus.active):
        raise HTTPException(status_code=400, detail=f"Cannot make decisions on '{getattr(session.status, 'value', session.status)}' session")

    # Check provider confirmation
    if not session.provider_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Session provider not confirmed. Please create a new session with explicit provider selection."
        )

    # Optimistic locking: check expected_version if provided
    if payload.expected_version is not None:
        if payload.expected_version != session.state_version:
            raise HTTPException(
                status_code=409,
                detail=f"State version conflict: expected {payload.expected_version}, current {session.state_version}. Session has been modified."
            )

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == session.experiment_version_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Associated version not found")

    spec = version.strategy_spec
    symbol = spec["asset_ids"][0]

    action = payload.action.lower()
    if action not in ("buy", "sell", "hold"):
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

    # The decision clock is the last bar the user actually watched. A session
    # nobody has advanced yet has no observed market time to stamp an intent
    # with, and inventing one (the spec's start date) would leave a decision row
    # on a bar the replay then treats as already consumed, silently dropping that
    # bar's own signal. So the request is refused instead of guessed at.
    cutoff = session.current_snapshot_date
    if cutoff is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No bar has been observed yet, so there is no market time to use "
                "as the decision basis. Advance the replay to the first bar, then "
                "submit the order."
            ),
        )

    # The book this decision is recorded against, exactly as it stands: pending is
    # not executed, so the submission copies the current figures rather than
    # moving them.
    last_row = session.decisions[-1] if session.decisions else None
    book_cash = (
        round(float(last_row.paper_cash), 4)
        if last_row is not None
        else round(float(session.initial_virtual_cash), 4)
    )
    book_position = session.current_position_json
    book_equity = round(float(session.paper_equity if session.paper_equity is not None else book_cash), 4)

    decision_detail: Dict[str, Any] = {
        'action': action,
        'quantity': payload.quantity,
        'idempotency_key': payload.idempotency_key,
        'rationale': payload.rationale,
        'reflection': payload.reflection,
        'symbol': symbol,
        'snapshot_date': cutoff,
        'booked_at_submit': False,
        # Text, like every other identity in this payload blob: the replay of the
        # same key compares these values, and a UUID never equals its own string.
        'linked_inference_id': (
            str(payload.linked_inference_id) if payload.linked_inference_id else None
        ),
    }
    warnings: List[str] = []
    order_info: Optional[Dict[str, Any]] = None
    execution_info: Optional[Dict[str, Any]] = None
    rejection_info: Optional[Dict[str, Any]] = None

    if action == 'hold':
        if payload.linked_inference_id is not None:
            # A hold here writes a replay decision row only — no paper decision
            # row exists to carry the inference column. Storing the identity in a
            # side blob instead would make two sources claim to own one lineage,
            # so the request is refused and pointed at the route that can record it.
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "inference_needs_decision_row",
                    "message": (
                        "A hold on this route records no paper decision, so it cannot "
                        "carry linked_inference_id. Record the held decision with its "
                        "inference through POST /api/v1/paper-portfolio/{portfolio_id}/decisions "
                        "instead."
                    ),
                },
            )
        decision_type = DecisionType.no_action
    else:
        decision_type, rejection_info, order_info, execution_info, warnings = _submit_manual_order(
            db,
            session=session,
            version=version,
            payload=payload,
            action=action,
            symbol=symbol,
            cutoff=cutoff,
            spec=spec,
            actor_type=actor_type,
            actor_id=current_user.id,
        )

    decision_detail['book'] = {
        'paper_cash': book_cash,
        'paper_position': book_position,
        'paper_equity': book_equity,
        'moved_by_this_submission': False,
    }
    if order_info:
        decision_detail['order'] = order_info
    if execution_info:
        decision_detail['execution'] = execution_info
    if rejection_info:
        decision_detail['rejection'] = rejection_info

    step_number = session.total_decisions + 1
    decision = SimulationDecision(
        session_id=session.id,
        step_number=step_number,
        snapshot_date=cutoff,
        data_cutoff_at=session.data_cutoff_at,
        engine_version=_replay_engine_version(session),
        decision_type=decision_type,
        decision_detail=decision_detail,
        idempotency_key=payload.idempotency_key,
        paper_cash=book_cash,
        paper_position=book_position,
        paper_equity=book_equity,
        warnings=warnings if warnings else None,
    )
    db.add(decision)

    # Update session state with optimistic locking. The cursor, the equity and
    # the position all stay as they were — only the decision count and the
    # version move. One transaction writes the decision, the order and the
    # session row, so a version conflict rolls the intent back whole rather than
    # leaving an order the user never sees.
    current_version = session.state_version
    update_result = db.execute(
        SimulationSession.__table__.update().where(
            (SimulationSession.__table__.c.id == session.id) &
            (SimulationSession.__table__.c.state_version == current_version)
        ).values(
            total_decisions=step_number,
            state_version=current_version + 1,
            status=SimulationStatus.active if session.status == SimulationStatus.pending else session.status,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None) if session.status == SimulationStatus.pending else session.started_at,
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
    )

    if update_result.rowcount == 0:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="State version conflict: session was modified by another request. Please reload and retry."
        )

    db.refresh(session)

    human_event: Optional[HumanDecisionEvent] = None
    if existing_event is None:
        ctx = _derive_server_context(session, db)
        event_metadata: Dict[str, Any] = {
            "source": "manual_decision",
            "simulation_step_number": step_number,
        }
        if payload.reflection:
            event_metadata["reflection"] = payload.reflection
        human_event = HumanDecisionEvent(
            owner_id=current_user.id,
            simulation_session_id=session.id,
            idempotency_key=payload.idempotency_key,
            action=recorded_action,
            quantity=recorded_quantity,
            rationale=payload.rationale,
            server_strategy_version_id=ctx["strategy_version_id"],
            server_instrument_id=ctx["instrument_id"],
            server_step_number=ctx["step_number"],
            server_snapshot_date=ctx["snapshot_date"],
            server_data_cutoff_at=ctx["data_cutoff_at"],
            decision_instant=ctx["decision_instant"],
            execution_status=HumanDecisionExecutionStatus.record_only,
            metadata_json=event_metadata,
            supersedes_event_id=None,
            actor_type=actor_type,
            # The same identity the decision row of this very request carries,
            # on the column this table added for it (d4e5f6g7h8i9). Not in
            # ``event_metadata``: two places claiming one lineage is how they
            # start disagreeing.
            linked_inference_id=payload.linked_inference_id,
        )
        db.add(human_event)

    # The guard below starts *before* the audit write, not before the commit.
    # ``emit_event`` flushes (``app/services/__init__.py:64``), so that call is
    # the first statement that drains this request's decision, ledger event and
    # order; a collision detected there used to escape the door entirely and
    # surface as an unhandled 500, which made the 409 contract documented in the
    # ``except`` below a claim about a branch the request rarely reached.
    try:
        emit_event(
            db,
            entity_type="simulation", entity_id=session.id,
            event_type="manual_decision", actor_id=current_user.id,
            actor_type=actor_type,
            payload={
                "step_number": step_number,
                "action": action,
                "snapshot_date": cutoff,
                "decision_type": decision_type.value,
                "paper_equity": book_equity,
                "book_changed": False,
                "has_order": order_info is not None,
                "order_id": order_info["order_id"] if order_info else None,
                "order_status": order_info["status"] if order_info else None,
                "has_rejection": rejection_info is not None,
                "rejection_reason": rejection_info.get("reason") if rejection_info else None,
            },
            rules_locked=True,
            data_cutoff_at=session.data_cutoff_at,
        )
        db.flush()
        if human_event is not None:
            emit_event(
                db,
                entity_type="human_decision",
                entity_id=str(human_event.id),
                event_type="human_decision_created",
                actor_id=current_user.id,
                actor_type=actor_type,
                payload={
                    "action": action,
                    "quantity": recorded_quantity,
                    "idempotency_key": payload.idempotency_key,
                    "simulation_session_id": str(session.id),
                    "source": "manual_decision",
                    "actor_type": actor_type,
                },
                data_cutoff_at=session.data_cutoff_at,
            )
        db.commit()
    except IntegrityError as exc:
        # Two requests carrying one idempotency key can both clear the read-side
        # checks; the unique index is what actually stops the second order, so a
        # duplicate is rolled back whole and answered as a conflict instead of
        # surfacing as an unhandled 500. Only that index: this block writes a
        # decision, an audit row, an order and its fills, and a foreign key or a
        # NOT NULL failing inside it is the platform's bug, not the caller's
        # repeated click.
        db.rollback()
        if expected_unique_violation(exc) != PAPER_ORDER_IDEMPOTENCY:
            raise
        raise HTTPException(
            status_code=409,
            detail=(
                "This idempotency_key was already written by another request; a "
                "second order is not submitted. Refresh the order list to see the "
                "existing one."
            ),
        )
    db.refresh(session)
    db.refresh(decision)

    return ManualDecisionResponse(
        session=session,
        decision=decision,
        fill=(decision.decision_detail or {}).get('fill'),
        rejection=rejection_info,
        order=order_info,
        execution=execution_info,
    )


@router.patch("/{session_id}/decisions/{decision_id}/reflection", response_model=ReflectionUpdateResponse)
def update_decision_reflection(
    session_id: UUID,
    decision_id: UUID,
    payload: ReflectionUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update reflection on an existing decision without creating a new trade or advancing the session."""
    # Verify session ownership
    session = _get_session_with_ownership(session_id, db, current_user)
    
    # Find the decision
    decision = db.query(SimulationDecision).filter(
        SimulationDecision.id == decision_id,
        SimulationDecision.session_id == session_id,
    ).first()
    
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    
    # Update the reflection in decision_detail
    # Create a new dict to ensure SQLAlchemy detects the change
    decision_detail = dict(decision.decision_detail or {})
    decision_detail['reflection'] = payload.reflection
    decision.decision_detail = decision_detail
    
    # Emit audit event
    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="reflection_updated", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "decision_id": str(decision.id),
            "step_number": decision.step_number,
        },
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )
    
    db.commit()
    db.refresh(decision)
    
    return ReflectionUpdateResponse(decision=decision)
