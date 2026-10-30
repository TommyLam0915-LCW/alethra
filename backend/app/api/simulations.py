from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion, VersionStatus
from app.models.simulation import SimulationSession, SimulationDecision, SimulationStatus, DecisionType
from app.auth.jwt import get_current_user
from app.api.schemas import (
    SimulationSessionCreate,
    SimulationSessionResponse,
    SimulationDecisionResponse,
    SimulationAdvanceResponse,
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
)
from app.services import emit_event
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
from app.services.market_data_storage import MarketDataStorage
from app.models.market_data import MarketDataBar
from app.providers import get_data_provider
from sqlalchemy import distinct, func
import pandas as pd

router = APIRouter(prefix="/simulations", tags=["simulations"])


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


def _load_stored_ohlcv(symbol: str, start_date: str, end_date: str, db: Session, provider_id: str = None) -> pd.DataFrame:
    """Load OHLCV bars from stored market data.
    
    Args:
        symbol: Instrument ID (e.g., 'US:AAPL')
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        db: Database session
        provider_id: Provider filter (e.g., 'twelvedata', 'demo'). If None, loads all providers.
    """
    query = db.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == symbol,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= end_date,
    )
    
    if provider_id:
        query = query.filter(MarketDataBar.provider_id == provider_id)
    
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
            'volume': bar.volume or 0,
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


def _build_simulator(session: SimulationSession, version: ExperimentVersion) -> ForwardSimulator:
    spec = version.strategy_spec
    return ForwardSimulator(
        strategy_spec=spec,
        initial_cash=session.initial_virtual_cash,
        commission_rate=spec.get("commission_rate", 0.0),
        slippage_rate=spec.get("slippage_rate", 0.0),
        dataset_cutoff_at=session.data_cutoff_at,
        engine_version=ENGINE_VERSION,
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
    shares_after = int(txn["shares_after"])
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
        "quantity": int(txn["quantity"]),
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
            simulator.shares = int(held + quantity)
            simulator._bought = simulator.shares > 0
        else:
            simulator.shares = max(0, int(float(simulator.shares) - quantity))
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
    shares = int(replay.shares)
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
    db: Session, *, session: SimulationSession, version: ExperimentVersion, current_user: User
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


@router.post("/{session_id}/advance", response_model=SimulationAdvanceResponse)
def advance_simulation(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
        return _advance_intraday(db, session=session, version=version, current_user=current_user)

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
    stored = (
        _load_stored_ohlcv(symbol, start_date, end_date, db, provider_id=provider_id)
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
                        actor_id=current_user.id,
                    )
                    if result.warnings:
                        existing = session.warnings or []
                        session.warnings = existing + result.warnings

        # No next bar exists in the bound dataset, so any pending manual order
        # has lost its only window. Say so on the record rather than leaving it
        # pending forever or filling it at a price the user already watched.
        _expire_session_pending_orders(
            db, session=session, actor_id=current_user.id, reason="dataset_exhausted"
        )

        session.status = SimulationStatus.completed
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        emit_event(
            db,
            entity_type="simulation", entity_id=session.id,
            event_type="simulation_completed", actor_id=current_user.id,
            payload={"total_decisions": session.total_decisions, "observed_days": session.observed_days},
            rules_locked=True,
            data_cutoff_at=session.data_cutoff_at,
        )

        db.commit()
        db.refresh(session)
        raise HTTPException(
            status_code=400,
            detail="No more snapshots available — simulation complete",
        )

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
        actor_id=current_user.id,
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
        actor_id=current_user.id,
    )
    if result.warnings:
        existing = session.warnings or []
        session.warnings = existing + result.warnings
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_advanced", actor_id=current_user.id,
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

    return SimulationAdvanceResponse(session=session, decision=decision)


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


@router.post("/{session_id}/pause", response_model=SimulationSessionResponse)
def pause_simulation(
    session_id: UUID,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(session_id, db, current_user)
    if session.status in (SimulationStatus.completed, SimulationStatus.retired):
        raise HTTPException(status_code=400, detail=f"Cannot retire a '{getattr(session.status, 'value', session.status)}' session")

    session.status = SimulationStatus.retired
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="simulation_retired", actor_id=current_user.id,
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
) -> Optional[float]:
    """The close of the bar the user was watching, read from the bound dataset.

    Only used to *estimate* what an order is likely to cost. It never prices a
    trade: the executable price is the next bar's open, taken by the executor.
    Returns None when the bound dataset has no such row, so the caller says the
    estimate is unavailable instead of assuming zero.
    """
    query = db.query(MarketDataBar).filter(MarketDataBar.instrument_id == symbol)
    if provider_id:
        query = query.filter(MarketDataBar.provider_id == provider_id)
    if _is_intraday_timeframe(session.timeframe) and session.current_bar_timestamp is not None:
        query = query.filter(MarketDataBar.bar_timestamp == session.current_bar_timestamp)
    else:
        query = query.filter(MarketDataBar.bar_date == date.fromisoformat(cutoff))

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
        portfolio = resolve_session_portfolio(db, session)
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
        db, session=session, symbol=symbol, provider_id=provider_id, cutoff=cutoff
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
        actor_type=PaperDecisionActorType.human,
        decision_type=PaperDecisionType.entry if side is OrderSide.BUY else PaperDecisionType.exit,
        data_cutoff_at=cutoff,
        linked_experiment_version_id=version.id,
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
        actor_type="human",
    )
    try:
        submit_pending(db, portfolio=portfolio, decision=decision, order=order, data_cutoff_at=cutoff)
    except IntegrityError:
        # The key check above is a read, not a lock: two concurrent submissions
        # can both pass it, and the unique index on (portfolio_id,
        # idempotency_key) is what actually stops the second order — here, at
        # flush time, earlier than the commit guard in ``manual_decision``. Rolled
        # back and answered as the same conflict, so a duplicate is never an
        # unhandled 500 whichever statement the index rejected.
        db.rollback()
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

        def normalize(val):
            return val if val not in (None, '') else None

        payload_matches = (
            normalize(existing_action) == normalize(payload.action) and
            normalize(existing_quantity) == normalize(payload.quantity) and
            normalize(existing_rationale) == normalize(payload.rationale)
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
    }
    warnings: List[str] = []
    order_info: Optional[Dict[str, Any]] = None
    execution_info: Optional[Dict[str, Any]] = None
    rejection_info: Optional[Dict[str, Any]] = None

    if action == 'hold':
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
        engine_version=SIMULATION_ENGINE_VERSION,
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

    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="manual_decision", actor_id=current_user.id,
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

    try:
        db.commit()
    except IntegrityError:
        # Two requests carrying one idempotency key can both clear the read-side
        # checks; the unique constraints are what actually stop the second order,
        # so a duplicate is rolled back whole and answered as a conflict instead
        # of surfacing as an unhandled 500.
        db.rollback()
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
