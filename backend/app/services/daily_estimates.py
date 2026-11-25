"""The daily model-estimate lane: estimates only, and off until a skill says so.

Three things the owner grants separately, and this lane reads only the third:

1. consent to use recorded decisions for **training** (``personalization_training``,
   ``app/services/personalization.py``);
2. which **model** a skill account uses (``PersonalModelSelection``, enforced by
   the inference gate);
3. consent to **run an estimate daily** for named skill accounts — this module's
   ``personal_daily_estimate`` purpose, whose ``scope_json`` names the skill
   units and the instruments the owner wants read.

Nothing here copies the authorization contract. One estimate is produced by
:func:`app.api.personal_models._run_personal_prediction`, the same function the
owner's own ``POST …/predict`` calls, with ``via="daily_service"`` — so the gate
that refuses a wrong skill, an unselected version, a mismatched artifact or a
stale feature schema refuses the service exactly as loudly as it refuses a
person. A fabricated ``Request`` object would look identical in a test and be
worthless as evidence.

What the lane does when it runs: one ``usage_records`` row per estimate (the
existing billing contract), one summary row in ``learning_jobs``, and the
estimate itself in that row's ``result_json``. What it never does: write a
``DecisionRecord``, place an order, advance a replay, or confirm a training
label.

The billing row, not the summary, is what the lane remembers by. A tick that
dies leaves its summary unwritten while every estimate it had already paid for
sits in ``usage_records``, so :func:`settled_estimates` reads that ledger and a
restart re-prices nothing. ``result_json`` is the same window reconstructed
from those rows beside whatever this run added, which makes it a report the
operator can re-derive rather than the only place the answer lives.
"""

from __future__ import annotations

import logging
import traceback
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.lib.uuid_spelling import stored_as
from app.lib.timeutil import utcnow_aware
from app.models.audit_event import AuditEvent
from app.models.billing import UsageRecord
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.personal_model_selection import PersonalModelSelection
from app.models.personalization import PersonalizationConsent
from app.models.user import User
from app.services.kill_switch import is_kill_switch_active

logger = logging.getLogger(__name__)

DAILY_ESTIMATE_PURPOSE = "personal_daily_estimate"
DAILY_ESTIMATE_CONSENT_VERSION = "1"
DAILY_ESTIMATE_JOB_TYPE = "daily_model_estimate"

# The label for "a person asked right now", as it is filed in the ledger row's
# ``config_json["scope"]``. It is the only thing that separates the button from
# the clock: both serve the same window, the same business key and the same
# durable seat. The refusal book, which is a report of what one run decided and
# spent nothing on, is keyed to the clock alone — see
# :func:`_summary_recorded_skills`.
MANUAL_SCOPE = "manual"

# The value this lane's billing rows carry in ``metadata_json["via"]``. It is
# written by :func:`_estimate_one` through the formal prediction function and
# read back by :func:`settled_estimates` from the same constant, so the two ends
# of the dedup cannot drift into different spellings of each other.
DAILY_SERVICE_VIA = "daily_service"

# The fourth hour: research owns 02:00 UTC, the simulation replay 03:00, and the
# weekly analysis Sunday 04:00. An estimate that fires beside another tick would
# inherit whatever that tick left in progress; an hour of its own does not.
DAILY_ESTIMATE_HOUR_UTC = 5

# One owner's declared scope, bounded. The estimate lane reserves ``ai_usage``
# quota per call through the existing billing contract, so an unbounded scope
# list is an unbounded bill the owner never wrote.
MAX_SKILL_UNITS_PER_OWNER = 10
MAX_INSTRUMENTS_PER_SKILL = 8

# The statuses this lane will read. ``active`` is the account the owner is using;
# ``paused`` is asked about and refused by name, because pausing is the owner's
# way of saying "stop calling me" and a silent skip would drop that instruction
# the moment the panel showed no estimate and no reason.
SERVED_PORTFOLIO_STATUSES = (PortfolioStatus.active,)
REFUSED_PORTFOLIO_STATUSES = (
    PortfolioStatus.paused,
    PortfolioStatus.retired,
    PortfolioStatus.completed,
)

NOT_FOR_ZH = ("市場方向預測", "交易是否正確或有利潤", "投資建議")


