"""Personal AI assistant API.

Research-advisory chat with a pluggable LLM provider adapter. Conversations
persist per user with user-controlled context (research drafts, approved
personal memories, instrument symbols, experiments). Assistant proposals
can be saved as research drafts in ``pending_review`` — human review is
always required before acceptance.

Safety invariants
-----------------
* LLM replies are NEVER trading authorization. This module does not touch
  execution intents, orders, or authorization state; the system prompt
  states this and the response schema carries an advisory notice.
* Only memories with privacy_scope=approved_for_personal_ai are fed to the
  provider; private_only memories are rejected with 400.
* The provider API key never appears in responses or errors.
* Quota: every successful assistant completion consumes 1 ``ai_usage`` unit
  (two-phase reserve → settle / release). Failed provider calls are
  persisted as error messages and release their reservation.
"""
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.auth.entitlements import (
    get_quota_used,
    reserve_quota,
    release_reservation,
    settle_reservation,
)
from app.auth.jwt import get_current_user
from app.auth.api_keys import check_api_key_scope
from app.config import get_settings
from app.data.real_instruments import get_instrument_by_id
from app.database import get_db
from app.lib.timeutil import utcnow
from app.models.ai_conversation import AIConversation, AIMessage
from app.models.model_research import PersonalResearchMemory, PrivacyScope
from app.models.pilot import UsageEvent, UsageEventType
from app.models.research_drafts import DraftStatus, ResearchDraft
from app.models.user import User
from app.services.ai_provider import (
    AIProviderError,
    CompletionRequest,
    get_ai_provider,
    estimate_cost_cents,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-assistant"])

MAX_TITLE_LENGTH = 200
MAX_MESSAGE_LENGTH = 8000
MAX_CONTEXT_DRAFTS = 10
MAX_CONTEXT_MEMORIES = 20
MAX_CONTEXT_SYMBOLS = 10
MAX_CONTEXT_EXPERIMENTS = 10
MAX_HISTORY_TURNS = 12

SAFETY_PREAMBLE = (
    "你是 Alethra 的個人 AI 研究助手。你只提供研究與教育性質的分析。"
    "你絕不授權、執行或建議任何交易授權；任何交易決定都必須由用戶本人"
    "經明確的意圖授權流程完成。回覆時引用用戶提供的脈絡（草稿、記憶、"
    "工具符號、實驗），不得虛構市場數據。"
)


# ── Schemas ─────────────────────────────────────────────────────────────────

class ConversationContextSchema(BaseModel):
    research_draft_ids: List[UUID] = Field(default_factory=list)
    memory_ids: List[UUID] = Field(default_factory=list)
    instrument_symbols: List[str] = Field(default_factory=list)
    experiment_ids: List[UUID] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=MAX_TITLE_LENGTH)
    context: ConversationContextSchema = Field(default_factory=ConversationContextSchema)


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=MAX_TITLE_LENGTH)
    context: Optional[ConversationContextSchema] = None


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class AIDraftCreate(BaseModel):
    """An AI-proposed draft awaiting human review."""
    title: str = Field(..., min_length=1, max_length=500)
    hypothesis: Optional[str] = None
    falsification_criteria: Optional[str] = None
    strategy_template: Optional[str] = Field(None, max_length=100)
    asset_symbol: Optional[str] = Field(None, max_length=50)
    benchmark_symbol: Optional[str] = Field(None, max_length=50)
    initial_cash: Optional[str] = Field(None, max_length=50)
    commission_rate: Optional[str] = Field(None, max_length=50)
    slippage_rate: Optional[str] = Field(None, max_length=50)
    research_rules: Optional[Dict[str, Any]] = None
    source_message_id: Optional[UUID] = None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _message_dict(message: AIMessage) -> Dict[str, Any]:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "error_detail": message.error_detail,
        "provider": message.provider,
        "model": message.model,
        "prompt_tokens": message.prompt_tokens,
        "completion_tokens": message.completion_tokens,
        "cost_cents": message.cost_cents,
        "created_at": message.created_at.isoformat(),
    }


def _conversation_dict(conversation: AIConversation, with_messages: bool) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": str(conversation.id),
        "title": conversation.title,
        "context": conversation.context or {},
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }
    if with_messages:
        data["messages"] = [_message_dict(m) for m in conversation.messages]
    return data


