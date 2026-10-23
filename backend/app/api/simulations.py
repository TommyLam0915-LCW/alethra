from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
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
    ReflectionUpdateRequest,
    ReflectionUpdateResponse,
)
from app.engine.simulation import ForwardSimulator, SIMULATION_ENGINE_VERSION
from app.engine.backtest import ENGINE_VERSION, compute_config_hash
from app.data.demo_data import get_demo_ohlcv, get_trade_dates
from app.services import emit_event
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


def _rebuild_state(session: SimulationSession, version: ExperimentVersion, simulator: ForwardSimulator):
    decisions = session.decisions
    spec = version.strategy_spec
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]

    for d in decisions:
        ohlcv = get_demo_ohlcv([symbol], start_date, d.snapshot_date)
        if symbol in ohlcv:
            simulator.step(ohlcv[symbol], d.snapshot_date)


def _rebuild_manual_state(session: SimulationSession, simulator: ForwardSimulator):
    """Rebuild simulator state from manual decisions by restoring cash/shares/entry from last decision."""
    decisions = session.decisions
    if not decisions:
        return

    last_decision = decisions[-1]
    simulator.cash = last_decision.paper_cash
    if last_decision.paper_position:
        simulator.shares = last_decision.paper_position.get('shares', 0)
        simulator.entry_price = last_decision.paper_position.get('entry_price')
        simulator.entry_date = last_decision.paper_position.get('entry_date')
        simulator._bought = simulator.shares > 0
    else:
        simulator.shares = 0
        simulator.entry_price = None
        simulator.entry_date = None
        simulator._bought = False


def _get_next_snapshot_date(version: ExperimentVersion, current_snapshot_date: str) -> str:
    all_dates = get_trade_dates()
    spec = version.strategy_spec
    start_date = spec["start_date"]
    end_date = spec["end_date"]

    eligible = [d for d in all_dates if d > current_snapshot_date and start_date <= d <= end_date]
    if not eligible:
        return None
    return eligible[0]


def _get_next_stored_snapshot_date(ohlcv_df: pd.DataFrame, current_snapshot_date: str, start_date: str, end_date: str) -> str:
    """Get next snapshot date from stored data."""
    all_dates = sorted(ohlcv_df.index.tolist())
    eligible = [d for d in all_dates if d > current_snapshot_date and start_date <= d <= end_date]
    if not eligible:
        return None
    return eligible[0]