class EstimateRefused(Exception):
    """A declared scope the platform will not serve, named by its code."""

    def __init__(self, code: str, detail: Any = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _as_uuid(value: Any) -> UUID:
    return UUID(str(value))


def _canonical_skill_id(value: Any) -> str:
    """One skill id in the spelling the scope is stored in, or the text as given.

    Comparison, not validation: a withdrawal that names an id this scope does
    not contain removes nothing and still answers "is it off?" truthfully.
    """
    try:
        return str(_as_uuid(value))
    except (ValueError, TypeError, AttributeError):
        return str(value)


# ── the third consent: which skills, for which instruments ─────────────


def get_daily_estimate_consent(
    db: Session, owner_id: UUID
) -> Optional[PersonalizationConsent]:
    """The owner's live daily-execution consent, or ``None``.

    Filtered on its own ``purpose`` value. Training consent is a different row
    and granting it enables nothing here: a query that read
    ``personalization_training`` would report an opt-in the owner never made.
    """
    return (
        db.query(PersonalizationConsent)
        .filter(
            PersonalizationConsent.owner_id == owner_id,
            PersonalizationConsent.purpose == DAILY_ESTIMATE_PURPOSE,
            PersonalizationConsent.revoked_at.is_(None),
        )
        .order_by(PersonalizationConsent.granted_at.desc())
        .first()
    )


def _normalise_entry(entry: Any) -> Optional[dict]:
    """One declared skill, in the shape the lane works with, or ``None``.

    An entry with no instrument list still reads back, because the owner named a
    skill account and the honest answer about it is a refusal by name
    (``no_instruments_declared``) — dropping the row would leave the panel
    saying "never declared" about a declaration that exists. Only an entry with
    no skill id, or whose instrument field is not a list at all, is unreadable
    and dropped.
    """
    if not isinstance(entry, dict):
        return None
    skill = entry.get("skill_unit_id")
    instruments = entry.get("instrument_ids")
    if not skill or not isinstance(instruments, list):
        return None
    cleaned = [str(value) for value in instruments if isinstance(value, str) and value]
    max_age = entry.get("max_data_age_days")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 1:
        max_age = None
    return {
        "skill_unit_id": str(skill),
        "instrument_ids": list(dict.fromkeys(cleaned)),
        "max_data_age_days": max_age,
    }


def declared_scope(
    consent: Optional[PersonalizationConsent]
) -> List[Tuple[str, dict]]:
    """``(skill_unit_id, entry)`` pairs the owner wrote.

    Ids are stored as strings, because the scope survives being read by another
    process and a UUID object's spelling is not part of the contract.
    """
    if consent is None or not isinstance(consent.scope_json, dict):
        return []
    out: List[Tuple[str, dict]] = []
    for entry in consent.scope_json.get("skill_units") or []:
        normalised = _normalise_entry(entry)
        if normalised is not None:
            out.append((normalised["skill_unit_id"], normalised))
    return out


def owned_skill_unit(
    db: Session, owner_id: UUID, raw_skill: str
) -> Optional[PaperPortfolio]:
    """This owner's skill account, in whichever UUID spelling the row was stored.

    The same read tolerance the selection path uses (``_owned_skill_unit`` in
    ``app/services/personal_selection.py``), because an account opened through
    the paper-trading create route historically stored the dashed spelling. An
    id that belongs to someone else is answered exactly like an id that does not
    exist, so neither a scope write nor a status read enumerates accounts.
    """
    try:
        skill_id = _as_uuid(raw_skill)
    except (ValueError, TypeError, AttributeError):
        raise EstimateRefused("malformed_skill_unit_id", raw_skill)
    return (
        db.query(PaperPortfolio)
        .filter(
            stored_as(PaperPortfolio.id, skill_id),
            stored_as(PaperPortfolio.owner_id, owner_id),
        )
        .first()
    )


def _validate_targets(
    db: Session, owner_id: UUID, targets: List[dict]
) -> List[Tuple[PaperPortfolio, dict]]:
    """Check ownership and status of every named skill before writing scope."""
    if not targets:
        raise EstimateRefused(
            "empty_scope",
            "daily estimate consent needs at least one skill account",
        )
    resolved: List[Tuple[PaperPortfolio, dict]] = []
    seen: set = set()
    for raw_entry in targets:
        entry = _normalise_entry(raw_entry)
        if entry is None:
            named = isinstance(raw_entry, dict) and bool(raw_entry.get("skill_unit_id"))
            raise EstimateRefused(
                "no_instruments_declared" if named else "malformed_skill_unit_id",
                raw_entry,
            )
        raw_skill = entry["skill_unit_id"]
        instruments = entry["instrument_ids"]
        if raw_skill in seen:
            continue
        seen.add(raw_skill)
        if len(seen) > MAX_SKILL_UNITS_PER_OWNER:
            raise EstimateRefused(
                "scope_too_large",
                f"at most {MAX_SKILL_UNITS_PER_OWNER} skill accounts per owner",
            )
        if len(instruments) > MAX_INSTRUMENTS_PER_SKILL:
            raise EstimateRefused(
                "instrument_list_too_long",
                f"at most {MAX_INSTRUMENTS_PER_SKILL} instruments per skill",
            )
        portfolio = owned_skill_unit(db, owner_id, raw_skill)
        if portfolio is None:
            raise EstimateRefused("skill_unit_not_found", raw_skill)
        if portfolio.status in REFUSED_PORTFOLIO_STATUSES:
            raise EstimateRefused(f"skill_{portfolio.status.value}", raw_skill)
        if portfolio.status not in SERVED_PORTFOLIO_STATUSES:
            raise EstimateRefused("skill_status_unreadable", raw_skill)
        resolved.append((portfolio, entry))
    return resolved


def grant_daily_estimate(
    db: Session, owner: User, targets: List[dict]
) -> dict:
    """Replace the owner's daily-execution consent with this explicit scope.

    Replacing, not merging: a partial edit that left a retired skill in the old
    scope would keep an account the owner stopped using on the estimate list.
    """
    resolved = _validate_targets(db, owner.id, targets)
    now = utcnow_aware()
    active = (
        db.query(PersonalizationConsent)
        .filter(
            PersonalizationConsent.owner_id == owner.id,
            PersonalizationConsent.purpose == DAILY_ESTIMATE_PURPOSE,
            PersonalizationConsent.revoked_at.is_(None),
        )
        .all()
    )
    for row in active:
        row.revoked_at = now
    consent = PersonalizationConsent(
        owner_id=owner.id,
        purpose=DAILY_ESTIMATE_PURPOSE,
        consent_version=DAILY_ESTIMATE_CONSENT_VERSION,
        scope_json={
            "skill_units": [
                {
                    "skill_unit_id": str(portfolio.id),
                    "instrument_ids": entry["instrument_ids"],
                    "max_data_age_days": entry["max_data_age_days"],
                }
                for portfolio, entry in resolved
            ]
        },
        granted_at=now,
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent_payload(db, owner.id)


def withdraw_daily_estimate(
    db: Session, owner: User, skill_unit_ids: Optional[Sequence[str]] = None
) -> dict:
    """Stop the estimates. All named skills, or every skill if none is named.

    Withdrawal is a write, not a suggestion: the row is closed with
    ``revoked_at`` and stays readable as history. A skill the owner does not
    hold is reported as absent rather than refused, because the answer the owner
    needs is "is it off?", and "off" is true either way.
    """
    consent = get_daily_estimate_consent(db, owner.id)
    if consent is None:
        return consent_payload(db, owner.id)
    if skill_unit_ids is None:
        consent.revoked_at = utcnow_aware()
        db.commit()
        return consent_payload(db, owner.id)

    drop = {_canonical_skill_id(value) for value in skill_unit_ids}
    kept = [
        entry
        for entry in (consent.scope_json or {}).get("skill_units") or []
        if _canonical_skill_id(entry.get("skill_unit_id")) not in drop
    ]
    if not kept:
        consent.revoked_at = utcnow_aware()
    else:
        # A new row, not an in-place edit: the row the next read answers from has
        # to be the row that was actually changed, and the closed one keeps the
        # scope that was in force while the estimates ran.
        consent.revoked_at = utcnow_aware()
        db.add(
            PersonalizationConsent(
                owner_id=owner.id,
                purpose=DAILY_ESTIMATE_PURPOSE,
                consent_version=DAILY_ESTIMATE_CONSENT_VERSION,
                scope_json={"skill_units": kept},
                granted_at=utcnow_aware(),
            )
        )
    db.commit()
    return consent_payload(db, owner.id)


def consent_payload(db: Session, owner_id: UUID) -> dict:
    consent = get_daily_estimate_consent(db, owner_id)
    scope = declared_scope(consent)
    return {
        # Default off is a fact about this row, not a default value substituted
        # at read time: with no consent row the service has nothing to serve.
        "enabled": bool(scope),
        "purpose": DAILY_ESTIMATE_PURPOSE,
        "consent_id": str(consent.id) if consent else None,
        "consent_version": consent.consent_version if consent else None,
        "granted_at": consent.granted_at.isoformat() if consent else None,
        "skill_units": [entry for _, entry in scope],
        "limits": {
            "skill_units_per_owner": MAX_SKILL_UNITS_PER_OWNER,
            "instruments_per_skill": MAX_INSTRUMENTS_PER_SKILL,
        },
        "not_for": list(NOT_FOR_ZH),
    }


# ── the tick ───────────────────────────────────────────────────────────


def estimate_window(now: Optional[datetime] = None) -> str:
    """The dedup key: one UTC calendar day per skill account.

    A window the ledger can be searched for without decoding anything, because
    the lane is a daily clock and the day is what an owner means by "today".
    """
    now = now or datetime.now(timezone.utc)
    return now.date().isoformat()


def estimate_owner_ids(db: Session, *, limit: int) -> List[UUID]:
    """Owners who declared a daily estimate scope, ordered and bounded.

    This is what makes the lane off by default: an account the owner never
    named is not in this list, so a scheduler firing on a host where nobody
    opted in serves nobody and records why.
    """
    rows = (
        db.query(PersonalizationConsent.owner_id)
        .filter(
            PersonalizationConsent.purpose == DAILY_ESTIMATE_PURPOSE,
            PersonalizationConsent.revoked_at.is_(None),
        )
        .distinct()
        .order_by(PersonalizationConsent.owner_id)
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def billed_estimates_by_skill(db: Session, owner_id: UUID) -> Dict[str, List[dict]]:
    """Every estimate this owner was billed for, grouped by skill, oldest first.

    This is the durable half of the dedup, and the half a crashed tick cannot
    erase. ``_run_personal_prediction`` settles the quota reservation and writes
    this row inside the same commit (``app/api/personal_models.py``: settle at
    :688, then ``record_api_call_no_commit`` at :692, then the one ``db.commit()``
    at :730), so the row answers two of the four window questions at once — the
    estimate exists, and the unit for that skill and instrument was paid for —
    and neither is reachable without the other. A ``learning_jobs`` summary is
    written later, in a different commit, and a tick that died before it got
    there left nothing behind that the ledger does not still hold.

    Rows are scanned and their metadata read in Python rather than matched as
    JSON in SQL, because the same lane must read the same way on SQLite and
    PostgreSQL. There is no cap on the scan: a limit is how a settled estimate
    ages out of the answer and gets bought a second time.
    """
    rows = (
        db.query(UsageRecord)
        .filter(UsageRecord.user_id == owner_id)
        .order_by(UsageRecord.created_at)
        .all()
    )
    grouped: Dict[str, List[dict]] = {}
    for row in rows:
        meta = row.metadata_json or {}
        if meta.get("via") != DAILY_SERVICE_VIA:
            continue
        skill = meta.get("skill_unit_id")
        if not skill:
            continue
        grouped.setdefault(_canonical_skill_id(skill), []).append(
            {
                "skill_unit_id": _canonical_skill_id(skill),
                "instrument_id": meta.get("instrument_id"),
                "inference_id": str(row.id),
                "model_version_id": meta.get("model_version_id"),
                "artifact_sha256": meta.get("artifact_sha256"),
                "as_of_date": meta.get("as_of_date"),
                "feature_bar_date": meta.get("feature_bar_date"),
                "billed_at": row.created_at.isoformat() if row.created_at else None,
                # The verdict itself is in the summary of the run that produced
                # it, not in the billing row. Naming it absent is the honest
                # spelling: a recovered estimate can be proven and cited by its
                # inference id without the lane pretending to still hold its
                # answer.
                "predicted_action": None,
                "action_probabilities": None,
                "classes": None,
                "verdict_durable": False,
            }
        )
    return grouped


def settled_estimates(
    db: Session, owner_id: UUID, window: str
) -> List[dict]:
    """Every estimate this owner was billed for in one window, read off the bill.

    The window is the ``as_of_date`` the estimate was filed under, not the row's
    own clock: a retry that crosses UTC midnight and one that does not must give
    the same answer.
    """
    settled: List[dict] = []
    for entries in billed_estimates_by_skill(db, owner_id).values():
        settled.extend(
            item
            for item in entries
            if _run_day(item["as_of_date"]) == _run_day(window)
        )
    return settled


def billed_estimate(
    db: Session, owner_id: UUID, window: str, skill_unit_id: str, instrument_id: str
) -> Optional[dict]:
    """One business key, asked of the billing ledger as it stands right now.

    The row, not just a yes: an owner who is told "this window is already paid
    for" is owed the inference id that was paid for, and a refusal that cannot
    name it is a refusal that could not have read the ledger either.
    """
    for item in settled_estimates(db, owner_id, window):
        if (item["skill_unit_id"], item["instrument_id"]) == (
            _canonical_skill_id(skill_unit_id),
            instrument_id,
        ):
            return item
    return None


def estimate_billed(
    db: Session, owner_id: UUID, window: str, skill_unit_id: str, instrument_id: str
) -> bool:
    """Whether one business key already sits in the billing ledger."""
    return billed_estimate(db, owner_id, window, skill_unit_id, instrument_id) is not None


def claim_estimate(
    db: Session, owner_id: UUID, window: str, skill_unit_id: str, instrument_id: str
) -> bool:
    """Take the first-billing seat for one business key, or report that it is taken.

    ``True`` means the window already holds a bill for that key, so the caller must
    not price it.

    The seat is a transaction, not a flag. What a tick reads at its opening
    (:func:`settled_estimates`) is a *description* of the window: between that read
    and the commit that makes a bill durable
    (``app/api/personal_models.py:730`` — reservation settle, billing row, one
    commit) another worker can pass the same test against the same snapshot, and
    both pay for the same skill and instrument. So each item re-reads the ledger
    once this session holds the seat, and on PostgreSQL that re-read and the
    estimate that follows sit inside one transaction-scoped advisory lock keyed by
    the business key. ``pg_advisory_xact_lock`` is released by exactly the commit
    that makes the bill durable, which is what turns read-then-bill into
    claim-then-bill: whoever takes the seat second re-reads and finds it filled.

    SQLite has no such function. There the re-read still happens in a fresh
    transaction, so an item that another session billed between this tick opening
    and the item's own turn is refused — but two OS processes racing on one file
    are not serialised by that, and the lane must say which of the two it proved.

    Which statement holds the seat, written out because "the caller is still
    running" is not a fact about a lock. The ``db.commit()`` below *closes* the
    transaction the tick had open; it opens nothing. The statement that takes the
    seat — the ``SELECT pg_advisory_xact_lock(hashtextextended(...))`` a few lines
    later — is then the first statement of a new transaction, because this session
    is a ``sessionmaker(autocommit=False)`` one (``app/database.py:20``) and
    autobegins on the next statement after a commit. The seat belongs to *that*
    transaction and ends with it, whether this frame is still mid-call or returned
    long ago.

    The ledger re-read is the statement right after the lock: :func:`estimate_billed`
    → :func:`billed_estimate` → :func:`settled_estimates`, i.e. one ``SELECT`` over
    ``usage_records`` for this owner — the scan whose query is built in
    :func:`billed_estimates_by_skill` — whose rows are then matched on the business
    key in Python. The engine sets no isolation level (``app/database.py:19``), so
    PostgreSQL runs its default READ COMMITTED and every statement gets its own
    fresh snapshot; that is what lets this re-read see a bill committed before it
    ran. It is not a claim about a snapshot that was already open — a worker reading
    under REPEATABLE READ, or one that read the ledger *before* being granted the
    lock, still sees only what its own snapshot held.

    What the second worker's re-read waits for, then, is the lock: it is granted
    when the first holder's transaction ends, and on the success path that ending is
    the one commit that also makes the bill durable. The caller hands this same
    session to the shared prediction function, and its reservation settle
    (``app/api/personal_models.py:688``) and billing row (its
    :func:`app.services.billing.record_api_call_no_commit` call at :692) hold no
    commit of their own, so ``:730`` both writes the bill and releases the seat.
    Release and durability are the same instant, and a re-read that runs after the
    grant therefore cannot precede it.

    Every other exit gives the seat back having billed nothing, which is what lets
    an unbilled failure be priced on a later tick, and each names the write that
    ends it. Two of the three ways an item can fail end the seat with a rollback:
    :func:`_estimate_one` raising :class:`EstimateRefused` — no selection, a version
    or record that is not this owner's, a bar older than the declared cut-off — and
    any ``HTTPException`` the gate raises before it holds anything to settle, whether
    from the switches and authorization checks that precede
    (``app.api.personal_models._refuse_when_switched_off``,
    :func:`app.api.personal_models._assert_personal_version`,
    :func:`app.api.personal_models._require_inference_authorization`, the
    missing-artifact, missing-feature and schema-mismatch refusals) or from
    :func:`app.auth.entitlements.reserve_quota` (:252) refusing the seat for want of
    quota. Both are met by a ``db.rollback()`` in the item loop of
    :func:`_estimates_for_owner`, which writes nothing.

    The prediction function's own four failure branches are the exception, and they
    end the seat *earlier* than the loop does: each commits a reservation release
    before it raises (``release_reservation`` at :660, :670, :674, :678, each followed
    by ``db.commit()`` at :661, :671, :675, :679 — the helper itself only flushes), so
    the transaction carrying this seat is already closed by that commit. The loop's
    rollback then ends only the empty transaction the session autobegins afterwards.
    Either way the ledger gains no bill.

    A commit and a rollback end a transaction equally, and the advisory lock is
    released by the transaction ending rather than by the frame returning, so no
    path — including the catch-all ``except Exception`` the tick keeps so one broken
    estimate cannot end the run — leaves a dead estimate holding a window's seat.
    """
    db.commit()
    bind = db.bind
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(CAST(:key AS text), CAST(0 AS bigint)))"
            ),
            {"key": f"{owner_id}|{skill_unit_id}|{instrument_id}|{window}"},
        )
    return estimate_billed(db, owner_id, window, skill_unit_id, instrument_id)