def _get_conversation_or_404(db: Session, conversation_id: UUID, user: User) -> AIConversation:
    conversation = (
        db.query(AIConversation)
        .filter(AIConversation.id == conversation_id, AIConversation.user_id == user.id)
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _validate_context_ownership(db: Session, ctx: ConversationContextSchema, user: User) -> None:
    if len(ctx.research_draft_ids) > MAX_CONTEXT_DRAFTS:
        raise HTTPException(400, detail=f"At most {MAX_CONTEXT_DRAFTS} research drafts in context")
    if len(ctx.memory_ids) > MAX_CONTEXT_MEMORIES:
        raise HTTPException(400, detail=f"At most {MAX_CONTEXT_MEMORIES} memories in context")
    if len(ctx.instrument_symbols) > MAX_CONTEXT_SYMBOLS:
        raise HTTPException(400, detail=f"At most {MAX_CONTEXT_SYMBOLS} instrument symbols in context")
    if len(ctx.experiment_ids) > MAX_CONTEXT_EXPERIMENTS:
        raise HTTPException(400, detail=f"At most {MAX_CONTEXT_EXPERIMENTS} experiments in context")

    if ctx.research_draft_ids:
        rows = db.query(ResearchDraft.id).filter(
            ResearchDraft.id.in_(ctx.research_draft_ids),
            ResearchDraft.user_id == user.id,
        ).all()
        found = {r[0] for r in rows}
        missing = [str(i) for i in ctx.research_draft_ids if i not in found]
        if missing:
            raise HTTPException(404, detail={"error": "research_drafts_not_found", "ids": missing})

    if ctx.memory_ids:
        rows = db.query(PersonalResearchMemory).filter(
            PersonalResearchMemory.id.in_(ctx.memory_ids),
            PersonalResearchMemory.owner_id == user.id,
        ).all()
        found = {m.id for m in rows}
        missing = [str(i) for i in ctx.memory_ids if i not in found]
        if missing:
            raise HTTPException(404, detail={"error": "memories_not_found", "ids": missing})
        not_approved = [m for m in rows if m.privacy_scope != PrivacyScope.approved_for_personal_ai]
        if not_approved:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "memory_privacy_scope",
                    "ids": [str(m.id) for m in not_approved],
                    "message": "僅 privacy_scope=approved_for_personal_ai 的記憶可用於個人 AI 脈絡",
                },
            )

    for symbol in ctx.instrument_symbols:
        if not isinstance(symbol, str) or not symbol.strip():
            raise HTTPException(400, detail="Instrument symbols must be non-empty strings")
        if get_instrument_by_id(symbol.strip()) is None:
            raise HTTPException(400, detail=f"Unknown instrument symbol: {symbol}")

    if ctx.experiment_ids:
        from app.models.experiment import Experiment
        rows = db.query(Experiment.id).filter(
            Experiment.id.in_(ctx.experiment_ids),
            Experiment.owner_id == user.id,
        ).all()
        found = {r[0] for r in rows}
        missing = [str(i) for i in ctx.experiment_ids if i not in found]
        if missing:
            raise HTTPException(404, detail={"error": "experiments_not_found", "ids": missing})


