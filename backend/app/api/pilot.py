from datetime import datetime
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models.user import User
from app.models.pilot import (
    PilotApplication,
    PilotApplicationStatus,
    PilotEntitlement,
    UsageEvent,
    UsageEventType,
    CostRecord,
    CostType,
    CostSource,
    SupportLog,
    ResolutionStatus,
    ProductHypothesis,
    HypothesisStatus,
)
from app.auth.jwt import get_current_user, require_admin
from app.api.schemas import (
    PilotApplicationCreate,
    PilotApplicationResponse,
    PilotEntitlementCreate,
    PilotEntitlementUpdate,
    PilotEntitlementResponse,
    UsageEventCreate,
    UsageEventResponse,
    CostRecordCreate,
    CostRecordResponse,
    SupportLogCreate,
    SupportLogUpdate,
    SupportLogResponse,
    ProductHypothesisCreate,
    ProductHypothesisUpdate,
    ProductHypothesisResponse,
    PilotFunnelSummary,
    ActivationMetrics,
    CostSummary,
    AdminDashboardResponse,
    ProductHypothesisDashboardResponse,
    PILOT_DISCLAIMER,
)

router = APIRouter(prefix="/pilot", tags=["pilot"])


# ── Pilot Applications ──


@router.post("/applications", response_model=PilotApplicationResponse, status_code=201)
def submit_pilot_application(
    data: PilotApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (
        db.query(PilotApplication)
        .filter(PilotApplication.user_id == current_user.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Pilot application already submitted")

    application = PilotApplication(
        user_id=current_user.id,
        intended_user_type=data.intended_user_type,
        current_research_workflow=data.current_research_workflow,
        tools_currently_used=data.tools_currently_used,
        primary_pain_point=data.primary_pain_point,
        expected_usage_frequency=data.expected_usage_frequency,
        willingness_to_interview=data.willingness_to_interview,
        consent_to_contact=data.consent_to_contact,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


@router.get("/applications/me", response_model=PilotApplicationResponse)
def get_my_application(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    application = (
        db.query(PilotApplication)
        .filter(PilotApplication.user_id == current_user.id)
        .first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="No pilot application found")
    return application


@router.get("/applications", response_model=List[PilotApplicationResponse])
def list_applications(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = db.query(PilotApplication).order_by(PilotApplication.created_at.desc())
    if status_filter:
        query = query.filter(PilotApplication.status == status_filter)
    return query.all()


@router.put("/applications/{application_id}/status", response_model=PilotApplicationResponse)
def update_application_status(
    application_id: UUID,
    new_status: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    valid_statuses = {s.value for s in PilotApplicationStatus}
    if new_status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"Invalid status. Allowed: {valid_statuses}")

    application = db.query(PilotApplication).filter(PilotApplication.id == application_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    application.status = PilotApplicationStatus(new_status)
    db.commit()
    db.refresh(application)
    return application


# ── Pilot Entitlements ──


@router.post("/entitlements", response_model=PilotEntitlementResponse, status_code=201)
def grant_entitlement(
    data: PilotEntitlementCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    existing = db.query(PilotEntitlement).filter(PilotEntitlement.user_id == data.user_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Entitlement already exists for this user")

    expiry = datetime.strptime(data.expiry_date, "%Y-%m-%d") if data.expiry_date else None

    entitlement = PilotEntitlement(
        user_id=data.user_id,
        pilot_access=data.pilot_access,
        research_run_limit=data.research_run_limit,
        ai_usage_limit=data.ai_usage_limit,
        export_limit=data.export_limit,
        forward_simulation_limit=data.forward_simulation_limit,
        expiry_date=expiry,
        support_tier=data.support_tier,
        granted_by=current_user.id,
    )
    db.add(entitlement)
    db.commit()
    db.refresh(entitlement)
    return entitlement


@router.get("/entitlements/me", response_model=PilotEntitlementResponse)
def get_my_entitlement(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entitlement = (
        db.query(PilotEntitlement)
        .filter(PilotEntitlement.user_id == current_user.id)
        .first()
    )
    if not entitlement:
        raise HTTPException(status_code=404, detail="No entitlement found")

    if entitlement.expiry_date and entitlement.expiry_date < datetime.utcnow():
        raise HTTPException(status_code=403, detail="Pilot access has expired")

    if not entitlement.pilot_access:
        raise HTTPException(status_code=403, detail="Pilot access is disabled")

    return entitlement


@router.put("/entitlements/{entitlement_id}", response_model=PilotEntitlementResponse)
def update_entitlement(
    entitlement_id: UUID,
    data: PilotEntitlementUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    entitlement = db.query(PilotEntitlement).filter(PilotEntitlement.id == entitlement_id).first()
    if not entitlement:
        raise HTTPException(status_code=404, detail="Entitlement not found")

    update_data = data.model_dump(exclude_unset=True)
    if "expiry_date" in update_data:
        if update_data["expiry_date"]:
            update_data["expiry_date"] = datetime.strptime(update_data["expiry_date"], "%Y-%m-%d")

    for field, value in update_data.items():
        setattr(entitlement, field, value)

    db.commit()
    db.refresh(entitlement)
    return entitlement


@router.get("/entitlements", response_model=List[PilotEntitlementResponse])
def list_entitlements(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return db.query(PilotEntitlement).order_by(PilotEntitlement.created_at.desc()).all()


# ── Usage Events ──


@router.post("/usage-events", response_model=UsageEventResponse, status_code=201)
def record_usage_event(
    data: UsageEventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = UsageEvent(
        user_id=current_user.id,
        event_type=data.event_type,
        metadata_json=data.metadata_json,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("/usage-events/me", response_model=List[UsageEventResponse])
def get_my_usage_events(
    event_type: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(UsageEvent).filter(UsageEvent.user_id == current_user.id)
    if event_type:
        query = query.filter(UsageEvent.event_type == event_type)
    return query.order_by(UsageEvent.created_at.desc()).limit(min(limit, 500)).all()


@router.get("/usage-events", response_model=List[UsageEventResponse])
def list_all_usage_events(
    event_type: Optional[str] = None,
    user_id: Optional[UUID] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = db.query(UsageEvent)
    if event_type:
        query = query.filter(UsageEvent.event_type == event_type)
    if user_id:
        query = query.filter(UsageEvent.user_id == user_id)
    return query.order_by(UsageEvent.created_at.desc()).limit(min(limit, 1000)).all()


# ── Cost Records ──


@router.post("/costs", response_model=CostRecordResponse, status_code=201)
def create_cost_record(
    data: CostRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    record = CostRecord(
        owner_id=data.owner_id,
        cost_type=data.cost_type,
        amount=data.amount,
        currency=data.currency,
        source=data.source,
        method_of_estimation=data.method_of_estimation,
        linked_entity=data.linked_entity,
        description=data.description,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/costs", response_model=List[CostRecordResponse])
def list_cost_records(
    cost_type: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = db.query(CostRecord)
    if cost_type:
        query = query.filter(CostRecord.cost_type == cost_type)
    if source:
        query = query.filter(CostRecord.source == source)
    return query.order_by(CostRecord.recorded_at.desc()).limit(min(limit, 1000)).all()


# ── Support Logs ──


@router.post("/support-logs", response_model=SupportLogResponse, status_code=201)
def create_support_log(
    data: SupportLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    log = SupportLog(
        user_id=data.user_id,
        category=data.category,
        time_spent_minutes=data.time_spent_minutes,
        resolution_status=data.resolution_status,
        linked_entity=data.linked_entity,
        recurring_issue_tag=data.recurring_issue_tag,
        notes=data.notes,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.put("/support-logs/{log_id}", response_model=SupportLogResponse)
def update_support_log(
    log_id: UUID,
    data: SupportLogUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    log = db.query(SupportLog).filter(SupportLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Support log not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(log, field, value)

    db.commit()
    db.refresh(log)
    return log


@router.get("/support-logs", response_model=List[SupportLogResponse])
def list_support_logs(
    user_id: Optional[UUID] = None,
    category: Optional[str] = None,
    recurring_issue_tag: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = db.query(SupportLog)
    if user_id:
        query = query.filter(SupportLog.user_id == user_id)
    if category:
        query = query.filter(SupportLog.category == category)
    if recurring_issue_tag:
        query = query.filter(SupportLog.recurring_issue_tag == recurring_issue_tag)
    return query.order_by(SupportLog.created_at.desc()).limit(min(limit, 1000)).all()


# ── Product Hypotheses ──


@router.post("/hypotheses", response_model=ProductHypothesisResponse, status_code=201)
def create_hypothesis(
    data: ProductHypothesisCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    hypothesis = ProductHypothesis(
        statement=data.statement,
        evidence_criteria=data.evidence_criteria,
        evidence_notes=data.evidence_notes,
    )
    db.add(hypothesis)
    db.commit()
    db.refresh(hypothesis)
    return hypothesis


@router.put("/hypotheses/{hypothesis_id}", response_model=ProductHypothesisResponse)
def update_hypothesis(
    hypothesis_id: UUID,
    data: ProductHypothesisUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    hypothesis = db.query(ProductHypothesis).filter(ProductHypothesis.id == hypothesis_id).first()
    if not hypothesis:
        raise HTTPException(status_code=404, detail="Hypothesis not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(hypothesis, field, value)

    db.commit()
    db.refresh(hypothesis)
    return hypothesis


@router.get("/hypotheses", response_model=List[ProductHypothesisResponse])
def list_hypotheses(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = db.query(ProductHypothesis).order_by(ProductHypothesis.created_at.desc())
    if status_filter:
        query = query.filter(ProductHypothesis.status == status_filter)
    return query.all()


# ── Admin Validation Dashboard ──


@router.get("/admin/dashboard", response_model=AdminDashboardResponse)
def admin_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from app.models.user import User as UserModel

    total_users = db.query(func.count(UserModel.id)).scalar() or 0

    applications_submitted = db.query(func.count(PilotApplication.id)).scalar() or 0
    applications_approved = (
        db.query(func.count(PilotApplication.id))
        .filter(PilotApplication.status == PilotApplicationStatus.approved)
        .scalar()
        or 0
    )

    event_counts = {}
    for et in UsageEventType:
        count = (
            db.query(func.count(UsageEvent.id))
            .filter(UsageEvent.event_type == et)
            .scalar()
            or 0
        )
        event_counts[et.value] = count

    funnel = PilotFunnelSummary(
        total_users=total_users,
        applications_submitted=applications_submitted,
        applications_approved=applications_approved,
        first_experiment_created=event_counts.get("first_experiment_created", 0),
        first_run_completed=event_counts.get("first_run_completed", 0),
        strategy_locked=event_counts.get("strategy_locked", 0),
        forward_simulation_started=event_counts.get("forward_simulation_started", 0),
        export_generated=event_counts.get("export_generated", 0),
        support_request_created=event_counts.get("support_request_created", 0),
    )

    activated_users = (
        db.query(func.count(func.distinct(UsageEvent.user_id)))
        .filter(UsageEvent.event_type == UsageEventType.first_experiment_created)
        .scalar()
        or 0
    )
    completed_run_users = (
        db.query(func.count(func.distinct(UsageEvent.user_id)))
        .filter(UsageEvent.event_type == UsageEventType.first_run_completed)
        .scalar()
        or 0
    )

    activation = ActivationMetrics(
        total_signed_up=total_users,
        activated_at_least_one_experiment=activated_users,
        activation_rate=round(activated_users / total_users, 4) if total_users > 0 else 0.0,
        completed_first_run=completed_run_users,
        completion_rate=round(completed_run_users / total_users, 4) if total_users > 0 else 0.0,
    )

    total_cost = db.query(func.coalesce(func.sum(CostRecord.amount), 0.0)).scalar()
    total_actual = (
        db.query(func.coalesce(func.sum(CostRecord.amount), 0.0))
        .filter(CostRecord.source == CostSource.actual)
        .scalar()
    )
    total_estimate = (
        db.query(func.coalesce(func.sum(CostRecord.amount), 0.0))
        .filter(CostRecord.source == CostSource.estimate)
        .scalar()
    )

    type_breakdown_rows = (
        db.query(CostRecord.cost_type, func.sum(CostRecord.amount))
        .group_by(CostRecord.cost_type)
        .all()
    )
    breakdown_by_type = {str(ct.value): float(amt) for ct, amt in type_breakdown_rows}

    active_user_count = max(activated_users, 1)
    cost_summary = CostSummary(
        total_cost=float(total_cost),
        total_actual=float(total_actual),
        total_estimate=float(total_estimate),
        cost_per_user=round(float(total_cost) / total_users, 4) if total_users > 0 else 0.0,
        cost_per_active_user=round(float(total_cost) / active_user_count, 4),
        breakdown_by_type=breakdown_by_type,
    )

    return AdminDashboardResponse(
        funnel=funnel,
        activation=activation,
        cost_summary=cost_summary,
    )


# ── Product Hypothesis Dashboard ──


@router.get("/admin/hypotheses-dashboard", response_model=ProductHypothesisDashboardResponse)
def hypotheses_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    hypotheses = db.query(ProductHypothesis).order_by(ProductHypothesis.created_at.desc()).all()

    by_status = {}
    for hs in HypothesisStatus:
        count = (
            db.query(func.count(ProductHypothesis.id))
            .filter(ProductHypothesis.status == hs)
            .scalar()
            or 0
        )
        by_status[hs.value] = count

    return ProductHypothesisDashboardResponse(
        hypotheses=hypotheses,
        total=len(hypotheses),
        by_status=by_status,
    )