def _summary_recorded_skills(db: Session, owner_id: UUID, window: str) -> List[str]:
    """Skill ids a *closed* clock summary of this window already named.

    The second half of the dedup, and the half that only says what a completed
    run decided. A skill refused here is not retried inside the window: the
    refusal is that window's record, and a retry storm would buy the day twice.
    Unlike :func:`settled_estimates` this reads the summary, so it sees refusals
    (which spent nothing) but not an estimate whose run died before closing.

    Only the clock's own rows are read. A button press files the same window, so
    a summary-based rule that counted it would let one refused press — which
    spent nothing — decide that the 05:00 service has nothing to try, and would
    let a refused *clock* run silence the owner who asks in person. Whether
    anything was paid for is answered by the billing ledger for both entries
    alike; this function answers only "has the schedule already reported on this
    skill today", which is a question about the schedule's own run.
    """
    jobs = (
        db.query(LearningJob)
        .filter(
            LearningJob.user_id == owner_id,
            LearningJob.job_type == DAILY_ESTIMATE_JOB_TYPE,
        )
        .order_by(LearningJob.created_at)
        .all()
    )
    served: List[str] = []
    for job in jobs:
        config = job.config_json or {}
        if config.get("scope") == MANUAL_SCOPE:
            continue
        if config.get("window") != window:
            continue
        for key in ("results", "refused", "recovered"):
            for entry in (job.result_json or {}).get(key) or []:
                if isinstance(entry, dict) and entry.get("skill_unit_id"):
                    served.append(str(entry["skill_unit_id"]))
    return served