def _resolve_session_provider(session: SimulationSession) -> str:
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
    providers = session.experiment_version.session.query(distinct(MarketDataBar.provider_id)).filter(
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


@router.post("/{session_id}/advance", response_model=SimulationAdvanceResponse)
def advance_simulation(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(session_id, db, current_user)

    if session.status not in (SimulationStatus.pending, SimulationStatus.active):
        raise HTTPException(status_code=400, detail=f"Cannot advance session in '{session.status}' status")

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == session.experiment_version_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Associated version not found")

    spec = version.strategy_spec
    symbol = spec["asset_ids"][0]
    start_date = spec["start_date"]

    current_snapshot = session.current_snapshot_date
    if current_snapshot is None:
        next_date = start_date
    else:
        next_date = _get_next_snapshot_date(version, current_snapshot)

    if next_date is None:
        # Force-exit any open position at the last available close
        if session.current_position_json is not None:
            last_snapshot = session.current_snapshot_date
            if last_snapshot:
                ohlcv_data = get_demo_ohlcv([symbol], start_date, last_snapshot)
                if symbol in ohlcv_data and not ohlcv_data[symbol].empty:
                    ohlcv = ohlcv_data[symbol]
                    simulator = _build_simulator(session, version)
                    _rebuild_state(session, version, simulator)
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
                    if result.warnings:
                        existing = session.warnings or []
                        session.warnings = existing + result.warnings

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

    ohlcv_data = get_demo_ohlcv([symbol], start_date, next_date)
    if symbol not in ohlcv_data or ohlcv_data[symbol].empty:
        raise HTTPException(status_code=400, detail=f"No data available for {symbol} up to {next_date}")

    ohlcv = ohlcv_data[symbol]

    if session.status == SimulationStatus.pending:
        session.status = SimulationStatus.active
        session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    simulator = _build_simulator(session, version)
    _rebuild_state(session, version, simulator)

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
        raise HTTPException(status_code=400, detail=f"Cannot retire a '{session.status}' session")

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


@router.post("/{session_id}/manual-decision", response_model=ManualDecisionResponse)
def manual_decision(
    session_id: UUID,
    payload: ManualDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute manual BUY/SELL/HOLD decision with stored data."""
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
        existing_quantity = existing_detail.get('fill', {}).get('quantity') or existing_detail.get('quantity')
        existing_rationale = existing_detail.get('rationale')
        
        # Normalize for comparison (treat None and empty string as equivalent)
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
            fill=existing_decision.decision_detail.get('fill') if existing_decision.decision_detail else None,
            rejection=existing_decision.decision_detail.get('rejection') if existing_decision.decision_detail else None,
        )

    # For NEW decisions: check mutable session state
    if session.status not in (SimulationStatus.pending, SimulationStatus.active):
        raise HTTPException(status_code=400, detail=f"Cannot make decisions on '{session.status}' session")

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
    start_date = spec["start_date"]
    end_date = spec["end_date"]

    # Resolve provider for this session
    provider_id = _resolve_session_provider(session)
    if not provider_id:
        raise HTTPException(status_code=400, detail=f"No stored data available for {symbol}")

    # Determine next snapshot date from stored data
    ohlcv_df = _load_stored_ohlcv(symbol, start_date, end_date, db, provider_id=provider_id)
    if ohlcv_df.empty:
        raise HTTPException(status_code=400, detail=f"No stored data available for {symbol} from provider {provider_id}")

    current_snapshot = session.current_snapshot_date
    if current_snapshot is None:
        # Use first available date from stored data, not spec start_date
        next_date = ohlcv_df.index.min()
    else:
        next_date = _get_next_stored_snapshot_date(ohlcv_df, current_snapshot, start_date, end_date)

    if next_date is None:
        raise HTTPException(status_code=400, detail="No more snapshots available — simulation complete")

    if next_date not in ohlcv_df.index:
        raise HTTPException(status_code=400, detail=f"No data for {next_date}")

    # Current bar: what the user has seen when making the decision
    current_bar = ohlcv_df.loc[next_date]
    observed_close = float(current_bar['close'])
    
    # Find next executable bar (day after next_date)
    executable_date = _get_next_stored_snapshot_date(ohlcv_df, next_date, start_date, end_date)
    if executable_date is None or executable_date not in ohlcv_df.index:
        raise HTTPException(
            status_code=400, 
            detail=f"No next executable bar after {next_date}. Cannot execute without lookahead."
        )
    
    executable_bar = ohlcv_df.loc[executable_date]
    exec_open = float(executable_bar['open'])

    # Initialize simulator state from manual decisions
    simulator = _build_simulator(session, version)
    _rebuild_manual_state(session, simulator)

    decision_type = None
    decision_detail = {}
    fill_info = None
    rejection_info = None
    warnings = []

    action = payload.action.lower()

    if action == 'buy':
        if simulator.shares > 0:
            rejection_info = {
                'reason': 'already_in_position',
                'current_shares': simulator.shares,
                'intent_preserved': True,
            }
            decision_type = DecisionType.blocked_action
        else:
            quantity = payload.quantity or 1
            cost = quantity * exec_open
            commission = cost * simulator.commission_rate
            slippage = cost * simulator.slippage_rate
            total_cost = cost + commission + slippage

            if total_cost > simulator.cash:
                rejection_info = {
                    'reason': 'insufficient_cash',
                    'required': round(total_cost, 4),
                    'available': round(simulator.cash, 4),
                    'intent_preserved': True,
                }
                decision_type = DecisionType.blocked_action
            else:
                simulator.cash -= total_cost
                simulator.shares = quantity
                simulator.entry_price = exec_open
                simulator.entry_date = executable_date
                simulator._bought = True

                fill_info = {
                    'action': 'buy',
                    'quantity': quantity,
                    'price': round(exec_open, 4),
                    'commission': round(commission, 4),
                    'slippage': round(slippage, 4),
                    'total_cost': round(total_cost, 4),
                    'observed_date': next_date,
                    'observed_close': round(observed_close, 4),
                    'execution_date': executable_date,
                    'execution_rule': 'next_open_no_lookahead',
                }
                decision_type = DecisionType.simulated_entry

    elif action == 'sell':
        if simulator.shares == 0:
            rejection_info = {
                'reason': 'no_position_to_sell',
                'intent_preserved': True,
            }
            decision_type = DecisionType.blocked_action
        else:
            quantity = simulator.shares
            proceeds = quantity * exec_open
            commission = proceeds * simulator.commission_rate
            slippage = proceeds * simulator.slippage_rate
            net_proceeds = proceeds - commission - slippage

            simulator.cash += net_proceeds
            # Gross P&L: price difference only (excludes all fees)
            # Net P&L can be derived: gross_pnl - buy_fees - sell_fees
            gross_pnl = (exec_open - simulator.entry_price) * quantity
            simulator.shares = 0

            fill_info = {
                'action': 'sell',
                'quantity': quantity,
                'price': round(exec_open, 4),
                'commission': round(commission, 4),
                'slippage': round(slippage, 4),
                'net_proceeds': round(net_proceeds, 4),
                'gross_pnl': round(gross_pnl, 4),
                'observed_date': next_date,
                'observed_close': round(observed_close, 4),
                'execution_date': executable_date,
                'execution_rule': 'next_open_no_lookahead',
            }
            decision_type = DecisionType.simulated_exit
            simulator.entry_price = None
            simulator.entry_date = None
            simulator._bought = False

    elif action == 'hold':
        decision_type = DecisionType.no_action
        fill_info = {
            'action': 'hold',
            'observed_date': next_date,
            'observed_close': round(observed_close, 4),
            'execution_rule': 'no_action_no_execution',
        }
        rejection_info = None

    else:
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

    # Build decision detail
    decision_detail = {
        'action': action,
        'idempotency_key': payload.idempotency_key,
        'rationale': payload.rationale,
        'reflection': payload.reflection,
        'symbol': symbol,
        'snapshot_date': next_date,
    }
    if fill_info:
        decision_detail['fill'] = fill_info
    if rejection_info:
        decision_detail['rejection'] = rejection_info

    # Calculate current equity using observed close (not future data)
    current_equity = simulator._current_equity(observed_close)

    # Create decision record
    step_number = session.total_decisions + 1
    decision = SimulationDecision(
        session_id=session.id,
        step_number=step_number,
        snapshot_date=next_date,
        data_cutoff_at=session.data_cutoff_at,
        engine_version=SIMULATION_ENGINE_VERSION,
        decision_type=decision_type,
        decision_detail=decision_detail,
        idempotency_key=payload.idempotency_key,
        paper_cash=round(simulator.cash, 4),
        paper_position=simulator._position_dict(observed_close),
        paper_equity=round(current_equity, 4),
        warnings=warnings if warnings else None,
    )
    db.add(decision)

    # Update session state with optimistic locking
    # Use atomic conditional update to prevent race conditions
    current_version = session.state_version
    update_result = db.execute(
        SimulationSession.__table__.update().where(
            (SimulationSession.__table__.c.id == session.id) &
            (SimulationSession.__table__.c.state_version == current_version)
        ).values(
            current_snapshot_date=next_date,
            paper_equity=round(current_equity, 4),
            current_position_json=simulator._position_dict(observed_close),
            total_decisions=step_number,
            observed_days=session.observed_days + 1,
            state_version=current_version + 1,
            status=SimulationStatus.active if session.status == SimulationStatus.pending else session.status,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None) if session.status == SimulationStatus.pending else session.started_at,
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
    )
    
    # If update affected 0 rows, another request modified the session
    if update_result.rowcount == 0:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="State version conflict: session was modified by another request. Please reload and retry."
        )
    
    # Refresh session to get updated values
    db.refresh(session)

    # Emit audit event
    emit_event(
        db,
        entity_type="simulation", entity_id=session.id,
        event_type="manual_decision", actor_id=current_user.id,
        payload={
            "step_number": step_number,
            "action": action,
            "snapshot_date": next_date,
            "decision_type": decision_type.value,
            "paper_equity": round(current_equity, 4),
            "has_fill": fill_info is not None,
            "has_rejection": rejection_info is not None,
        },
        rules_locked=True,
        data_cutoff_at=session.data_cutoff_at,
    )

    db.commit()
    db.refresh(session)
    db.refresh(decision)

    return ManualDecisionResponse(
        session=session,
        decision=decision,
        fill=fill_info,
        rejection=rejection_info,
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
