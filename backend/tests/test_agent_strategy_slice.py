"""Agent candidate screening — the isolated data and contract vertical slice.

What this slice does, and what these tests pin down:

  * entry signals of an existing locked SMA rule become candidates, including
    signals that could not be executed; a non-take is never stored as a loss
  * features are read as of the next-day availability instant, so a later bar
    can never leak into an earlier decision
  * an actual paper round trip is linked as ``observed``; everything else is a
    deterministic ``counterfactual`` replay and says so; incomplete windows and
    missing prices are ``immature``/``unavailable`` with no fabricated 0%
  * a dataset snapshot without the research consent is recorded as refused and
    is not a training set
  * the split is chronological and grouped by decision date, with a gap and a
    label-window purge; nothing is shuffled
  * existing paper orders are only classified by a read-only report; no fill,
    commission, cash or provenance value is rewritten

Nothing here trains a model, activates one, runs inference, accepts consent,
or creates a customer trade.
"""
import uuid
from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import TestSession
from app.models.agent_strategy import (
    AgentCandidate,
    AgentCandidateOutcome,
    AgentDatasetSnapshot,
)
from app.models.decision_label import DecisionLabel
from app.models.human_decision import HumanDecisionEvent
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
)
from app.models.user import User
from app.services.agent_strategy import (
    COMMISSION_RATE,
    EXECUTION_POLICY_VERSION,
    FEATURE_EXCLUSIONS,
    HOLD_BARS,
    INITIAL_NOTIONAL,
    SLIPPAGE_RATE,
    AgentStrategyRefused,
    _canonical_checksum,
    build_outcomes,
    build_split_manifest,
    candidate_outcome_history,
    collect_candidates,
    create_snapshot,
    evaluate_eligibility,
    existing_trade_eligibility,
    list_candidates,
    preview_snapshot,
    training_contract,
    validate_lineage,
)
from app.services.personal_dataset import daily_bar_available_at, features_as_of

INSTRUMENT = "DEMO-ALPHA"
EXPENSIVE = "DEMO-BETA"
START = date(2024, 1, 1)
PROVIDER = "synthetic"


def _owner_id() -> uuid.UUID:
    db = TestSession()
    try:
        return db.query(User).filter(User.email == "test@alethra.io").one().id
    finally:
        db.close()


def _seed_bars(instrument: str, closes, start: date = START) -> list:
    db = TestSession()
    days = []
    try:
        for i, close in enumerate(closes):
            day = start + timedelta(days=i)
            days.append(day)
            db.add(
                MarketDataBar(
                    instrument_id=instrument,
                    provider_id=PROVIDER,
                    bar_date=day,
                    bar_timestamp=datetime.combine(day, time.min),
                    open=float(close),
                    high=float(close) + 1.0,
                    low=float(close) - 1.0,
                    close=float(close),
                    volume=1_000_000,
                )
            )
        db.commit()
    finally:
        db.close()
    return days


def _zigzag(levels=7, count=40, base=50.0):
    return [base + float(i % levels) for i in range(count)]


def _create_locked_strategy(client, headers, instrument=INSTRUMENT, title="Screening"):
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "sma_crossover",
                "asset_ids": [instrument],
                "start_date": START.isoformat(),
                "end_date": (START + timedelta(days=80)).isoformat(),
                "initial_cash": 100000,
                "signal_parameters": {"short_window": 2, "long_window": 3},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    lock = client.post(
        f"/api/v1/experiments/{body['id']}/versions/{body['current_version_id']}/lock",
        headers=headers,
        json={"confirm": True, "reason": "agent screening test"},
    )
    assert lock.status_code == 200, lock.text
    return uuid.UUID(body["current_version_id"])


def _candidates(version_id):
    db = TestSession()
    try:
        return (
            db.query(AgentCandidate)
            .filter(AgentCandidate.strategy_version_id == version_id)
            .order_by(AgentCandidate.signal_date, AgentCandidate.id)
            .all()
        )
    finally:
        db.close()