def window_state(db: Session, owner_id: UUID, window: str) -> dict:
    """One owner's window, per skill account, as the durable state describes it.

    Four things an operator has to be able to tell apart, because the lane can
    be in any of them at the same moment on different accounts:

    * ``execution_claimed`` — a ``learning_jobs`` row naming this window exists,
      in whatever status, which is what a tick leaves when it opened the row and
      died. It is evidence the tick existed, not a claim that the window was
      served;
    * ``estimate_persisted`` and ``quota_settled`` — read together off the
      billing row, because one commit writes both. The reservation table is not
      keyed to the usage row, so nothing here pretends to join them;
    * ``summary_written`` — whether a closed run's ``result_json`` names the
      skill. Absent here means only that the report was never written; the
      estimate and the bill are, and the summary below is rebuilt from them.

    ``state`` then answers what a retry owes: ``settled`` (nothing, and re-billing
    it would be the defect), ``partial`` (the outstanding instruments only),
    ``refused`` (a recorded refusal, not retried inside the window by the
    established contract), ``not_started`` (everything, if the owner still
    declares it), and ``undeclared`` (billed, then removed from the scope — the
    bill stands, the scope no longer asks).
    """
    scope = {skill: entry for skill, entry in declared_scope(get_daily_estimate_consent(db, owner_id))}
    settled = settled_estimates(db, owner_id, window)
    by_skill: Dict[str, List[dict]] = {}
    for entry in settled:
        by_skill.setdefault(entry["skill_unit_id"], []).append(entry)
    summary_skills = set(_summary_recorded_skills(db, owner_id, window))

    claimed = False
    jobs = (
        db.query(LearningJob)
        .filter(
            LearningJob.user_id == owner_id,
            LearningJob.job_type == DAILY_ESTIMATE_JOB_TYPE,
        )
        .order_by(LearningJob.created_at)
        .all()
    )
    for job in jobs:
        if (job.config_json or {}).get("window") == window:
            claimed = True

    rows: List[dict] = []
    for skill_id in sorted(set(scope) | set(by_skill)):
        entry = scope.get(skill_id) or {}
        declared_instruments = list(entry.get("instrument_ids") or [])
        billed = by_skill.get(skill_id, [])
        settled_pairs = {item["instrument_id"] for item in billed}
        outstanding = [
            instrument
            for instrument in declared_instruments
            if instrument not in settled_pairs
        ]
        if skill_id not in scope:
            state = "undeclared"
        elif billed and not outstanding:
            state = "settled"
        elif billed:
            state = "partial"
        elif skill_id in summary_skills:
            state = "refused"
        else:
            state = "not_started"
        rows.append(
            {
                "skill_unit_id": skill_id,
                "declared": skill_id in scope,
                "instruments_declared": declared_instruments,
                "instruments_settled": sorted(
                    str(item) for item in settled_pairs if item is not None
                ),
                "instruments_outstanding": outstanding,
                "estimate_persisted": bool(billed),
                "quota_settled": bool(billed),
                "inference_ids": [item["inference_id"] for item in billed],
                "model_version_ids": sorted(
                    {str(item["model_version_id"]) for item in billed if item["model_version_id"]}
                ),
                "summary_written": skill_id in summary_skills,
                "state": state,
                "retry_owes": outstanding if state in ("partial", "not_started") else [],
            }
        )

    return {
        "window": window,
        "execution_claimed": claimed,
        "estimates_billed": len(settled),
        "skills": rows,
    }


def live_declared_ids(db: Session, owner_id: UUID) -> List[str]:
    """The skill accounts the owner's consent names *now*, read this instant.

    The tick takes its scope from a row it read before the first estimate. A
    withdrawal that lands while the clock is still walking that list is the
    owner's most recent instruction, so each skill is asked of the live row
    rather than of the snapshot — the same rule the pause check already follows.
    """
    return [skill_id for skill_id, _ in declared_scope(get_daily_estimate_consent(db, owner_id))]


def current_selection(db: Session, owner_id: UUID, skill_unit_id: UUID):
    """The selection row that says which version this skill account uses now.

    Only a locator. The formal gate re-derives the same answer inside
    :func:`app.services.model_activation.check_model_version_inference_authorization`
    and refuses if this is not the newest selection for the record, so a stale
    read cannot pin a version the owner has since replaced.
    """
    return (
        db.query(PersonalModelSelection)
        .filter(
            PersonalModelSelection.owner_id == owner_id,
            PersonalModelSelection.skill_unit_id == skill_unit_id,
        )
        .order_by(
            PersonalModelSelection.created_at.desc(),
            PersonalModelSelection.id.desc(),
        )
        .first()
    )


def _run_day(as_of_date: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(as_of_date)[:10])
    except (TypeError, ValueError):
        return None