def _build_system_prompt(db: Session, conversation: AIConversation) -> str:
    """Assemble the system prompt from the user-controlled context.

    Only privacy-approved memories are ever included (enforced at context
    validation; re-checked here defensively).
    """
    ctx = conversation.context or {}
    parts: List[str] = [SAFETY_PREAMBLE]

    def _as_uuids(values) -> List[UUID]:
        parsed = []
        for v in values or []:
            try:
                parsed.append(UUID(str(v)))
            except (ValueError, TypeError, AttributeError):
                continue
        return parsed

    draft_ids = _as_uuids(ctx.get("research_draft_ids"))
    if draft_ids:
        drafts = (
            db.query(ResearchDraft)
            .filter(ResearchDraft.id.in_(draft_ids), ResearchDraft.user_id == conversation.user_id)
            .all()
        )
        if drafts:
            lines = ["用戶選定的研究草稿："]
            for d in drafts:
                lines.append(
                    f"- [draft:{d.id}] 標題：{d.title}｜狀態：{d.status.value}｜"
                    f"假設：{(d.hypothesis or '未填')[:300]}"
                )
            parts.append("\n".join(lines))

    memory_ids = _as_uuids(ctx.get("memory_ids"))
    if memory_ids:
        memories = (
            db.query(PersonalResearchMemory)
            .filter(
                PersonalResearchMemory.id.in_(memory_ids),
                PersonalResearchMemory.owner_id == conversation.user_id,
                PersonalResearchMemory.privacy_scope == PrivacyScope.approved_for_personal_ai,
            )
            .all()
        )
        if memories:
            lines = ["用戶選定的個人研究記憶："]
            for m in memories:
                lines.append(f"- [memory:{m.id}] {m.content[:400]}")
            parts.append("\n".join(lines))

    symbols = ctx.get("instrument_symbols") or []
    if symbols:
        lines = ["用戶關注的工具："]
        for s in symbols:
            instrument = get_instrument_by_id(str(s))
            if instrument is not None:
                lines.append(
                    f"- [symbol:{instrument.instrument_id}] {instrument.name} "
                    f"({instrument.market.value})"
                )
        if len(lines) > 1:
            parts.append("\n".join(lines))

    experiment_ids = _as_uuids(ctx.get("experiment_ids"))
    if experiment_ids:
        from app.models.experiment import Experiment
        experiments = (
            db.query(Experiment)
            .filter(
                Experiment.id.in_(experiment_ids),
                Experiment.owner_id == conversation.user_id,
            )
            .all()
        )
        if experiments:
            lines = ["用戶選定的實驗："]
            for e in experiments:
                lines.append(f"- [experiment:{e.id}] {getattr(e, 'name', '')}")
            parts.append("\n".join(lines))

    parts.append(
        "回覆規則：簡潔、結構化、可引用上述 [draft:...] [memory:...] [symbol:...] "
        "[experiment:...] 標籤。若需提議研究草稿，明確標示為「提議（待人工審核）」，"
        "並說明這只是研究建議，不構成交易授權。"
    )
    return "\n\n".join(parts)


# ── Provider status / connection test ───────────────────────────────────────