def _outcomes(version_id):
    db = TestSession()
    try:
        return (
            db.query(AgentCandidateOutcome)
            .join(
                AgentCandidate,
                AgentCandidateOutcome.candidate_id == AgentCandidate.id,
            )
            .filter(AgentCandidate.strategy_version_id == version_id)
            .all()
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Candidate collection and feature availability
# ---------------------------------------------------------------------------


class TestCandidateCollection:

    def test_collect_is_idempotent_and_records_the_availability_instant(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)

        first = collect_candidates(TestSession(), _owner_id(), version_id)
        assert first["created"] > 0, first
        assert first["stored_bars"] == 40

        second = collect_candidates(TestSession(), _owner_id(), version_id)
        assert second["created"] == 0
        assert second["existing"] == first["created"]

        rows = _candidates(version_id)
        assert len(rows) == first["created"]
        for row in rows:
            signal_day = date.fromisoformat(row.signal_date)
            # A daily bar is readable at the next calendar day 00:00 UTC, and
            # the decision instant records exactly that, not the signal date.
            assert row.decision_instant == daily_bar_available_at(signal_day)
            assert row.decision_instant_source == "daily_bar_available_at"
            assert row.data_cutoff_at == row.signal_date
            # The feature row is the signal-day bar at the latest; later bars
            # are stored but must not be visible at this decision instant.
            # Early candidates without enough history keep no feature row and
            # carry an explicit exclusion instead of a fabricated value.
            if row.rejection_reason in FEATURE_EXCLUSIONS:
                assert row.feature_bar_date is None
            else:
                assert row.feature_bar_date is not None
                assert row.feature_bar_date <= row.signal_date
            assert row.execution_policy_version == EXECUTION_POLICY_VERSION

    def test_a_later_bar_is_not_visible_at_an_earlier_decision_instant(self):
        closes = _zigzag()
        days = _seed_bars(INSTRUMENT, closes)
        signal_day = days[23]

        after_close = features_as_of(
            TestSession(),
            INSTRUMENT,
            signal_day.isoformat(),
            as_of_instant=daily_bar_available_at(signal_day),
        )
        before_close = features_as_of(
            TestSession(),
            INSTRUMENT,
            signal_day.isoformat(),
            as_of_instant=datetime.combine(signal_day, time.min),
        )
        assert after_close[3] is None
        assert after_close[2] == signal_day.isoformat()
        assert before_close[2] == (signal_day - timedelta(days=1)).isoformat()

    def test_insufficient_notional_is_a_constraint_not_a_loss(
        self, client, auth_headers
    ):
        # 100,000 / (200,000 * 1.001) buys zero shares.
        _seed_bars(EXPENSIVE, _zigzag(base=200_000.0))
        db = TestSession()
        try:
            owner_id = _owner_id()
            experiment = _create_experiment_row(db, owner_id, EXPENSIVE)
            result = collect_candidates(db, owner_id, experiment)
            candidates = (
                db.query(AgentCandidate)
                .filter(AgentCandidate.strategy_version_id == experiment)
                .all()
            )
        finally:
            db.close()

        assert result["created"] > 0
        assert result["execution_feasibility"].get("insufficient_cash") == result["created"]
        for candidate in candidates:
            assert candidate.execution_feasibility == "insufficient_cash"
            assert candidate.rejection_reason == "insufficient_cash"
        assert INITIAL_NOTIONAL / (200_000.0 * (1 + COMMISSION_RATE)) < 1


def _create_experiment_row(db, owner_id, instrument):
    """A locked SMA version created directly, for service-level tests."""
    from app.models.experiment import Experiment, ExperimentStatus, ResearchType
    from app.models.experiment_version import ExperimentVersion

    experiment = Experiment(
        owner_id=owner_id,
        title="Direct screening fixture",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
    )
    db.add(experiment)
    db.flush()
    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        created_by=owner_id,
        strategy_spec={
            "template_type": "sma_crossover",
            "asset_ids": [instrument],
            "start_date": START.isoformat(),
            "end_date": (START + timedelta(days=80)).isoformat(),
            "initial_cash": 100000.0,
            "signal_parameters": {"short_window": 2, "long_window": 3},
        },
        locked_at=datetime.combine(START + timedelta(days=1), time.min),
    )
    db.add(version)
    db.flush()
    experiment.current_version_id = version.id
    db.commit()
    return version.id


# ---------------------------------------------------------------------------
# Observed vs counterfactual vs immature vs unavailable
# ---------------------------------------------------------------------------