def _data_provenance(db: Session, instrument_id: str) -> dict:
    """Which provider's series this instrument's bars came from, as recorded.

    Disclosed, never a refusal: a lane whose bars are synthetic is still the
    lane the isolated tests run on, and an estimate that silently stopped being
    served because a metadata row is missing would look like a dead scheduler.
    What the owner needs is to see ``data_nature`` beside the estimate.
    """
    from app.models.market_data import MarketDataMetadata

    row = (
        db.query(MarketDataMetadata)
        .filter(MarketDataMetadata.instrument_id == instrument_id)
        .order_by(MarketDataMetadata.updated_at.desc())
        .first()
    )
    if row is None:
        return {"provider_id": None, "data_nature": None}
    return {"provider_id": row.provider_id, "data_nature": row.data_nature}


def _estimate_one(
    db: Session,
    owner: User,
    portfolio: PaperPortfolio,
    instrument_id: str,
    as_of_date: str,
    *,
    max_data_age_days: Optional[int] = None,
) -> dict:
    """One estimate, through the formal prediction function and no other door."""
    from app.api.personal_models import PredictRequest, _run_personal_prediction
    from app.models.model_research import ModelResearchRecord, ModelVersion
    from app.services.personal_dataset import features_as_of

    selection = current_selection(db, owner.id, portfolio.id)
    if selection is None:
        # No selection is not "use whatever the record alias points at": that
        # would let one skill's rollback decide another skill's daily estimate.
        raise EstimateRefused("strategy_scope_missing", str(portfolio.id))

    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == selection.version_id)
        .one_or_none()
    )
    if version is None:
        raise EstimateRefused("model_version_not_found", str(selection.version_id))
    record = (
        db.query(ModelResearchRecord)
        .filter(ModelResearchRecord.id == version.model_research_record_id)
        .one_or_none()
    )
    if record is None or record.owner_id != owner.id:
        raise EstimateRefused("model_version_not_found", str(version.id))

    # The freshness read happens here, before the gate, because the gate spends
    # ``ai_usage`` quota and an owner who declared "do not price me off a
    # two-week-old bar" should not be billed for a refusal they asked for. This
    # is the same ``features_as_of`` the gate calls; a cut-off it cannot produce
    # is left to the gate, so the service reports the same code a person gets.
    _, _, cut, exclusion = features_as_of(db, instrument_id, as_of_date)
    provenance = _data_provenance(db, instrument_id)
    run_day = _run_day(as_of_date)
    data_age_days = None
    if cut is not None and run_day is not None:
        bar_day = _run_day(cut)
        if bar_day is not None:
            data_age_days = max(0, (run_day - bar_day).days)
    if (
        exclusion is None
        and max_data_age_days is not None
        and data_age_days is not None
        and data_age_days > max_data_age_days
    ):
        raise EstimateRefused(
            "stale_market_data",
            f"{instrument_id}: last bar {cut} is {data_age_days} days old, "
            f"past the {max_data_age_days} declared",
        )

    answer = _run_personal_prediction(
        db,
        version=version,
        record=record,
        body=PredictRequest(
            instrument_id=instrument_id,
            as_of_date=as_of_date,
            skill_unit_id=portfolio.id,
        ),
        via=DAILY_SERVICE_VIA,
        current_user=owner,
        resolved_from="daily_estimate_selection",
    )
    return {
        "skill_unit_id": str(portfolio.id),
        "instrument_id": instrument_id,
        "inference_id": answer["inference_id"],
        "model_version_id": answer["resolved_version"]["model_version_id"],
        "artifact_sha256": answer["artifact_sha256"],
        "as_of_date": answer["as_of"]["as_of_date"],
        "feature_bar_date": answer["as_of"]["feature_bar_date"],
        "data_age_days": data_age_days,
        "max_data_age_days": max_data_age_days,
        "provider_id": provenance["provider_id"],
        "data_nature": provenance["data_nature"],
        "predicted_action": answer["predicted_action"],
        "action_probabilities": answer["action_probabilities"],
        "classes": answer["classes"],
        "consent_currently_active": answer["consent_currently_active"],
        "disclaimer_zh": answer["disclaimer_zh"],
        "not_for": answer["not_for"],
    }


