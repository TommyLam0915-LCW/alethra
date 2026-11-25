"""Card B's seed input: the decided synthetic material as one bounded, readable
plan, and a writer that does nothing until an operator tells it to.

Round 22 item 3 asks for this because the decisions, marks, sessions and the two
instruments' daily bars card B needs have long since been *settled* — 140 bars
per instrument, 41 days x 5 confirmed decisions, one session per decision — but
they existed only as helper calls inside a pytest process. An operator approving
"the seed write" could not see the rows, and nothing bounded them. So the shape
lives here as data, the caps live beside it, and the write is behind a flag.

What this tool deliberately is not:

* **not a consent.** It writes no ``personalization_consent`` row of any purpose,
  in any mode. The daily-estimate consent is the owner's own press
  (``app/api/personalization.py`` refuses a non-password-authenticated session),
  and the readiness section it prints stops naming that gap for a reason.
* **not a strategy version, a model or a selection.** Those come from the formal
  doors. If the target has no locked version owned by the named owner, this tool
  stops with a code instead of creating one — a seed that fabricates its own
  prerequisites has stopped being a seed.
* **not a natural tick.** Nothing here starts a scheduler or calls
  ``POST …/daily-estimate/run``. A manual run through that door is a fixture
  action and is never reported as cron behaviour.

Synthetic rows stay synthetic where the schema can say so:
``human_decision_events.actor_type`` is constrained to ``human|agent|unknown``
(``app/models/human_decision.py:85-88``), and ``human`` means "a
password-authenticated owner session" (``app/models/decision_label.py:64-68``).
A prep seed has no such session, so every row it writes carries ``unknown`` plus
a ``metadata_json`` marker, and the session rows name ``provider_id="synthetic"``
the way the bars do.

Run:  python scripts/card_b_seed.py --target-db PATH --owner-id UUID
      --strategy-version-id UUID [--out plan.json] [--execute]
Exit: 0 planned or written, 2 bad arguments, 3 target refused (not a file, not
      SQLite, not this file, no platform schema, no such owner/version),
      4 write stopped (over a cap, the seed is already there, or the target
      already holds the same market-data keys under other ids).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.decision_label import DecisionLabel
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.models.simulation import SimulationSession, SimulationStatus
from app.models.user import User
from app.services.daily_estimate_readiness import assess_readiness
from app.services.decision_labels import record_label

# ── the settled shape, spelled out as data ───────────────────────────────────

SEED_VERSION = "card-b-seed/1"
# Namespaced so the same input resolves to the same row ids on every read: an
# operator who runs the plan twice must see the same digest, and a retry must
# collide with the rows it would duplicate rather than adding a second copy.
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, f"alethra://{SEED_VERSION}")

MARKER: Dict[str, Any] = {
    "seed_version": SEED_VERSION,
    "nature": "synthetic_prep_seed",
    "owner_confirmed": False,
    "actor_type_meaning": (
        "'unknown': no password-authenticated owner session wrote this row. A "
        "real confirmation carries 'human' and comes from the review door."
    ),
}

# The two instruments card B declares, and the one the decisions belong to. The
# second instrument carries bars only — 205 decisions spread over it would be a
# different fixture than the one already settled.
INSTRUMENTS = ("US:AAPL", "US:MSFT")
DECISION_INSTRUMENT = "US:AAPL"
PROVIDER_ID = "synthetic"

BAR_START = date(2024, 1, 1)
BAR_COUNT = 140
DECISION_START_INDEX = 30
DECISION_STEP = 2
DECISION_DAYS = 41
DECISIONS_PER_DAY = 5
TOTAL_DECISIONS = DECISION_DAYS * DECISIONS_PER_DAY  # 205
INITIAL_VIRTUAL_CASH = 100_000.0

# The ceiling per table, in the corrected counts §16.4 records: a session per
# decision, not per day. The writer refuses anything that would exceed these,
# so "bounded" is enforced where the rows are written and not only in the plan.
CAPS = {
    "market_data_bars": BAR_COUNT * len(INSTRUMENTS),  # 280
    "market_data_metadata": len(INSTRUMENTS),  # 2
    "simulation_sessions": TOTAL_DECISIONS,  # 205
    "human_decision_events": TOTAL_DECISIONS,  # 205
    "decision_labels": TOTAL_DECISIONS,  # 205
}


class SeedRefused(Exception):
    """A named stop. ``code`` is what the operator reads, not a stack trace."""

    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


# The stops that mean "the target was acceptable and the write was refused at the
# door", as distinct from "what you pointed me at is not the target".
WRITE_STOP_REFUSALS = {
    "seed_exceeds_caps",
    "target_exceeds_caps_after_write",
    "seed_rows_read_back_short",
    "target_already_holds_seed_rows",
    "target_holds_conflicting_rows",
    "seed_label_action_label_drift",
}


def business_days(start: date, count: int) -> List[date]:
    """The same calendar the settled fixture uses: consecutive weekdays, no gaps."""
    days: List[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


BARS = business_days(BAR_START, BAR_COUNT)


def _price(index: int) -> float:
    """Deterministic, and not a price anyone can trade: a sine plus a drift plus a sawtooth."""
    return 100.0 + 0.05 * index + 6.0 * math.sin(index / 4.0) + 0.25 * (index % 7)


def _seed_id(kind: str, *parts: Any) -> str:
    return str(uuid.uuid5(SEED_NAMESPACE, "|".join([kind, *[str(p) for p in parts]])))


def bar_rows(instruments=INSTRUMENTS) -> Dict[str, List[Dict[str, Any]]]:
    """``market_data_bars`` and its one provenance row per instrument.

    The bars carry the same columns as the settled fixture, in the same order of
    dates; their provenance is the ``provider_id="synthetic"`` they share with
    the metadata row, because ``market_data_bars`` has no marker column of its
    own and inventing one here would be a schema change dressed as a seed.
    """
    rows: List[Dict[str, Any]] = []
    for instrument in instruments:
        for index, day in enumerate(BARS):
            close = _price(index)
            rows.append(
                {
                    "id": _seed_id("bar", instrument, day.isoformat()),
                    "instrument_id": instrument,
                    "provider_id": PROVIDER_ID,
                    "bar_date": day.isoformat(),
                    "bar_timestamp": datetime.combine(day, datetime.min.time()).isoformat(),
                    "open": round(close - 0.4, 6),
                    "high": round(close + 0.8, 6),
                    "low": round(close - 0.9, 6),
                    "close": round(close, 6),
                    "volume": 1_000_000 + (index % 5) * 10_000,
                }
            )
    metadata = [
        {
            "id": _seed_id("bar_meta", instrument),
            "instrument_id": instrument,
            "provider_id": PROVIDER_ID,
            "data_nature": "synthetic",
            "first_bar_date": BARS[0].isoformat(),
            "last_bar_date": BARS[-1].isoformat(),
            "total_bars": len(BARS),
            "provider_note": (
                f"{SEED_VERSION}: bounded prep seed. Synthetic series, not market "
                "history and not a collection the platform fetched."
            ),
        }
        for instrument in instruments
    ]
    return {"market_data_bars": rows, "market_data_metadata": metadata}


def decision_rows(owner_id: UUID, strategy_version_id: UUID) -> Dict[str, List[Dict[str, Any]]]:
    """The 205 confirmed decisions, each with the session it belongs to.

    One session per decision, as the settled fixture writes them — 205 rows, not
    41 (the correction §16.4 records). The label spec carries no id: the review
    service assigns that, along with the revision chain, and the plan says so
    rather than naming a row the writer would ignore.
    """
    sessions: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    labels: List[Dict[str, Any]] = []
    index = 0
    for day_number in range(DECISION_DAYS):
        bar_index = DECISION_START_INDEX + day_number * DECISION_STEP
        day = BARS[bar_index]
        for minute in range(DECISIONS_PER_DAY):
            key = f"{SEED_VERSION}:{day.isoformat()}:{minute}"
            session_id = _seed_id("session", owner_id, key)
            event_id = _seed_id("event", owner_id, key)
            action = "buy" if index % 2 == 0 else "sell"
            sessions.append(
                {
                    "id": session_id,
                    "experiment_version_id": str(strategy_version_id),
                    "owner_id": str(owner_id),
                    "initial_virtual_cash": INITIAL_VIRTUAL_CASH,
                    "started_at": datetime.combine(day, time(8, 30)).isoformat(),
                    "data_cutoff_at": day.isoformat(),
                    "status": SimulationStatus.active.value,
                    "provider_id": PROVIDER_ID,
                }
            )
            events.append(
                {
                    "id": event_id,
                    "owner_id": str(owner_id),
                    "simulation_session_id": session_id,
                    "idempotency_key": key,
                    "action": action,
                    "quantity": 10.0,
                    "rationale": "synthetic prep seed decision (not an owner's own record)",
                    "server_strategy_version_id": str(strategy_version_id),
                    "server_instrument_id": DECISION_INSTRUMENT,
                    "server_step_number": day_number + 1,
                    "server_snapshot_date": day.isoformat(),
                    "server_data_cutoff_at": day.isoformat(),
                    "execution_status": HumanDecisionExecutionStatus.record_only.value,
                    "actor_type": "unknown",
                    "created_at": datetime.combine(day, time(9, 0))
                    + timedelta(minutes=minute),
                    "metadata_json": {**MARKER, "seed_index": index},
                }
            )
            # No id here on purpose: the label is appended by ``record_label``,
            # which assigns its own id, its revision chain and its supersession
            # link. A plan that named an id the writer would ignore would be a
            # plan with a digest nobody can compare against the rows.
            labels.append(
                {
                    "decision_event_id": event_id,
                    "state": "confirmed",
                    "action_label": None,
                    "resolved_action_label": action,
                    "actor_type": "unknown",
                    "marker": {**MARKER, "seed_index": index},
                }
            )
            index += 1
    assert index == TOTAL_DECISIONS, index
    return {
        "simulation_sessions": sessions,
        "human_decision_events": events,
        "decision_labels": labels,
    }


def _canonical(plan_rows: Dict[str, List[Dict[str, Any]]]) -> str:
    """One JSON spelling, so the digest compares across runs and machines."""
    return json.dumps(
        {
            name: [
                {key: _text(value) for key, value in row.items()}
                for row in rows
            ]
            for name, rows in sorted(plan_rows.items())
        },
        sort_keys=True,
    )


def _text(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def build_plan(owner_id: UUID, strategy_version_id: UUID) -> Dict[str, Any]:
    """The whole input, in memory, with nothing written anywhere."""
    rows = {**bar_rows(), **decision_rows(owner_id, strategy_version_id)}
    counts = {name: len(items) for name, items in rows.items()}
    over = {name: counts[name] for name in CAPS if counts[name] > CAPS[name]}
    return {
        "seed_version": SEED_VERSION,
        "owner_id": str(owner_id),
        "strategy_version_id": str(strategy_version_id),
        "decision_instrument": DECISION_INSTRUMENT,
        "bar_window": {
            "instruments": list(INSTRUMENTS),
            "first_bar": BARS[0].isoformat(),
            "last_bar": BARS[-1].isoformat(),
            "bars_per_instrument": len(BARS),
        },
        "counts": counts,
        "caps": dict(CAPS),
        "within_caps": not over,
        "over_caps": over,
        "rows_sha256": hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest(),
        "written": False,
        "rows": rows,
    }


# ── the target, checked before a single row is written ───────────────────────


def target_state(session: Session, expected_database: str) -> Dict[str, Any]:
    """The bound database, described without its URL (a URL can carry a password).

    Three stops before a row is written: not SQLite, bound to something other
    than the file the operator named, and — checked by ``require_target_schema``
    below — a file that has no platform tables in it.
    """
    bind = session.get_bind()
    dialect = bind.dialect.name
    database = bind.url.database or ""
    if dialect != "sqlite":
        raise SeedRefused("target_not_sqlite", dialect=dialect)
    if not database:
        raise SeedRefused("target_has_no_database_file")
    if os.path.realpath(database) != os.path.realpath(expected_database):
        raise SeedRefused(
            "target_database_mismatch",
            expected=os.path.realpath(expected_database),
            bound=os.path.realpath(database),
        )
    return {"dialect": dialect, "database": os.path.realpath(database)}


# Every table the seed touches. A target missing one is either a database from
# another application or one that has never been migrated — in both cases the
# write would land somewhere nobody is reading, which is worse than a stop.
REQUIRED_TABLES = (
    "market_data_bars",
    "market_data_metadata",
    "simulation_sessions",
    "human_decision_events",
    "decision_labels",
    "experiment_versions",
    "users",
    # Listed so a target without the consent table is refused rather than
    # "partially seeded": this tool reads that table (the readiness report) and
    # never writes it.
    "personalization_consents",
)


def require_target_schema(session: Session) -> Dict[str, Any]:
    """The target has to be a platform database, not just a readable file."""
    from sqlalchemy import inspect

    present = set(inspect(session.get_bind()).get_table_names())
    absent = sorted(name for name in REQUIRED_TABLES if name not in present)
    if absent:
        raise SeedRefused("target_has_no_platform_schema", missing_tables=absent)
    return {"tables_present": len(REQUIRED_TABLES)}


def require_no_conflicting_rows(session: Session, plan: Dict[str, Any]) -> Dict[str, int]:
    """Stop when the target already holds the business keys the seed is about.

    The id-based check in ``write_seed`` cannot see this case: a target whose
    bars came from the regular fixtures has the same ``(instrument, provider,
    bar_timestamp)`` rows under *different* ids, so the seed would look absent and
    then die on ``uq_instrument_timestamp_provider`` halfway through a
    transaction. Naming the collision turns an IntegrityError traceback into the
    answer the operator needs — this target already has market data.

    ``market_data_metadata`` has the same shape of constraint on
    ``(instrument_id, provider_id)``, so one series can only ever have one
    provenance row.
    """
    timestamps = [
        datetime.fromisoformat(row["bar_timestamp"])
        for row in plan["rows"]["market_data_bars"]
    ]
    bars = (
        session.query(MarketDataBar)
        .filter(
            MarketDataBar.provider_id == PROVIDER_ID,
            MarketDataBar.instrument_id.in_(list(INSTRUMENTS)),
            MarketDataBar.bar_timestamp.in_(timestamps),
        )
        .count()
    )
    metadata = (
        session.query(MarketDataMetadata)
        .filter(
            MarketDataMetadata.provider_id == PROVIDER_ID,
            MarketDataMetadata.instrument_id.in_(list(INSTRUMENTS)),
        )
        .count()
    )
    if bars or metadata:
        raise SeedRefused(
            "target_holds_conflicting_rows",
            market_data_bars=bars,
            market_data_metadata=metadata,
            provider_id=PROVIDER_ID,
            instruments=list(INSTRUMENTS),
        )
    return {"conflicting_market_data_bars": bars, "conflicting_metadata_rows": metadata}


def require_owner_and_version(
    session: Session, owner_id: UUID, strategy_version_id: UUID
) -> Dict[str, Any]:
    """The owner and the locked version have to already be in this target.

    Creating either here would put a prep tool inside the path the formal doors
    guard, so the absence is a stop with a name instead.
    """
    owner = session.query(User).filter(User.id == owner_id).one_or_none()
    if owner is None:
        raise SeedRefused("owner_not_in_target", owner_id=str(owner_id))
    version = (
        session.query(ExperimentVersion)
        .filter(ExperimentVersion.id == strategy_version_id)
        .one_or_none()
    )
    if version is None:
        raise SeedRefused(
            "strategy_version_not_in_target",
            strategy_version_id=str(strategy_version_id),
        )
    if version.locked_at is None:
        raise SeedRefused(
            "strategy_version_not_locked", strategy_version_id=str(strategy_version_id)
        )
    return {
        "owner_id": str(owner.id),
        "owner_email_domain": (owner.email or "").rsplit("@", 1)[-1] or None,
        "strategy_version_id": str(version.id),
        "version_number": version.version_number,
        "locked_at": version.locked_at.isoformat(),
    }


# ── the writer ───────────────────────────────────────────────────────────────


def _add(session: Session, plan: Dict[str, Any]) -> None:
    rows = plan["rows"]
    for row in rows["market_data_bars"]:
        session.add(
            MarketDataBar(
                **{
                    **row,
                    "id": UUID(row["id"]),
                    "bar_date": date.fromisoformat(row["bar_date"]),
                    "bar_timestamp": datetime.fromisoformat(row["bar_timestamp"]),
                }
            )
        )
    for row in rows["market_data_metadata"]:
        session.add(
            MarketDataMetadata(
                **{
                    "id": UUID(row["id"]),
                    "instrument_id": row["instrument_id"],
                    "provider_id": row["provider_id"],
                    "data_nature": row["data_nature"],
                    "first_bar_date": date.fromisoformat(row["first_bar_date"]),
                    "last_bar_date": date.fromisoformat(row["last_bar_date"]),
                    "total_bars": row["total_bars"],
                    # The one column on this table that can say where the series
                    # came from, so the note is written rather than implied.
                    "provider_note": row["provider_note"],
                }
            )
        )
    for row in rows["simulation_sessions"]:
        session.add(
            SimulationSession(
                id=UUID(row["id"]),
                experiment_version_id=UUID(row["experiment_version_id"]),
                owner_id=UUID(row["owner_id"]),
                initial_virtual_cash=row["initial_virtual_cash"],
                started_at=datetime.fromisoformat(row["started_at"]),
                data_cutoff_at=row["data_cutoff_at"],
                status=SimulationStatus(row["status"]),
                provider_id=row["provider_id"],
            )
        )
    for row in rows["human_decision_events"]:
        session.add(
            HumanDecisionEvent(
                id=UUID(row["id"]),
                owner_id=UUID(row["owner_id"]),
                simulation_session_id=UUID(row["simulation_session_id"]),
                idempotency_key=row["idempotency_key"],
                action=HumanDecisionAction(row["action"]),
                quantity=row["quantity"],
                rationale=row["rationale"],
                server_strategy_version_id=UUID(row["server_strategy_version_id"]),
                server_instrument_id=row["server_instrument_id"],
                server_step_number=row["server_step_number"],
                server_snapshot_date=row["server_snapshot_date"],
                server_data_cutoff_at=row["server_data_cutoff_at"],
                execution_status=HumanDecisionExecutionStatus(row["execution_status"]),
                actor_type=row["actor_type"],
                created_at=row["created_at"],
                metadata_json=row["metadata_json"],
            )
        )


def _seed_counts(session: Session, plan: Dict[str, Any]) -> Dict[str, int]:
    """How many of *this seed's* rows are in the target, per table.

    Scoped by the planned ids (and, for the labels, by the events they revise)
    rather than by a whole-table ``count()``: the isolated database a review run
    uses already holds other demo rows, and a ceiling measured across all of them
    would stop on a number this seed never wrote. The full-table counts are
    reported beside these so nothing about the target's freshness is hidden.
    """
    event_ids = [UUID(row["id"]) for row in plan["rows"]["human_decision_events"]]
    found = {
        "market_data_bars": session.query(MarketDataBar)
        .filter(MarketDataBar.id.in_([UUID(r["id"]) for r in plan["rows"]["market_data_bars"]]))
        .count(),
        "market_data_metadata": session.query(MarketDataMetadata)
        .filter(
            MarketDataMetadata.id.in_(
                [UUID(r["id"]) for r in plan["rows"]["market_data_metadata"]]
            )
        )
        .count(),
        "simulation_sessions": session.query(SimulationSession)
        .filter(
            SimulationSession.id.in_(
                [UUID(r["id"]) for r in plan["rows"]["simulation_sessions"]]
            )
        )
        .count(),
        "human_decision_events": session.query(HumanDecisionEvent)
        .filter(HumanDecisionEvent.id.in_(event_ids))
        .count(),
        "decision_labels": session.query(DecisionLabel)
        .filter(DecisionLabel.decision_event_id.in_(event_ids))
        .count(),
    }
    return {name: int(count) for name, count in found.items()}


def _table_counts(session: Session) -> Dict[str, int]:
    """Everything already in the target, seed rows or not — observation, not a stop."""
    return {
        "market_data_bars": session.query(MarketDataBar).count(),
        "market_data_metadata": session.query(MarketDataMetadata).count(),
        "simulation_sessions": session.query(SimulationSession).count(),
        "human_decision_events": session.query(HumanDecisionEvent).count(),
        "decision_labels": session.query(DecisionLabel).count(),
    }


def write_seed(
    session: Session, plan: Dict[str, Any], *, execute: bool = False
) -> Dict[str, Any]:
    """Inert unless ``execute`` is passed. Nothing here writes a consent row.

    The caps are re-checked at the door rather than trusted from ``build_plan``,
    and a target that already holds planned rows stops instead of adding a
    second copy of the same intent.
    """
    receipt = {
        "seed_version": plan["seed_version"],
        "rows_sha256": plan["rows_sha256"],
        "planned_counts": plan["counts"],
        "caps": dict(CAPS),
        "executed": bool(execute),
    }
    if not plan["within_caps"]:
        raise SeedRefused("seed_exceeds_caps", over=plan["over_caps"])
    if not execute:
        return {**receipt, "reason": "inert_by_default", "written": 0}
    already = _seed_counts(session, plan)
    present = {name: count for name, count in already.items() if count}
    if present:
        # A stop rather than a receipt with ``written: 0``: an operator who ran
        # ``--execute`` twice has to get a non-zero answer, because "the rows are
        # already there" and "the write happened" must not share an exit code.
        raise SeedRefused("target_already_holds_seed_rows", existing=present)
    require_no_conflicting_rows(session, plan)

    _add(session, plan)
    session.flush()
    # The label is appended through the review service rather than built here:
    # its revision chain, its ownership check and its action-label rules are the
    # contract the training door reads, and a seed that bypassed them would be
    # evidence of nothing. The marker is merged into the metadata the service
    # wrote, so the service's own record of what it resolved stays intact and
    # provenance travels with the row rather than with a comment.
    author = session.query(User).filter(User.id == UUID(plan["owner_id"])).one()
    try:
        for spec in plan["rows"]["decision_labels"]:
            event = session.get(HumanDecisionEvent, UUID(spec["decision_event_id"]))
            label = record_label(
                session,
                event=event,
                author=author,
                state=spec["state"],
                action_label=spec["action_label"],
                note=None,
                exclusion_reason=None,
                actor_type=spec["actor_type"],
            )
            label.metadata_json = {**(label.metadata_json or {}), **spec["marker"]}
            # The review service decides the action label from the recorded
            # decision, so the plan can only describe it in advance. If it ever
            # resolves differently, the digest the operator approved does not
            # match the rows, which is a stop and not a note in the receipt.
            if label.action_label != spec["resolved_action_label"]:
                raise SeedRefused(
                    "seed_label_action_label_drift",
                    decision_event_id=spec["decision_event_id"],
                    planned=spec["resolved_action_label"],
                    recorded=label.action_label,
                )
    except SeedRefused:
        session.rollback()
        raise
    session.flush()

    seed_rows = _seed_counts(session, plan)
    over = {name: seed_rows[name] for name in CAPS if seed_rows[name] > CAPS[name]}
    short = {
        name: {"planned": plan["counts"][name], "read_back": seed_rows[name]}
        for name in CAPS
        if seed_rows[name] != plan["counts"][name]
    }
    if over or short:
        # The ceiling and the read-back are stops, not notes in the receipt: a
        # write that cannot name its own rows is not a write an operator can
        # approve, and a target holding more than the approved cap was never a
        # target for this seed.
        session.rollback()
        raise SeedRefused(
            "target_exceeds_caps_after_write" if over else "seed_rows_read_back_short",
            over=over,
            short=short,
        )
    session.commit()
    return {
        **receipt,
        "reason": "written",
        "written": sum(plan["counts"].values()),
        "seed_rows_read_back": seed_rows,
        "target_table_counts": _table_counts(session),
        "consent_rows_written": 0,
    }


# ── readiness: the two keys this window still owes, and the units to pay them ─


def readiness_view(
    session: Session, owner_id: UUID, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """The report reduced to the two things card B is gated on.

    ``assess_readiness`` is the lane's own door — it reads the same
    ``features_as_of`` cut, the same ``settled_estimates`` bills and the same
    entitlement contract the tick will. What this adds is the per-key answer the
    brief asks for: which ``(skill unit, instrument, window)`` keys are still
    unpaid, and whether the units that remain — not the nominal limit — cover
    them.
    """
    report = assess_readiness(session, owner_id=owner_id, now=now)
    unsettled: List[Dict[str, Any]] = []
    billed: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    for skill in report["skills"]:
        for instrument in skill["instruments"]:
            key = {
                "skill_unit_id": skill["skill_unit_id"],
                "instrument_id": instrument["instrument_id"],
                "window": report["window"],
            }
            if instrument["refusal"] is not None:
                refused.append({**key, "refusal": instrument["refusal"]})
            elif instrument["already_billed"]:
                billed.append(key)
            else:
                unsettled.append(key)
    quota = report["quota"]
    return {
        "owner_id": str(owner_id),
        "window": report["window"],
        "unsettled_business_keys": unsettled,
        "billed_business_keys": billed,
        "refused_business_keys": refused,
        "quota": {
            "limit": quota["limit"],
            "used": quota["used"],
            "reserved": quota["reserved"],
            "remaining": quota["remaining"],
            "units_declared": quota["units_declared"],
            "units_ready_to_spend": quota["units_ready_to_spend"],
            # Spend is measured against what is left, so a nominal ``limit`` of
            # 10 with 9 already used does not clear a two-key window.
            "covers_unsettled_keys": bool(quota["sufficient"]),
            "nominal_limit_at_least_key_count": (
                quota["limit"] is None or quota["limit"] >= len(unsettled)
            ),
        },
        "missing": report["missing"],
        "billing_observation_allowed": report["billing_observation_allowed"],
        "verdict": report["verdict"],
        "owner_consent_pressed": report["consent"]["present"],
        # Stated where it can be read next to the number: this says nothing about
        # a natural tick, and pressing the manual run door is not one either.
        "natural_tick_observed": False,
    }


# ── operator entry ───────────────────────────────────────────────────────────


def _report(payload: Dict[str, Any], out: Optional[str]) -> None:
    shown = {k: v for k, v in payload.items() if k != "rows"}
    if "rows" in payload:
        shown["rows_included_in_file"] = out is not None
    text = json.dumps(shown, indent=2, sort_keys=True, default=str)
    print(text)
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target-db", required=True, help="the isolated SQLite file")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--strategy-version-id", required=True)
    parser.add_argument("--out", help="write the full plan (rows included) here")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually write the seed; without this flag nothing is written",
    )
    parser.add_argument("--readiness", action="store_true", help="include the report")
    args = parser.parse_args(argv)

    try:
        owner_id = UUID(args.owner_id)
        strategy_version_id = UUID(args.strategy_version_id)
    except ValueError:
        print(json.dumps({"refusal": "id_not_a_uuid"}), file=sys.stderr)
        return 2

    # SQLite creates a file on connect, so a typo in --target-db would otherwise
    # produce a brand new empty database and a "successful" write into nowhere.
    if not os.path.exists(args.target_db):
        print(
            json.dumps(
                {"refusal": "target_database_missing", "written": 0}, sort_keys=True
            ),
            file=sys.stderr,
        )
        return 3

    engine = create_engine(f"sqlite:///{os.path.realpath(args.target_db)}")
    session = Session(bind=engine)
    try:
        target = target_state(session, args.target_db)
        target.update(require_target_schema(session))
        subject = require_owner_and_version(session, owner_id, strategy_version_id)
        plan = build_plan(owner_id, strategy_version_id)
        receipt = write_seed(session, plan, execute=args.execute)
        payload = {
            "target": target,
            "subject": subject,
            "plan": {k: v for k, v in plan.items() if k != "rows"},
            "write": receipt,
        }
        if args.readiness:
            payload["readiness"] = readiness_view(session, owner_id)
        if args.out:
            _report({**payload, "rows": plan["rows"]}, args.out)
        else:
            _report(payload, None)
        return 0
    except SeedRefused as refused:
        print(
            json.dumps(
                {"refusal": refused.code, **refused.detail, "written": 0},
                sort_keys=True,
                default=str,
            ),
            file=sys.stderr,
        )
        # 4 only for a write that got as far as the door and was stopped there;
        # anything earlier means the target itself was never acceptable.
        return 4 if refused.code in WRITE_STOP_REFUSALS else 3
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
