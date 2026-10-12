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
)
from app.engine.simulation import ForwardSimulator, SIMULATION_ENGINE_VERSION
from app.engine.backtest import ENGINE_VERSION, compute_config_hash
from app.data.demo_data import get_demo_ohlcv, get_trade_dates
from app.services import emit_event

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


def _get_next_snapshot_date(version: ExperimentVersion, current_snapshot_date: str) -> str:
    all_dates = get_trade_dates()
    spec = version.strategy_spec
    start_date = spec["start_date"]
    end_date = spec["end_date"]

    eligible = [d for d in all_dates if d > current_snapshot_date and start_date <= d <= end_date]
    if not eligible:
        return None
    return eligible[0]


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
    cutoff = spec.get("end_date", version.dataset_cutoff_at)

    session = SimulationSession(
        experiment_version_id=version.id,
        owner_id=current_user.id,
        initial_virtual_cash=payload.initial_virtual_cash,
        data_cutoff_at=cutoff,
        status=SimulationStatus.pending,
        simulation_policy_version=SIMULATION_ENGINE_VERSION,
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