def _estimates_for_owner(
    db: Session,
    owner_id: UUID,
    *,
    scope: str,
    job_type: str,
    scheduled_for: Optional[str],
    job_id: Optional[UUID] = None,
    window: Optional[str] = None,
    only_skill_unit_id: Optional[UUID] = None,
) -> dict:
    """One owner's declared skills, each estimated once per window or refused by name.

    Every run of this lane is filed under one UTC calendar-day window, including
    a run a person started by hand: the button and the clock are two ways of
    asking for the same service, so they share the window, the business key
    ``(owner, skill, instrument, window)`` and the durable seat in
    :func:`claim_estimate`. What still differs is only who asked, recorded in
    ``config_json["scope"]``, and the refusal book below, which belongs to the
    clock alone. A press therefore cannot buy what the schedule already paid
    for, and a schedule cannot re-price a day a press paid for — whichever came
    first, the second one names the existing ``inference_id`` and spends nothing.

    The ledger row is opened before the first estimate, so a tick that died
    midway leaves a ``running`` record of having existed rather than looking like
    it never happened. That row is evidence the tick ran, not a claim on the
    window — nothing in ``learning_jobs`` is unique per window, so two workers
    both open a row and neither is stopped by the other. What stops the second
    one is :func:`claim_estimate`: each item is re-read off the billing ledger
    inside its own transaction before a unit is spent on it, so an estimate
    already paid for is named and left alone, and a skill the dead run never
    reached is served.
    """
    from app.workers.learning_scheduler import _open_job

    # No caller is trusted to have named the window, because a run that names no
    # window dedups against nothing: every ledger lookup below would answer
    # "empty" and the second press would be billed beside the first.
    window = window or estimate_window()

    owner = db.query(User).filter(User.id == owner_id).one_or_none()
    if owner is None:
        raise EstimateRefused("owner_not_found", str(owner_id))

    consent = get_daily_estimate_consent(db, owner_id)
    pairs = declared_scope(consent)
    if only_skill_unit_id is not None:
        wanted = str(only_skill_unit_id)
        pairs = [(skill, entry) for skill, entry in pairs if skill == wanted]
    config: Dict[str, Any] = {
        "scope": scope,
        "purpose": DAILY_ESTIMATE_PURPOSE,
        "statuses_served": [status.value for status in SERVED_PORTFOLIO_STATUSES],
        "kill_switch_scope": KillSwitchScope.inference.value,
        "window": window,
    }

    job = _open_job(db, owner_id, job_type=job_type, config=config, job_id=job_id)
    # Two books, two questions. The billing ledger is the durable one: an
    # instrument it names for this window has been paid for, whatever the run
    # that paid for it managed to write afterwards, and a skill whose every
    # declared instrument is in it is not retried. The closed summaries add the
    # refusals, which spent nothing and are that window's record — for the
    # clock's own rows only, so that a refusal that cost nothing silences
    # nothing outside the run that recorded it. Neither is a process flag, so a
    # retry, a restart or a second worker reads the same answer off the same
    # rows.
    recovered = settled_estimates(db, owner_id, window)
    settled_pairs = {
        (item["skill_unit_id"], item["instrument_id"]) for item in recovered
    }
    summary_skills = (
        set()
        if scope == MANUAL_SCOPE
        else set(_summary_recorded_skills(db, owner_id, window))
    )

    results: List[dict] = []
    refused: List[dict] = []
    errors: List[str] = []
    # ``errors`` is the log-shaped string this lane has always written, and four
    # pinned nodes read it verbatim, so it stays exactly as it was. ``failures``
    # is the same event in the shape a card can quote: it names the account and
    # the instrument and nothing else, because the exception text behind it is
    # the platform's own failure, not something the owner can act on and not
    # something that belongs in a response body.
    failures: List[dict] = []

    for skill_id, entry in pairs:
        # Both stop controls are asked again as the tick walks the scope, not only
        # at its door: one tick fans out over every opted-in owner and that takes
        # minutes, so a switch pulled or a consent withdrawn while the clock is on
        # its way should stop the skills that have not started. ``commit`` ends the
        # read snapshot first — PostgreSQL re-reads per statement under READ
        # COMMITTED and SQLite does not, and the lane must not answer from a stale
        # row for want of a transaction boundary. Nothing is pending here: the
        # ledger row this tick owns was committed the moment it opened.
        db.commit()
        try:
            stopped = is_kill_switch_active(db, KillSwitchScope.inference)
            stop_reason = "kill_switch_active"
        except Exception as exc:
            logger.error("Unable to re-read the kill switch, refusing: %s", exc)
            stopped, stop_reason = True, "kill_switch_unreadable"
        if stopped:
            refused.append({"skill_unit_id": skill_id, "reason": stop_reason})
            continue
        # The estimate already in flight cannot be un-spent, so the boundary this
        # lane honours is "not started yet": the withdrawal refuses the skills whose
        # first call has not happened, by name, in the same ledger row.
        if skill_id not in live_declared_ids(db, owner_id):
            refused.append({"skill_unit_id": skill_id, "reason": "consent_withdrawn"})
            continue
        instruments = entry["instrument_ids"]
        portfolio = owned_skill_unit(db, owner_id, skill_id)
        if portfolio is None:
            refused.append({"skill_unit_id": skill_id, "reason": "skill_unit_not_found"})
            continue
        if portfolio.status in REFUSED_PORTFOLIO_STATUSES:
            refused.append(
                {"skill_unit_id": skill_id, "reason": f"skill_{portfolio.status.value}"}
            )
            continue
        if portfolio.status not in SERVED_PORTFOLIO_STATUSES:
            refused.append(
                {"skill_unit_id": skill_id, "reason": "skill_status_unreadable"}
            )
            continue
        pending = [
            instrument for instrument in instruments if (skill_id, instrument) not in settled_pairs
        ]
        # A skill the window has already billed in full is refused, not re-run,
        # and the answer is read off the bill rather than off the summary — so a
        # run that died before closing cannot turn a paid estimate into a second
        # one. A skill with one instrument paid and the next not is not refused:
        # it is served exactly the part it never got. Which of the two books said
        # no is recorded, because "the clock already reported on this" and "this
        # was already paid for" are different promises: only the second one means
        # the owner's quota went out the door.
        if skill_id in summary_skills:
            refused.append(
                {
                    "skill_unit_id": skill_id,
                    "reason": "already_estimated_this_window",
                    "decided_by": "clock_summary",
                }
            )
            continue
        if instruments and not pending:
            billed_here = [
                item for item in recovered if item["skill_unit_id"] == skill_id
            ]
            refused.append(
                {
                    "skill_unit_id": skill_id,
                    "reason": "already_estimated_this_window",
                    "decided_by": "billing_ledger",
                    "inference_ids": [item["inference_id"] for item in billed_here],
                }
            )
            continue
        if not instruments:
            refused.append(
                {"skill_unit_id": skill_id, "reason": "no_instruments_declared"}
            )
            continue
        for instrument_id in pending:
            # The tick-open snapshot is not the authority over one item. Whatever
            # another worker billed since this tick read the ledger is refused
            # here, by business key, before a second unit is spent on it. The
            # seat is taken for a hand press exactly as for the clock: it is the
            # billing row, read inside its own transaction, that decides whether
            # this key has been paid for, and never the summary of some run.
            if claim_estimate(db, owner_id, window, skill_id, instrument_id):
                taken = billed_estimate(
                    db, owner_id, window, skill_id, instrument_id
                ) or {}
                refused.append(
                    {
                        "skill_unit_id": skill_id,
                        "instrument_id": instrument_id,
                        "reason": "already_estimated_this_window",
                        "decided_by": "billing_ledger",
                        "inference_id": taken.get("inference_id"),
                    }
                )
                continue
            try:
                results.append(
                    _estimate_one(
                        db,
                        owner,
                        portfolio,
                        instrument_id,
                        window,
                        max_data_age_days=entry["max_data_age_days"],
                    )
                )
            except EstimateRefused as exc:
                db.rollback()
                refused.append(
                    {
                        "skill_unit_id": skill_id,
                        "instrument_id": instrument_id,
                        "reason": exc.code,
                        "detail": _plain(exc.detail),
                    }
                )
            except HTTPException as exc:
                # The formal gate's own refusal, kept as the gate wrote it: the
                # code and the message an owner would have seen at the HTTP door
                # are the reason recorded in this row.
                db.rollback()
                reason, door = _refusal_code(exc)
                refused.append(
                    {
                        "skill_unit_id": skill_id,
                        "instrument_id": instrument_id,
                        "reason": reason,
                        "refused_by": door,
                        "detail": _plain(exc.detail),
                        "http_status": exc.status_code,
                    }
                )
            except Exception as exc:  # a broken estimate must not end the tick
                db.rollback()
                errors.append(f"{skill_id}/{instrument_id}: {exc}")
                failures.append(
                    {
                        "skill_unit_id": skill_id,
                        "instrument_id": instrument_id,
                        "reason": "engine_failure",
                    }
                )
                logger.warning(
                    "Daily estimate failed for skill %s instrument %s: %s\n%s",
                    skill_id,
                    instrument_id,
                    exc,
                    traceback.format_exc(),
                )

    if errors and not results:
        status_value = "failed"
    elif errors or refused:
        status_value = "partial"
    else:
        # Nothing broke and nothing was refused. An empty ``results`` here means
        # the tick was handed a scope with nothing in it, which is not a day of
        # estimates and must not read as one.
        status_value = "completed" if results else "partial"

    job.status = status_value
    job.completed_at = utcnow_aware()
    # Two counts of the same ledger at two different times, named for the time
    # each one answers: what the window had already cost before this run opened
    # (the carry-over the dedup acted on) and what it costs now that this run's
    # own estimates have committed. Reading the second one off the bill rather
    # than adding it to ``len(results)`` is what keeps the window's account true
    # for the run that inherits a half-billed day, and it is re-derivable by
    # anyone who can read ``usage_records`` — the summary is a description, not
    # the record.
    billed_now = len(settled_estimates(db, owner_id, window))
    job.result_json = {
        "window": window,
        "skills_declared": len(pairs),
        "estimated": len(results),
        "refused_count": len(refused),
        "failed_count": len(errors),
        # Everything this window had already been billed for before this run
        # opened, whether or not any earlier run ever described it.
        "billed_before_this_run": len(recovered),
        "billed_in_window": billed_now,
        "recovered": recovered,
        "results": results,
        "refused": refused,
        "errors": errors,
        "failures": failures,
    }
    job.error_details = "\n".join(errors) if errors else None
    db.commit()
    return {
        "job_id": str(job.id),
        "user_id": str(owner_id),
        "status": status_value,
        "estimated": len(results),
        "refused": len(refused),
        # The third way an item does not land. A caller that reads only
        # ``estimated`` and ``refused`` sees ``1 + 0`` for a tick that answered one
        # account and had another's worker die, and calls it a clean success —
        # which is the same misreading the row's own ``partial`` status exists to
        # prevent, so the count that says it travels with the response.
        "failed": len(errors),
    }