@router.get("/status")
def ai_status(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    settings = get_settings()
    try:
        provider = get_ai_provider()
    except AIProviderError as exc:
        raise HTTPException(status_code=503, detail=exc.message)
    return {
        "provider": provider.name,
        "model": settings.ai_model,
        "mode": "mock" if provider.name == "mock" else "live",
        "api_key_configured": bool(settings.ai_api_key.strip()) and settings.ai_api_key != "not-used-in-phase-1",
        "base_url": provider.base_url if provider.name == "openai_compatible" else None,
        "timeout_seconds": settings.ai_timeout_seconds,
        "max_output_tokens": settings.ai_max_output_tokens,
        "advisory_notice": "AI 回覆僅供研究參考，不構成交易授權",
    }


@router.post("/test-connection")
def ai_test_connection(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    try:
        provider = get_ai_provider()
    except AIProviderError as exc:
        return {"ok": False, "detail": exc.message, "kind": exc.kind}
    result = provider.test_connection()
    return {
        "ok": result.ok,
        "detail": result.detail,
        "latency_ms": result.latency_ms,
        "kind": result.kind,
        "provider": provider.name,
    }


# ── Conversations ───────────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    conversations = (
        db.query(AIConversation)
        .filter(AIConversation.user_id == current_user.id)
        .order_by(AIConversation.updated_at.desc())
        .all()
    )
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "context": c.context or {},
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
            "message_count": len(c.messages),
        }
        for c in conversations
    ]


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    _validate_context_ownership(db, payload.context, current_user)
    conversation = AIConversation(
        user_id=current_user.id,
        title=(payload.title or "新對話").strip() or "新對話",
        context=payload.context.model_dump(mode="json"),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_dict(conversation, with_messages=True)


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    conversation = _get_conversation_or_404(db, conversation_id, current_user)
    return _conversation_dict(conversation, with_messages=True)


@router.patch("/conversations/{conversation_id}")
def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    conversation = _get_conversation_or_404(db, conversation_id, current_user)
    if payload.title is not None:
        conversation.title = payload.title.strip()
    if payload.context is not None:
        _validate_context_ownership(db, payload.context, current_user)
        conversation.context = payload.context.model_dump(mode="json")
    db.commit()
    db.refresh(conversation)
    return _conversation_dict(conversation, with_messages=True)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    conversation = _get_conversation_or_404(db, conversation_id, current_user)
    db.delete(conversation)
    db.commit()


# ── Messages ────────────────────────────────────────────────────────────────

@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Send a user message and persist the assistant reply.

    Quota: 1 ``ai_usage`` unit is reserved before the provider call and
    settled on success; provider failures release the reservation (no
    charge) and persist an error message on the thread.
    """
    conversation = _get_conversation_or_404(db, conversation_id, current_user)

    reservation = reserve_quota(db, current_user.id, "ai_usage", amount=1)

    user_message = AIMessage(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
    )
    db.add(user_message)
    db.flush()

    history = [
        {"role": m.role, "content": m.content}
        for m in conversation.messages[-MAX_HISTORY_TURNS:]
        if m.status == "completed"
    ]
    # Ensure the just-added user message is the last turn.
    history.append({"role": "user", "content": payload.content})

    request = CompletionRequest(messages=[{"role": "system", "content": _build_system_prompt(db, conversation)}] + history)

    try:
        result = get_ai_provider().complete(request)
    except AIProviderError as exc:
        release_reservation(db, reservation.id)
        assistant = AIMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="（AI 供應商呼叫失敗，本次不計用量）",
            status="error",
            error_detail=exc.message,
        )
        db.add(assistant)
        conversation.updated_at = utcnow()
        db.commit()
        db.refresh(assistant)
        return _message_dict(assistant)

    cost_cents = estimate_cost_cents(result.prompt_tokens, result.completion_tokens)
    assistant = AIMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=result.content,
        status="completed",
        provider=result.provider,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_cents=cost_cents,
    )
    db.add(assistant)
    conversation.updated_at = utcnow()
    settle_reservation(db, reservation.id)
    db.commit()
    db.refresh(assistant)

    # Usage/cost accounting is best-effort: a failure here must not fail
    # the (already persisted) conversation turn.
    try:
        from app.services.billing import record_api_call
        record_api_call(
            db,
            user_id=current_user.id,
            endpoint=f"/ai/conversations/{conversation.id}/messages",
            model=result.model,
            tokens_used=result.prompt_tokens + result.completion_tokens,
            cost=cost_cents,
            metadata={"conversation_id": str(conversation.id), "provider": result.provider},
        )
    except Exception:
        logger.warning("Failed to record AI usage record", exc_info=True)

    response = _message_dict(assistant)
    response["quota_used"] = get_quota_used(db, current_user.id, "ai_usage")
    return response


# ── AI-proposed research drafts (always human-reviewed) ─────────────────────

@router.post("/conversations/{conversation_id}/drafts", status_code=status.HTTP_201_CREATED)
def create_draft_from_conversation(
    conversation_id: UUID,
    payload: AIDraftCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Save an AI proposal as a research draft in ``pending_review``.

    The draft is linked back to its conversation for traceability. It is
    NOT accepted — acceptance still goes through the human approve route,
    which validates completeness.
    """
    conversation = _get_conversation_or_404(db, conversation_id, current_user)

    if payload.source_message_id is not None:
        message = (
            db.query(AIMessage)
            .join(AIConversation, AIMessage.conversation_id == AIConversation.id)
            .filter(
                AIMessage.id == payload.source_message_id,
                AIConversation.user_id == current_user.id,
            )
            .first()
        )
        if not message:
            raise HTTPException(404, detail="Source message not found")

    from app.models.research_drafts import ResearchDraftAuditEvent

    draft = ResearchDraft(
        user_id=current_user.id,
        source_conversation_id=conversation.id,
        title=payload.title.strip(),
        hypothesis=payload.hypothesis,
        falsification_criteria=payload.falsification_criteria,
        strategy_template=payload.strategy_template,
        asset_symbol=payload.asset_symbol,
        benchmark_symbol=payload.benchmark_symbol,
        initial_cash=payload.initial_cash,
        commission_rate=payload.commission_rate,
        slippage_rate=payload.slippage_rate,
        research_rules=payload.research_rules,
        status=DraftStatus.PENDING_REVIEW,
    )
    db.add(draft)
    db.flush()

    db.add(
        ResearchDraftAuditEvent(
            draft_id=draft.id,
            user_id=current_user.id,
            event_type="research_draft_created_from_ai",
            event_data={
                "conversation_id": str(conversation.id),
                "source_message_id": str(payload.source_message_id) if payload.source_message_id else None,
                "review_required": True,
            },
        )
    )
    db.commit()
    db.refresh(draft)

    from app.api.schemas import ResearchDraftResponse
    data = ResearchDraftResponse.model_validate(draft).model_dump(mode="json")
    data["source_conversation_id"] = str(conversation.id)
    data["review_notice"] = "AI 提議草稿待人工審核；接受前不會用於任何執行流程"
    return data