class TestOutcomes:

    def test_counterfactual_replay_uses_the_declared_cost_model(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        collect_candidates(TestSession(), _owner_id(), version_id)
        result = build_outcomes(TestSession(), _owner_id(), version_id)
        assert result["created"] > 0

        db = TestSession()
        try:
            row = (
                db.query(AgentCandidateOutcome, AgentCandidate)
                .join(
                    AgentCandidate,
                    AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                )
                .filter(
                    AgentCandidate.strategy_version_id == version_id,
                    AgentCandidateOutcome.outcome_status == "counterfactual",
                    # A candidate whose features were missing still has a price
                    # replay but is not a usable label; select an eligible one.
                    AgentCandidateOutcome.label_eligible.is_(True),
                )
                .order_by(AgentCandidate.signal_date)
                .first()
            )
            assert row is not None
            outcome, candidate = row
            bars = (
                db.query(MarketDataBar)
                .filter(MarketDataBar.instrument_id == INSTRUMENT)
                .order_by(MarketDataBar.bar_date)
                .all()
            )
            index = {bar.bar_date.isoformat(): i for i, bar in enumerate(bars)}
            entry = bars[index[candidate.signal_date] + 1]
            exit_bar = bars[index[candidate.signal_date] + 1 + HOLD_BARS]
            entry_price = entry.open * (1 + SLIPPAGE_RATE)
            exit_price = exit_bar.close * (1 - SLIPPAGE_RATE)
            net = (
                (exit_price * (1 - COMMISSION_RATE))
                / (entry_price * (1 + COMMISSION_RATE))
                - 1
            ) * 100.0
        finally:
            db.close()

        assert outcome.counterfactual is True
        assert outcome.outcome_source == "counterfactual_replay"
        assert outcome.label_maturity == "mature"
        assert outcome.label_eligible is True
        assert outcome.entry_price == pytest.approx(entry_price)
        assert outcome.exit_price == pytest.approx(exit_price)
        assert outcome.net_return_pct == pytest.approx(net)
        assert outcome.cost_model_json["counterfactual"] is True
        assert outcome.cost_model_json["version"] == EXECUTION_POLICY_VERSION
        assert outcome.notes_json["matching"] == "counterfactual_replay"

    def test_an_actual_round_trip_is_observed_and_overrides_the_replay(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        build_outcomes(TestSession(), owner_id, version_id)

        db = TestSession()
        try:
            outcome, candidate = (
                db.query(AgentCandidateOutcome, AgentCandidate)
                .join(
                    AgentCandidate,
                    AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                )
                .filter(
                    AgentCandidate.strategy_version_id == version_id,
                    AgentCandidateOutcome.outcome_status == "counterfactual",
                    AgentCandidateOutcome.label_eligible.is_(True),
                )
                .order_by(AgentCandidate.signal_date)
                .first()
            )
            assert outcome is not None
            candidate_id = candidate.id
            counterfactual_id = outcome.id
            counterfactual_snapshot = {
                "entry_price": outcome.entry_price,
                "exit_price": outcome.exit_price,
                "net_return_pct": outcome.net_return_pct,
                "outcome_source": outcome.outcome_source,
                "version_seq": outcome.version_seq,
            }
            entry_date = outcome.entry_execution_date
            exit_date = outcome.exit_execution_date
            portfolio = PaperPortfolio(
                owner_id=owner_id,
                currency="USD",
                initial_virtual_cash=100000.0,
                current_virtual_cash=90000.0,
                status=PortfolioStatus.active,
            )
            db.add(portfolio)
            db.flush()

            buy, buy_fill = _filled_round_trip_leg(
                db, portfolio, OrderSide.BUY, entry_date, 100.0, -1000.0, version_id
            )
            sell, _ = _filled_round_trip_leg(
                db, portfolio, OrderSide.SELL, exit_date, 110.0, 1100.0, None
            )
            db.commit()
            buy_id = buy.id
        finally:
            db.close()

        rebuilt = build_outcomes(TestSession(), owner_id, version_id)
        assert rebuilt["preserved_observed"] == 0

        db = TestSession()
        try:
            versions = (
                db.query(AgentCandidateOutcome)
                .filter(AgentCandidateOutcome.candidate_id == candidate_id)
                .order_by(AgentCandidateOutcome.version_seq)
                .all()
            )
            observed = next(row for row in versions if row.is_current)
            previous = next(row for row in versions if row.id == counterfactual_id)
        finally:
            db.close()

        # The new selected result is the actual round trip, but the earlier
        # counterfactual is still present, untouched, with its own version.
        assert len(versions) == 2
        assert observed.version_seq == 2
        assert observed.selection_reason == "observed_round_trip"
        assert observed.outcome_status == "observed"
        assert observed.outcome_source == "paper_fill"
        assert observed.counterfactual is False
        assert observed.observed_order_id == buy_id
        assert observed.net_return_pct == pytest.approx(10.0)
        assert observed.gross_return_pct == pytest.approx(10.0)
        assert observed.notes_json["matching"] == "actual_paper_round_trip"
        assert previous.is_current is False
        assert previous.superseded_at is not None
        assert previous.version_seq == counterfactual_snapshot["version_seq"] == 1
        assert previous.entry_price == counterfactual_snapshot["entry_price"]
        assert previous.exit_price == counterfactual_snapshot["exit_price"]
        assert previous.net_return_pct == counterfactual_snapshot["net_return_pct"]
        assert previous.outcome_source == counterfactual_snapshot["outcome_source"]
        assert previous.selection_reason == "counterfactual_replay"

        history = candidate_outcome_history(
            TestSession(), owner_id, candidate_id
        )
        assert history["version_count"] == 2
        assert history["current_outcome_id"] == str(observed.id)
        assert [row["version_seq"] for row in history["versions"]] == [1, 2]

    def test_rebuilding_outcomes_is_idempotent_and_keeps_one_current_version(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        first = build_outcomes(TestSession(), owner_id, version_id)
        assert first["created"] > 0

        second = build_outcomes(TestSession(), owner_id, version_id)

        assert second["created"] == 0
        assert second["superseded"] == 0
        assert second["preserved"] == first["created"]
        assert second["history_versions"] == first["created"]

        db = TestSession()
        try:
            rows = (
                db.query(AgentCandidateOutcome)
                .join(
                    AgentCandidate,
                    AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                )
                .filter(AgentCandidate.strategy_version_id == version_id)
                .all()
            )
        finally:
            db.close()
        current_per_candidate = {}
        for row in rows:
            if row.is_current:
                key = str(row.candidate_id)
                current_per_candidate[key] = current_per_candidate.get(key, 0) + 1
        # Every candidate has exactly one selected version, never zero or two.
        assert set(current_per_candidate.values()) == {1}
        assert len(current_per_candidate) == len(
            {str(row.candidate_id) for row in rows}
        )

    def test_an_exit_policy_mismatch_is_evidence_not_a_replacement(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        build_outcomes(TestSession(), owner_id, version_id)

        db = TestSession()
        try:
            outcome, candidate = (
                db.query(AgentCandidateOutcome, AgentCandidate)
                .join(
                    AgentCandidate,
                    AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                )
                .filter(
                    AgentCandidate.strategy_version_id == version_id,
                    AgentCandidateOutcome.outcome_status == "counterfactual",
                    AgentCandidateOutcome.label_eligible.is_(True),
                )
                .order_by(AgentCandidate.signal_date)
                .first()
            )
            assert outcome is not None
            candidate_id = candidate.id
            entry_date = outcome.entry_execution_date
            target_exit_date = outcome.exit_execution_date
            target_entry_price = outcome.entry_price
            target_exit_price = outcome.exit_price
            target_net = outcome.net_return_pct
            # The owner exits earlier than the declared fixed hold.
            actual_exit_date = (
                date.fromisoformat(entry_date) + timedelta(days=2)
            ).isoformat()
            assert actual_exit_date != target_exit_date
            portfolio = PaperPortfolio(
                owner_id=owner_id,
                currency="USD",
                initial_virtual_cash=100000.0,
                current_virtual_cash=90000.0,
                status=PortfolioStatus.active,
            )
            db.add(portfolio)
            db.flush()
            _filled_round_trip_leg(
                db, portfolio, OrderSide.BUY, entry_date, 100.0, -1000.0, version_id
            )
            _filled_round_trip_leg(
                db, portfolio, OrderSide.SELL, actual_exit_date, 105.0, 1050.0, None
            )
            db.commit()
        finally:
            db.close()

        build_outcomes(TestSession(), owner_id, version_id)

        db = TestSession()
        try:
            versions = (
                db.query(AgentCandidateOutcome)
                .filter(AgentCandidateOutcome.candidate_id == candidate_id)
                .order_by(AgentCandidateOutcome.version_seq)
                .all()
            )
            current = next(row for row in versions if row.is_current)
            mismatch = next(
                row
                for row in versions
                if row.selection_reason == "exit_policy_mismatch"
            )
        finally:
            db.close()

        # Exactly the frozen target plus the retained evidence. The replay's
        # descriptive note changing when an observed entry appears must not
        # mint a duplicate copy of the same result.
        assert len(versions) == 2
        # The declared fixed-hold target stays selected and unchanged.
        assert current.outcome_status == "counterfactual"
        assert current.outcome_source == "counterfactual_replay"
        assert current.counterfactual is True
        assert current.exit_execution_date == target_exit_date
        assert current.entry_price == target_entry_price
        assert current.exit_price == target_exit_price
        assert current.net_return_pct == target_net
        # The actual round trip is retained as unselected evidence.
        assert mismatch.is_current is False
        assert mismatch.outcome_status == "observed"
        assert mismatch.outcome_source == "paper_fill"
        assert mismatch.counterfactual is False
        assert mismatch.label_eligible is False
        assert mismatch.exclusion_reason == "exit_policy_mismatch"
        assert mismatch.exit_execution_date == actual_exit_date
        assert mismatch.net_return_pct == pytest.approx(5.0)
        assert (
            mismatch.notes_json["matching"]
            == "actual_paper_round_trip_exit_policy_mismatch"
        )
        assert mismatch.notes_json["target_exit_execution_date"] == target_exit_date


def _filled_round_trip_leg(db, portfolio, side, execution_date, price, net, version):
    order = PaperOrder(
        portfolio_id=portfolio.id,
        asset_id=INSTRUMENT,
        side=side,
        quantity=10.0,
        status=OrderStatus.filled,
        actor_type="human",
        model_or_strategy_version_id=version,
        data_cutoff_at=execution_date,
    )
    db.add(order)
    db.flush()
    fill = PaperFill(
        paper_order_id=order.id,
        execution_date=execution_date,
        observed_price=price,
        execution_price_before_costs=price,
        slippage_amount=0.0,
        commission_amount=0.0,
        quantity=10.0,
        gross_amount=price * 10.0,
        net_cash_change=net,
    )
    db.add(fill)
    db.flush()
    return order, fill


class TestOutcomeEvidenceImmutability:
    """A frozen record keeps referring to the exact result it consumed."""

    def test_refused_snapshot_is_unchanged_when_an_observed_result_arrives(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag(count=80))
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        build_outcomes(TestSession(), owner_id, version_id)

        preview_before = preview_snapshot(TestSession(), owner_id, version_id)
        assert preview_before["rows"], "the fixture must produce label rows"
        # The frozen record recomputes to the checksum it published.
        assert (
            _canonical_checksum(
                preview_before["rows"], preview_before["label_definition"]
            )
            == preview_before["checksum"]
        )
        for row in preview_before["rows"]:
            assert row["outcome_id"]
            assert row["outcome_version"] >= 1
            assert row["outcome_selection_reason"]

        with pytest.raises(AgentStrategyRefused) as first_exc:
            create_snapshot(TestSession(), owner_id, version_id)
        first_snapshot_id = first_exc.value.details["snapshot_id"]

        target_row = next(
            row
            for row in preview_before["rows"]
            if row["outcome_status"] == "counterfactual"
        )
        entry_date = target_row["entry_execution_date"]
        exit_date = target_row["exit_execution_date"]

        db = TestSession()
        try:
            portfolio = PaperPortfolio(
                owner_id=owner_id,
                currency="USD",
                initial_virtual_cash=100000.0,
                current_virtual_cash=90000.0,
                status=PortfolioStatus.active,
            )
            db.add(portfolio)
            db.flush()
            _filled_round_trip_leg(
                db, portfolio, OrderSide.BUY, entry_date, 100.0, -1000.0, version_id
            )
            _filled_round_trip_leg(
                db, portfolio, OrderSide.SELL, exit_date, 110.0, 1100.0, None
            )
            db.commit()
        finally:
            db.close()

        build_outcomes(TestSession(), owner_id, version_id)

        db = TestSession()
        try:
            frozen = db.get(AgentDatasetSnapshot, uuid.UUID(first_snapshot_id))
            frozen_state = (
                frozen.checksum,
                frozen.rows_json,
                frozen.candidate_ids_json,
                frozen.split_manifest_json,
                frozen.exclusion_breakdown_json,
                frozen.outcome_distribution_json,
                frozen.status,
                frozen.included_count,
            )
            # Every referenced version is still stored with the same content.
            retained = {}
            for row in preview_before["rows"]:
                record = db.get(
                    AgentCandidateOutcome, uuid.UUID(row["outcome_id"])
                )
                assert record is not None
                assert record.version_seq == row["outcome_version"]
                assert record.selection_reason == row["outcome_selection_reason"]
                assert record.outcome_status == row["outcome_status"]
                assert record.outcome_source == row["outcome_source"]
                assert record.net_return_pct == row["net_return_pct"]
                assert record.exit_execution_date == row["exit_execution_date"]
                retained[row["candidate_id"]] = (record.is_current, record.id)
            observed_current = retained[target_row["candidate_id"]]
        finally:
            db.close()

        assert observed_current[0] is False

        # A new snapshot captures the new state without touching the old one.
        with pytest.raises(AgentStrategyRefused) as second_exc:
            create_snapshot(TestSession(), owner_id, version_id)
        second_snapshot_id = second_exc.value.details["snapshot_id"]
        assert second_snapshot_id != first_snapshot_id

        db = TestSession()
        try:
            frozen = db.get(AgentDatasetSnapshot, uuid.UUID(first_snapshot_id))
            after = (
                frozen.checksum,
                frozen.rows_json,
                frozen.candidate_ids_json,
                frozen.split_manifest_json,
                frozen.exclusion_breakdown_json,
                frozen.outcome_distribution_json,
                frozen.status,
                frozen.included_count,
            )
            latest = db.get(AgentDatasetSnapshot, uuid.UUID(second_snapshot_id))
        finally:
            db.close()

        assert after == frozen_state
        assert latest.checksum != frozen.checksum

        preview_after = preview_snapshot(TestSession(), owner_id, version_id)
        refreshed = next(
            row
            for row in preview_after["rows"]
            if row["candidate_id"] == target_row["candidate_id"]
        )
        assert refreshed["outcome_version"] == target_row["outcome_version"] + 1
        assert refreshed["outcome_source"] == "paper_fill"
        assert refreshed["outcome_status"] == "observed"


class TestOutcomeBuckets:

    def test_immature_and_unavailable_rows_never_carry_a_return(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        build_outcomes(TestSession(), owner_id, version_id)

        outcomes = _outcomes(version_id)
        statuses = {outcome.outcome_status for outcome in outcomes}
        assert statuses <= {"observed", "counterfactual", "immature", "unavailable"}
        assert "immature" in statuses
        for outcome in outcomes:
            if outcome.outcome_status in {"immature", "unavailable"}:
                assert outcome.label_eligible is False
                assert outcome.net_return_pct is None
                assert outcome.gross_return_pct is None
                assert outcome.exclusion_reason
            if outcome.counterfactual:
                assert outcome.outcome_source == "counterfactual_replay"

        eligibility = evaluate_eligibility(TestSession(), owner_id, version_id)
        for key, label in eligibility["exclusion_labels_zh"].items():
            assert label
            assert key in EXCLUSION_CODES
        assert eligibility["not_human_quality_evaluation"] is True
        assert eligibility["training_ready"] is False
        assert eligibility["training_blocked_by"] == "agent_strategy_consent_not_configured"


def _all_outcome_exclusions():
    from app.services.agent_strategy import EXCLUSION_LABELS_ZH

    return set(EXCLUSION_LABELS_ZH)


EXCLUSION_CODES = _all_outcome_exclusions()


# ---------------------------------------------------------------------------
# Consent is fail-closed
# ---------------------------------------------------------------------------


class TestConsentFailClosed:

    def test_api_snapshot_is_refused_and_stored_without_rows(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        build_outcomes(TestSession(), owner_id, version_id)

        preview = preview_snapshot(TestSession(), owner_id, version_id)
        assert preview["consent"]["status"] == "not_configured"
        assert preview["training_ready"] is False

        with pytest.raises(AgentStrategyRefused) as exc:
            create_snapshot(TestSession(), owner_id, version_id)
        assert exc.value.code == "agent_strategy_consent_not_configured"
        assert exc.value.details["stored_rows"] == 0

        db = TestSession()
        try:
            rows = (
                db.query(AgentDatasetSnapshot)
                .filter(AgentDatasetSnapshot.strategy_version_id == version_id)
                .all()
            )
        finally:
            db.close()
        assert len(rows) == 1
        snapshot = rows[0]
        assert snapshot.status == "refused"
        assert snapshot.consent_status == "not_configured"
        assert snapshot.included_count == 0
        assert snapshot.rows_json == []
        assert snapshot.candidate_ids_json == []
        assert snapshot.failure_reason == "agent_strategy_consent_not_configured"

    def test_consent_put_is_refused_and_persists_nothing(self, client, auth_headers):
        resp = client.put(
            "/api/v1/agent-strategy/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [], "confirm": True},
        )
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "agent_strategy_consent_not_configured"
        assert detail["persisted"] is False

    def test_capability_reports_development_not_a_delivered_feature(
        self, client, auth_headers
    ):
        resp = client.get("/api/v1/agent-strategy/capabilities", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["capability"]["status"] == "DEVELOPMENT"
        assert body["capability"]["status_label_zh"] == "開發中／尚不可在原客戶環境執行"
        assert body["enabled_in_this_process"] is False
        assert body["consent_policy"]["status"] == "not_configured"
        assert body["consent_policy"]["fail_closed"] is True
        assert body["training"] == {
            "real_training": "NOT_APPROVED",
            "model_activation": "NOT_APPROVED",
            "inference": "NOT_APPROVED",
        }


class TestScreeningWriteGate:
    """The C writes are only reachable in an explicitly enabled dev process."""

    _WRITE_ROUTES = (
        "/api/v1/agent-strategy/candidates/collect",
        "/api/v1/agent-strategy/candidates/outcomes",
        "/api/v1/agent-strategy/snapshots/preview",
        "/api/v1/agent-strategy/snapshots",
    )

    def test_every_write_route_fails_closed_with_no_side_effects(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)

        db = TestSession()
        try:
            before = (
                db.query(AgentCandidate).count(),
                db.query(AgentCandidateOutcome).count(),
                db.query(AgentDatasetSnapshot).count(),
            )
        finally:
            db.close()
        assert before == (0, 0, 0)

        for route in self._WRITE_ROUTES:
            resp = client.post(
                route,
                headers=auth_headers,
                json={"strategy_version_id": str(version_id)},
            )
            assert resp.status_code == 409, (route, resp.text)
            detail = resp.json()["detail"]
            assert detail["code"] == "agent_strategy_screening_disabled"
            assert detail["enabled"] is False

        db = TestSession()
        try:
            after = (
                db.query(AgentCandidate).count(),
                db.query(AgentCandidateOutcome).count(),
                db.query(AgentDatasetSnapshot).count(),
            )
        finally:
            db.close()
        assert after == before

    def test_the_gate_is_the_only_blocker_when_the_process_enables_it(
        self, client, auth_headers, monkeypatch
    ):
        from types import SimpleNamespace

        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        monkeypatch.setattr(
            "app.api.agent_strategy.get_settings",
            lambda: SimpleNamespace(agent_strategy_screening_enabled=True),
        )
        resp = client.post(
            "/api/v1/agent-strategy/candidates/collect",
            headers=auth_headers,
            json={"strategy_version_id": str(version_id)},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["created"] > 0


# ---------------------------------------------------------------------------
# Chronological split with gap and purge
# ---------------------------------------------------------------------------


class TestSplitManifest:

    @staticmethod
    def _rows(decision_dates, label_days=10):
        rows = []
        for index, day in enumerate(decision_dates):
            rows.append(
                {
                    "candidate_id": f"c{index}",
                    "decision_date": day.isoformat(),
                    "label_end_date": (day + timedelta(days=label_days)).isoformat(),
                    "label_eligible": True,
                }
            )
        return rows

    def test_split_is_chronological_grouped_and_never_shuffled(self):
        days = [date(2024, 1, 1) + timedelta(days=3 * i) for i in range(20)]
        rows = self._rows(days)
        # Two candidates on the same decision date must never be split apart.
        rows.append({**rows[3], "candidate_id": "c3b"})
        manifest = build_split_manifest(rows)

        assert manifest["no_shuffle"] is True
        assert manifest["grouping"] == "decision_date"
        assert manifest["refusal_reason"] is None
        assert manifest["boundaries"]

        by_id = {row["candidate_id"]: row for row in rows}
        assigned = {}
        for split_name, split in manifest["splits"].items():
            for candidate_id in split["candidate_ids"]:
                assert candidate_id not in assigned
                assigned[candidate_id] = split_name
        purged = {item["candidate_id"] for item in manifest["purged"]}
        assert set(assigned).isdisjoint(purged)
        assert set(assigned) | purged == set(by_id)

        # Grouping: a decision date lands in exactly one split.
        date_split = {}
        for candidate_id, split_name in assigned.items():
            day = by_id[candidate_id]["decision_date"]
            assert date_split.setdefault(day, split_name) == split_name

        boundary_train, boundary_val = [
            date.fromisoformat(value) for value in manifest["boundaries"]
        ]
        for candidate_id, split_name in assigned.items():
            day = date.fromisoformat(by_id[candidate_id]["decision_date"])
            if split_name == "train":
                assert day < boundary_train
            elif split_name == "validation":
                assert boundary_train <= day < boundary_val
            else:
                assert day >= boundary_val
            assert day + timedelta(days=10) < (
                boundary_train if split_name == "train" else boundary_val
            ) or split_name == "test"

    def test_label_windows_crossing_a_boundary_are_purged(self):
        days = [date(2024, 1, 1) + timedelta(days=3 * i) for i in range(20)]
        manifest = build_split_manifest(self._rows(days))
        assert manifest["purged"], "overlapping label windows must be purged"
        reasons = {item["reason"] for item in manifest["purged"]}
        assert reasons <= {"label_window_crosses_boundary", "gap_before_boundary"}
        assert manifest["total_assigned"] + manifest["total_purged"] == manifest["total_eligible"]

    def test_too_few_time_groups_refuses_the_split(self):
        days = [date(2024, 1, 1) + timedelta(days=3 * i) for i in range(4)]
        manifest = build_split_manifest(self._rows(days))
        assert manifest["refusal_reason"] == "insufficient_time_groups"
        assert manifest["measured_groups"] == 4
        assert manifest["required_groups"] >= 10
        assert all(
            split["candidate_ids"] == []
            for split in manifest["splits"].values()
        )


# ---------------------------------------------------------------------------
# Training / inference contract lineage
# ---------------------------------------------------------------------------


class TestContract:

    def test_contract_is_development_only_and_requires_lineage(self):
        contract = training_contract()
        assert contract["real_training"] == "NOT_APPROVED"
        assert contract["model_activation"] == "NOT_APPROVED"
        assert contract["inference"] == "NOT_APPROVED"
        assert contract["preprocessing_fit_scope"] == "train_only"
        assert contract["split_policy"]["no_shuffle"] is True
        assert contract["split_policy"]["gap_days"] == HOLD_BARS + 1
        baseline_keys = {item["key"] for item in contract["baselines"]}
        assert baseline_keys == {"take_all", "skip_all", "random_skip_seed_42"}
        assert "coverage" in contract["evaluation_metrics"]

        incomplete = validate_lineage({"strategy_version_id": "v1"})
        assert incomplete["valid"] is False
        assert "model_artifact_checksum" in incomplete["missing"]
        assert incomplete["training_performed"] is False

        complete = {name: "x" for name in contract["lineage_required"]}
        assert validate_lineage(complete)["valid"] is True
        assert validate_lineage(complete)["model_activated"] is False


# ---------------------------------------------------------------------------
# Existing paper trades are read-only
# ---------------------------------------------------------------------------


class TestExistingTradeTraceability:

    def test_functional_test_buy_is_excluded_without_rewriting_anything(
        self, client, auth_headers
    ):
        portfolio = client.post(
            "/api/v1/paper-portfolio",
            headers=auth_headers,
            json={"initial_virtual_cash": 100000.0, "currency": "USD"},
        )
        assert portfolio.status_code == 201, portfolio.text
        portfolio_id = portfolio.json()["id"]

        order = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders",
            headers=auth_headers,
            json={
                "asset_id": INSTRUMENT,
                "side": "BUY",
                "quantity": 1,
                "order_type": "market",
                "rationale": "E2E functional test trade",
                "expected_outcome": "validation only",
                "self_reported_confidence": "high",
            },
        )
        assert order.status_code == 201, order.text

        db = TestSession()
        try:
            before_orders = _paper_snapshot(db)
            before_human = db.query(HumanDecisionEvent).count()
            before_labels = db.query(DecisionLabel).count()
        finally:
            db.close()

        report = existing_trade_eligibility(
            TestSession(), _owner_id(), portfolio_id=uuid.UUID(portfolio_id)
        )
        assert report["read_only"] is True
        assert report["writes_performed"] == 0
        assert report["order_count"] == 1
        entry = report["orders"][0]
        assert entry["order_actor_type"] == "human"
        assert entry["functional_test"] is True
        assert entry["eligible_for_c_dataset"] is False
        assert entry["exclusion_reason"] == "functional_test_trade"
        assert entry["fill_ids"]

        db = TestSession()
        try:
            assert _paper_snapshot(db) == before_orders
            assert db.query(HumanDecisionEvent).count() == before_human
            assert db.query(DecisionLabel).count() == before_labels
        finally:
            db.close()

    def test_declared_functional_test_is_excluded_before_lineage_lookup(
        self, client, auth_headers
    ):
        portfolio = client.post(
            "/api/v1/paper-portfolio",
            headers=auth_headers,
            json={"initial_virtual_cash": 100000.0, "currency": "USD"},
        )
        assert portfolio.status_code == 201, portfolio.text
        portfolio_id = portfolio.json()["id"]

        order = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders",
            headers=auth_headers,
            json={
                "asset_id": INSTRUMENT,
                "side": "BUY",
                "quantity": 1,
                "order_type": "market",
                "rationale": "placed to check the order and ledger chain",
                "expected_outcome": "validation only",
                "self_reported_confidence": "high",
            },
        )
        assert order.status_code == 201, order.text
        order_id = order.json()["id"]

        # The prose carries no marker, so eligibility is only reachable when the
        # trade is declared a functional test by its id.
        report = existing_trade_eligibility(
            TestSession(),
            _owner_id(),
            portfolio_id=uuid.UUID(portfolio_id),
            explicit_functional_test_ids=[order_id],
        )
        entry = report["orders"][0]
        assert entry["functional_test"] is True
        assert entry["eligible_for_c_dataset"] is False
        assert entry["exclusion_reason"] == "functional_test_trade"
        assert entry["lineage_status"] == "not_checked_functional_test"
        assert entry["candidate_id"] is None

        resp = client.get(
            "/api/v1/agent-strategy/existing-trades/eligibility",
            headers=auth_headers,
            params=[
                ("portfolio_id", portfolio_id),
                ("explicit_functional_test_order_ids", order_id),
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["orders"][0]["exclusion_reason"] == "functional_test_trade"

    def test_an_order_without_strategy_lineage_is_excluded_on_that_ground(
        self, client, auth_headers
    ):
        portfolio = client.post(
            "/api/v1/paper-portfolio",
            headers=auth_headers,
            json={"initial_virtual_cash": 100000.0, "currency": "USD"},
        ).json()
        order = client.post(
            f"/api/v1/paper-portfolio/{portfolio['id']}/orders",
            headers=auth_headers,
            json={
                "asset_id": INSTRUMENT,
                "side": "BUY",
                "quantity": 1,
                "order_type": "market",
                "rationale": "manual exploratory entry",
                "expected_outcome": "observe one session",
                "self_reported_confidence": "medium",
            },
        )
        assert order.status_code == 201, order.text

        report = existing_trade_eligibility(
            TestSession(), _owner_id(), portfolio_id=uuid.UUID(portfolio["id"])
        )
        assert report["orders"][0]["exclusion_reason"] == "no_strategy_lineage"
        assert report["orders"][0]["lineage_status"] == "not_linked"

    def test_a_shared_execution_date_is_traceability_not_confirmed_linkage(
        self, client, auth_headers
    ):
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers)
        owner_id = _owner_id()
        collect_candidates(TestSession(), owner_id, version_id)
        build_outcomes(TestSession(), owner_id, version_id)

        db = TestSession()
        try:
            outcome = (
                db.query(AgentCandidateOutcome)
                .join(
                    AgentCandidate,
                    AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                )
                .filter(
                    AgentCandidate.strategy_version_id == version_id,
                    AgentCandidateOutcome.outcome_status == "counterfactual",
                    AgentCandidateOutcome.label_eligible.is_(True),
                )
                .order_by(AgentCandidate.signal_date)
                .first()
            )
            assert outcome is not None
            entry_date = outcome.entry_execution_date
            portfolio = PaperPortfolio(
                owner_id=owner_id,
                currency="USD",
                initial_virtual_cash=100000.0,
                current_virtual_cash=90000.0,
                status=PortfolioStatus.active,
            )
            db.add(portfolio)
            db.flush()
            order, _ = _filled_round_trip_leg(
                db, portfolio, OrderSide.BUY, entry_date, 100.0, -1000.0, version_id
            )
            # No functional-test marker, no order/fill id in any outcome.
            order.rationale = "followed the published rule signal"
            db.commit()
            portfolio_id = portfolio.id
        finally:
            db.close()

        report = existing_trade_eligibility(
            TestSession(), owner_id, portfolio_id=portfolio_id
        )
        entry = report["orders"][0]
        assert entry["functional_test"] is False
        assert entry["lineage_status"] == "date_only_unconfirmed"
        assert entry["exclusion_reason"] == "unconfirmed_candidate_linkage"
        assert entry["linkage_evidence"] is None
        assert entry["candidate_id"] is not None
        assert entry["outcome_id"] is None
        assert entry["eligible_for_c_dataset"] is False
        # Absence of a marker never stands in for a research-purpose check.
        assert entry["linkage_confirmation"]["candidate_linkage"] == (
            "date_only_unconfirmed"
        )
        assert entry["linkage_confirmation"]["data_source_confirmed"] is False
        assert entry["linkage_confirmation"]["research_purpose_confirmed"] is False
        assert entry["linkage_confirmation"]["outcome_policy_confirmed"] is False
        assert entry["linkage_confirmation"]["authorization_scope_confirmed"] is False


def _paper_snapshot(db):
    """Everything that would change if the report were not read-only."""
    orders = [
        (
            str(order.id),
            order.status.value,
            order.actor_type,
            order.model_or_strategy_version_id,
            order.data_cutoff_at,
        )
        for order in db.query(PaperOrder).order_by(PaperOrder.id).all()
    ]
    fills = [
        (
            str(fill.id),
            fill.execution_date,
            fill.execution_price_before_costs,
            fill.commission_amount,
            fill.net_cash_change,
        )
        for fill in db.query(PaperFill).order_by(PaperFill.id).all()
    ]
    cash = [
        (str(portfolio.id), portfolio.current_virtual_cash)
        for portfolio in db.query(PaperPortfolio).order_by(PaperPortfolio.id).all()
    ]
    decisions = [
        (str(decision.id), decision.actor_type.value, decision.outcome_status.value)
        for decision in db.query(DecisionRecord).order_by(DecisionRecord.id).all()
    ]
    return orders, fills, cash, decisions