def _refusal_code(exc: HTTPException) -> Tuple[str, Optional[str]]:
    """``(the condition, the door)`` read out of the gate's own detail shape.

    The inference gate raises ``{"code": "inference_not_authorized",
    "reason_code": "artifact_mismatch"}``: the code names which door refused and
    the reason names the condition the owner can act on, so a ledger that stored
    only the first would report "not authorized" for a day the owner needs to
    hear "the artifact no longer matches". Billing spells its refusal
    ``{"error": "quota_exceeded"}`` and the market-data door
    ``{"code": "missing_market_data"}``, so all three spellings are read rather
    than one; anything else is named by its status, because "the tick stopped
    here" still beats "the tick stopped".
    """
    detail = exc.detail
    if isinstance(detail, dict):
        found = {}
        for key in ("code", "reason_code", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value:
                found[key] = value
        condition = found.get("reason_code") or found.get("code") or found.get("error")
        if condition:
            door = found.get("code")
            return condition, door if door and door != condition else None
    if isinstance(detail, str) and detail:
        return detail, None
    return f"http_{exc.status_code}", None


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def run_daily_estimate_tick(
    *,
    scope: str,
    window: Optional[str],
    user_id: Optional[UUID] = None,
    job_id: Optional[UUID] = None,
    only_skill_unit_id: Optional[UUID] = None,
) -> dict:
    """One estimate tick over every owner that declared a scope.

    The kill switch, the fan-out ceiling and the owner resolution come from the
    shared tick body, with two things this lane genuinely has a reason to differ
    on: which accounts it serves (owners with a live daily-estimate consent, not
    owners with an account) and which switch stops it (``inference``, because
    that is what an estimate is).
    """
    from app.workers.learning_scheduler import _run_tick

    return _run_tick(
        scope=scope,
        job_type=DAILY_ESTIMATE_JOB_TYPE,
        # ``None`` at the shared body: its once-a-day guard is per owner, and
        # this lane dedups per owner *and* skill. The window travels as work.
        scheduled_for=None,
        tick_owner=_estimates_for_owner,
        user_id=user_id,
        job_id=job_id,
        owners_of=estimate_owner_ids,
        kill_switch_scope=KillSwitchScope.inference,
        window=window,
        only_skill_unit_id=only_skill_unit_id,
    )


def run_scheduled_daily_estimates(
    scope: str = "daily",
    user_id: Optional[UUID] = None,
    *,
    job_id: Optional[UUID] = None,
) -> dict:
    """The cron entry: today's window for every declared skill of every owner."""
    return run_daily_estimate_tick(
        scope=scope,
        window=estimate_window(),
        user_id=user_id,
        job_id=job_id,
    )


def trigger_daily_estimate(
    db: Session, owner: User, skill_unit_id: UUID
) -> dict:
    """Estimate one named skill now, through the tick body, and return the row.

    Same window, same business key, same durable seat as the clock; the only
    thing a hand press changes is ``config_json["scope"]``, which says who asked.
    That is what makes the button safe to press twice: the estimate it would
    repeat has already been billed, so the second press names the first one's
    ``inference_id`` and spends nothing. It does not make the button an
    authorisation — the gate :func:`_estimate_one` walks is the same inference
    gate that refuses an unselected version or a mismatched artifact, and a
    window that is already paid for is not a licence to run a model the owner
    has not activated.

    What a press does keep that the clock does not: it is not silenced by a
    refusal the schedule recorded, because a refusal spent nothing and the
    person asking in person is the more recent instruction.

    The declared scope is checked here, before the tick opens a ledger row: a
    button must not widen a consent, and a request the platform was never asked
    to honour should not leave a record looking like a service run.
    """
    consent = get_daily_estimate_consent(db, owner.id)
    declared = {_canonical_skill_id(skill) for skill, _ in declared_scope(consent)}
    if _canonical_skill_id(skill_unit_id) not in declared:
        raise EstimateRefused("no_skills_enabled", str(skill_unit_id))

    outcome = run_daily_estimate_tick(
        scope=MANUAL_SCOPE,
        window=estimate_window(),
        user_id=owner.id,
        only_skill_unit_id=skill_unit_id,
    )
    if outcome.get("skipped"):
        raise EstimateRefused(outcome["skipped"])
    jobs = outcome.get("jobs") or []
    if not jobs:
        raise EstimateRefused("no_skills_enabled")
    return {"scope": MANUAL_SCOPE, **jobs[0]}


# ── what an owner can read back ────────────────────────────────────────


def _consent_withdrawn_for(db: Session, owner_id: Any, skill_id: str) -> bool:
    """Whether the owner's own event history says this account was withdrawn.

    ``declared`` answers 「is it in the scope today」 and cannot tell a scope that
    was never named from one the person named and then took back — the second is
    a fact about their instruction, and the durable record of it is the
    ``daily_estimate_consent_revoked`` event the DELETE entry emits. A withdrawal
    naming a different account is not evidence about this one, and a whole-scope
    withdrawal is evidence only for an account a grant had actually named.
    """
    skill = _canonical_skill_id(skill_id)
    events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "daily_estimate_consent",
            AuditEvent.entity_id == str(owner_id),
            AuditEvent.event_type.in_(
                ("daily_estimate_consent_granted", "daily_estimate_consent_revoked")
            ),
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .all()
    )
    named: Optional[str] = None
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "daily_estimate_consent_granted":
            declared_ids = {
                _canonical_skill_id(item) for item in (payload.get("skill_unit_ids") or [])
            }
            if skill in declared_ids:
                named = "declared"
        elif payload.get("skill_unit_id") is None:
            if named == "declared":
                named = "withdrawn"
        elif _canonical_skill_id(payload.get("skill_unit_id")) == skill:
            named = "withdrawn"
    return named == "withdrawn"


def _failures_for_skill(result: dict, skill_id: str) -> List[dict]:
    """The items this run's row says failed for ``skill_id``, quoting nothing.

    Two shapes are read, and they are not the same claim:

    ``failures`` is the structured entry the tick writes beside its log string,
    so the account and the instrument are fields rather than something parsed
    out of a sentence.

    ``errors`` is what rows written before that field have — a bare
    ``"{skill_id}/{instrument_id}: {exc}"``. Only the prefix is read back, and
    only when it names this skill and the remainder still has the separator: an
    entry that does not match says nothing here and is not guessed at, and the
    part after the separator is the platform's own exception text, which stays
    in the job row and the log and never reaches this response.
    """
    structured = result.get("failures")
    if isinstance(structured, list):
        return [
            entry
            for entry in structured
            if isinstance(entry, dict)
            and str(entry.get("skill_unit_id")) == skill_id
        ]
    attributed = []
    prefix = f"{skill_id}/"
    for item in result.get("errors") or []:
        if not isinstance(item, str) or not item.startswith(prefix):
            continue
        instrument, separator, _ = item[len(prefix) :].partition(": ")
        if not separator or not instrument:
            continue
        attributed.append(
            {
                "skill_unit_id": skill_id,
                "instrument_id": instrument,
                "reason": "engine_failure",
                "attributed_from": "error_string_prefix",
            }
        )
    return attributed


def daily_estimate_status(
    db: Session,
    owner: User,
    portfolio: PaperPortfolio,
    *,
    billed: Optional[Dict[str, List[dict]]] = None,
) -> dict:
    """Last estimate, pinned version, data time point, not-run reason, off state.

    Reads the ledger per skill, in two directions that answer two different
    questions. ``last_run``, ``last_refusal`` and ``last_failure`` come from the
    newest row that names this skill, whichever status it carries: a day that
    produced only refusals is exactly what the panel has to show, and a reader
    that only looked at ``completed`` rows would answer "never run" for a day
    the service refused on purpose, and a row that only ever named a failure is
    the same kind of answer. ``last_estimate`` keeps scanning past that row,
    because "when did an estimate last come out, on what version, at what data
    time point" is a fact the owner does not lose when a later day is refused —
    a stopped clock, a paused skill or a full month of stale data must not erase
    the answer, and must not make the panel say "never ran". A pause, a pulled
    stop control or a month of stale data hides nothing that was already said.

    ``billed`` lets a caller reading several skill accounts pass
    :func:`billed_estimates_by_skill` once instead of re-scanning the ledger per
    account; it changes what is read, never what is said.
    """
    consent = get_daily_estimate_consent(db, owner.id)
    scope = {skill: entry for skill, entry in declared_scope(consent)}
    skill_id = str(portfolio.id)
    declared = skill_id in scope
    declared_entry = scope.get(skill_id) or {}
    served = portfolio.status in SERVED_PORTFOLIO_STATUSES
    enabled = declared and served

    last_entry: Optional[dict] = None
    last_refusal: Optional[dict] = None
    last_failure: Optional[dict] = None
    last_run: Optional[dict] = None
    jobs = (
        db.query(LearningJob)
        .filter(
            LearningJob.user_id == owner.id,
            LearningJob.job_type == DAILY_ESTIMATE_JOB_TYPE,
        )
        .order_by(LearningJob.created_at.desc())
        .limit(50)
        .all()
    )
    for job in jobs:
        result = job.result_json or {}
        served_here = [
            entry
            for key in ("results", "recovered")
            for entry in (result.get(key) or [])
            if str(entry.get("skill_unit_id")) == skill_id
        ]
        refused_here = [
            entry
            for entry in (result.get("refused") or [])
            if str(entry.get("skill_unit_id")) == skill_id
        ]
        failed_here = _failures_for_skill(result, skill_id)
        if not served_here and not refused_here and not failed_here:
            # A row that only ever said 「failed」 still named this account and
            # still spent the tick's attempt on it. Before this the scan looked
            # past such a row, so the newest thing the clock had to say about a
            # skill was a day it had not been asked about for weeks, and a card
            # reloaded over the dead run answered 「沒有估計」 for an attempt that
            # is written in the same table.
            continue
        if last_run is None:
            # Only the newest row that names this skill supplies the not-run
            # reason: a refusal the owner has since got past is not a reason
            # anything is being withheld for today.
            last_run = {
                "job_id": str(job.id),
                "window": result.get("window"),
                "scope": (job.config_json or {}).get("scope"),
                "status": job.status,
                "completed_at": job.completed_at.isoformat()
                if job.completed_at
                else None,
            }
            if refused_here:
                last_refusal = refused_here[0]
            if failed_here:
                # Copied, and counted: a card that quotes one instrument must be
                # able to say how many this row named for this skill, or four
                # dead items read as one.
                last_failure = {**failed_here[0], "count": len(failed_here)}
        if served_here and last_entry is None:
            last_entry = served_here[0]
        if last_run is not None and last_entry is not None:
            break

    # The summary describes the window; the bill *is* the window. A tick that
    # committed its estimates and died before writing its report names nothing in
    # ``result_json``, so the scan above finds no row for it — and a card that
    # stops there tells the owner 「沒有估計」 about a day the ledger says was
    # paid for. Ask the ledger before answering, and hand back the row with its
    # verdict left absent: ``verdict_durable`` is False on a billing row precisely
    # because the answer stayed in the run that never described it. The search is
    # not limited to today, because the question this row answers is the same one
    # the summary scan asks — "what is the last estimate this skill can name" —
    # and a bill from yesterday's lost summary has not stopped existing today.
    billed_for_skill = (
        billed
        if billed is not None
        else billed_estimates_by_skill(db, owner.id)
    ).get(skill_id, [])
    if last_entry is None and billed_for_skill:
        last_entry = billed_for_skill[-1]

    # Which window this card is describing: the one the newest run row for this
    # owner opened, or today's UTC day when no run has ever opened one. It is read
    # off the lane's own claim rows rather than off the wall clock, because a tick
    # that died yesterday is still the newest thing the clock has to say, and a
    # card anchored to today would report 「沒有估計」 over it.
    #
    # ``window_claimed`` then says only that a run opened that window. A
    # ``learning_jobs`` row is proof the tick existed and nothing more: it is not
    # a heartbeat, so a window that billed nothing and reported nothing is
    # unconfirmed rather than running, dead, or timed out. Deciding any of those
    # would need a lease this lane does not write.
    window_claimed = bool(jobs)
    window = (
        (jobs[0].config_json or {}).get("window")
        if window_claimed
        else estimate_window()
    )

    # Which version the skill account uses *now*, so an estimate made before a
    # model or artifact switch cannot be quoted back as the current one. The
    # stored row keeps the version it actually ran; this field says whether the
    # two still agree.
    selection = current_selection(db, owner.id, portfolio.id)
    now_version = str(selection.version_id) if selection else None

    disabled_reason = None
    if enabled:
        pass
    elif not declared:
        # Two different facts, and only one of them is the absence of a scope:
        # an account the owner declared and then took back leaves a revoke event
        # behind, and 「目前未啟用」 would hide that instruction from the person
        # who made it.
        disabled_reason = (
            "consent_withdrawn"
            if _consent_withdrawn_for(db, owner.id, skill_id)
            else "not_declared"
        )
    elif portfolio.status in REFUSED_PORTFOLIO_STATUSES:
        disabled_reason = f"skill_{portfolio.status.value}"
    else:
        disabled_reason = "skill_status_unreadable"

    return {
        "skill_unit_id": skill_id,
        # Three separate answers, never collapsed into one "enabled" bool:
        "declared": declared,
        "instrument_ids": declared_entry.get("instrument_ids", []),
        "max_data_age_days": declared_entry.get("max_data_age_days"),
        "skill_status": portfolio.status.value if portfolio.status else None,
        "enabled": enabled,
        "disabled_reason": disabled_reason,
        "current_model_version_id": now_version,
        "last_run": last_run,
        "last_estimate": last_entry,
        "last_refusal": last_refusal,
        "last_failure": last_failure,
        # The two facts that separate 「這個視窗還沒有人跑」 from 「這個視窗跑過而
        # 沒有留下可證明的結果」. Only ``window_claimed`` + no bill + no refusal
        # makes the second reading true, and neither of them licenses a claim
        # that the run is still going.
        "window": window,
        "window_claimed": window_claimed,
        "estimate_matches_current_selection": (
            last_entry is not None
            and now_version is not None
            and str(last_entry.get("model_version_id")) == now_version
        ),
        "not_for": list(NOT_FOR_ZH),
        "disclaimer_zh": "每日估計只是模型對業主本人決策傾向的估計，"
        "不會替你按下任何按鈕，也不是市場方向預測。",
    }
