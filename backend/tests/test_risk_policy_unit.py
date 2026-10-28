"""The shared policy, tested where it lives: no HTTP, no lane mechanics.

``test_risk_policy_across_lanes.py`` proves each lane actually asks this policy.
This file pins the answers themselves — the order the gates run in, what the
daily counter includes, how the cap is valued, and that an unknown price is a
refusal rather than a pass — so a lane regression and a policy regression stay
distinguishable.

All figures are synthetic. The account is created through the real route so the
row has a real owner; everything else is asserted on the policy's own return
value and on rows read back from the test database.
"""

import uuid
from datetime import date, datetime

import pytest

from tests.conftest import TestSession
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import PaperOrder, PaperPortfolio, PaperPosition, OrderSide, OrderStatus, PositionStatus
from app.services import risk_policy
from app.services.risk_policy import (
    RiskInputs,
    RiskPolicy,
    count_entries,
    count_entries_on_utc_date,
    evaluate_risk,
    value_open_positions,
)

SYMBOL = "DEMO-ALPHA"
OTHER = "DEMO-BETA"
PROVIDER = "demo"


def _inputs(**overrides):
    base = dict(
        lane="unit",
        side="BUY",
        asset_id=SYMBOL,
        quantity=10.0,
        portfolio_status="active",
        portfolio_cash=100_000.0,
        held_quantity=0.0,
        price=100.0,
        open_positions_value=0.0,
        entries_on_date=0,
        decision_date="2024-02-01",
    )
    base.update(overrides)
    return RiskInputs(**base)


def _portfolio(client, auth_headers, cash=100_000.0):
    resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _load(db, portfolio_id):
    return db.query(PaperPortfolio).filter(
        PaperPortfolio.id == uuid.UUID(portfolio_id)
    ).one()


def _order(db, portfolio, **overrides):
    row = PaperOrder(
        portfolio_id=portfolio.id,
        asset_id=overrides.pop("asset_id", SYMBOL),
        side=overrides.pop("side", OrderSide.BUY),
        quantity=overrides.pop("quantity", 1.0),
        status=overrides.pop("status", OrderStatus.pending),
        actor_type=overrides.pop("actor_type", "human"),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    db.add(row)
    return row


class TestTheDecisionItself:
    def test_a_clean_entry_passes_and_states_the_numbers(self):
        decision = evaluate_risk(_inputs())
        assert decision.allowed is True
        assert decision.code is None
        assert decision.facts["lane"] == "unit"
        assert decision.facts["decision_price"] == 100.0
        assert decision.facts["policy"]["max_daily_entries"] == 10

    def test_an_unknown_side_is_refused_not_assumed(self):
        decision = evaluate_risk(_inputs(side="SHORT"))
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_UNSUPPORTED_SIDE

    def test_a_paused_account_refuses_an_entry(self):
        decision = evaluate_risk(_inputs(portfolio_status="paused"))
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_PORTFOLIO_PAUSED

    def test_a_paused_account_still_allows_the_exit(self):
        decision = evaluate_risk(
            _inputs(portfolio_status="paused", side="SELL", quantity=10, held_quantity=10)
        )
        assert decision.allowed is True, "closing risk must not wait on the pause"

    def test_a_retired_or_completed_account_refuses_both_sides(self):
        for status, code in (
            ("retired", risk_policy.CODE_PORTFOLIO_RETIRED),
            ("completed", risk_policy.CODE_PORTFOLIO_COMPLETED),
        ):
            entry = evaluate_risk(_inputs(portfolio_status=status))
            exit_ = evaluate_risk(
                _inputs(portfolio_status=status, side="SELL", held_quantity=10)
            )
            assert (entry.allowed, entry.code) == (False, code)
            assert (exit_.allowed, exit_.code) == (False, code)

    def test_a_sell_cannot_go_past_the_held_quantity(self):
        decision = evaluate_risk(
            _inputs(side="SELL", quantity=11, held_quantity=10)
        )
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_QUANTITY_EXCEEDS_POSITION


class TestTheAllowList:
    def test_a_policy_list_narrows_the_lane(self):
        policy = RiskPolicy(allowed_instruments=frozenset({OTHER}))
        decision = evaluate_risk(_inputs(), policy=policy)
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_INSTRUMENT_NOT_ALLOWED

    def test_the_lane_list_is_an_intersection_not_an_override(self):
        policy = RiskPolicy(allowed_instruments=frozenset({SYMBOL, OTHER}))
        decision = evaluate_risk(_inputs(), policy=policy, lane_allowed_instruments=[OTHER])
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_INSTRUMENT_NOT_ALLOWED

    def test_neither_list_set_means_no_list(self):
        assert evaluate_risk(_inputs()).allowed is True

    def test_the_exit_is_not_held_to_the_allow_list(self):
        policy = RiskPolicy(allowed_instruments=frozenset({OTHER}))
        decision = evaluate_risk(
            _inputs(side="SELL", held_quantity=10), policy=policy
        )
        assert decision.allowed is True


class TestThePositionCap:
    def test_a_cap_below_one_refuses_without_a_decision_price(self):
        policy = RiskPolicy(max_position_pct=0.5)
        decision = evaluate_risk(_inputs(price=None), policy=policy)
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_RISK_PRICE_UNAVAILABLE

    def test_a_cap_below_one_refuses_without_a_book_valuation(self):
        policy = RiskPolicy(max_position_pct=0.5)
        decision = evaluate_risk(
            _inputs(open_positions_value=None, unpriced_assets=(OTHER,)), policy=policy
        )
        assert decision.allowed is False
        assert decision.code == risk_policy.CODE_RISK_PRICE_UNAVAILABLE
        assert decision.facts["unpriced_assets"] == [OTHER]

    def test_a_cap_of_one_hundred_percent_needs_no_mark(self):
        decision = evaluate_risk(_inputs(price=None, open_positions_value=None))
        assert decision.allowed is True, (
            "at 100% a cash-funded buy cannot exceed cash + positions, so a "
            "missing mark must not refuse a legal trade"
        )

    def test_the_cap_value_is_order_value_over_cash_plus_positions(self):
        policy = RiskPolicy(max_position_pct=0.5)
        # 477 x 105 = 50,085 > 50% of (100,000 + 0).
        refused = evaluate_risk(
            _inputs(quantity=477, price=105.0), policy=policy
        )
        assert refused.allowed is False
        assert refused.code == risk_policy.CODE_POSITION_LIMIT
        assert refused.facts["order_value"] == 50_085.0
        assert refused.facts["portfolio_value"] == 100_000.0
        assert refused.facts["position_ratio"] == pytest.approx(0.50085)

        # 476 x 105 = 49,980 fits.
        allowed = evaluate_risk(_inputs(quantity=476, price=105.0), policy=policy)
        assert allowed.allowed is True

    def test_open_positions_count_towards_the_book(self):
        policy = RiskPolicy(max_position_pct=0.5)
        # Book = 40,000 cash + 60,000 positions = 100,000; half is 50,000.
        at_half = evaluate_risk(
            _inputs(quantity=500, price=100.0, portfolio_cash=40_000.0,
                    open_positions_value=60_000.0),
            policy=policy,
        )
        over_half = evaluate_risk(
            _inputs(quantity=501, price=100.0, portfolio_cash=40_000.0,
                    open_positions_value=60_000.0),
            policy=policy,
        )
        assert at_half.allowed is True
        assert over_half.allowed is False
        assert over_half.code == risk_policy.CODE_POSITION_LIMIT


class TestTheDailyBudget:
    def test_the_limit_is_a_boundary_not_an_estimate(self):
        policy = RiskPolicy(max_daily_entries=1)
        at_limit = evaluate_risk(_inputs(entries_on_date=1), policy=policy)
        below = evaluate_risk(_inputs(entries_on_date=0), policy=policy)
        assert at_limit.allowed is False
        assert at_limit.code == risk_policy.CODE_DAILY_ENTRY_LIMIT
        assert below.allowed is True

    def test_the_exit_does_not_consume_or_respect_the_entry_budget(self):
        policy = RiskPolicy(max_daily_entries=1)
        decision = evaluate_risk(
            _inputs(side="SELL", held_quantity=10, entries_on_date=99), policy=policy
        )
        assert decision.allowed is True


class TestCountingRules:
    def test_only_buys_on_that_market_date_count_and_rejected_does_not(
        self, client, auth_headers
    ):
        portfolio_id = _portfolio(client, auth_headers)
        db = TestSession()
        try:
            portfolio = _load(db, portfolio_id)
            _order(db, portfolio, data_cutoff_at="2024-02-01")                     # counts
            _order(db, portfolio, data_cutoff_at="2024-02-01",
                   status=OrderStatus.cancelled)                                   # counts
            _order(db, portfolio, data_cutoff_at="2024-02-01",
                   status=OrderStatus.rejected)                                    # never an intent
            _order(db, portfolio, data_cutoff_at="2024-02-02")                     # other date
            _order(db, portfolio, data_cutoff_at="2024-02-01",
                   side=OrderSide.SELL)                                            # an exit
            db.commit()
            assert count_entries(db, portfolio, market_date="2024-02-01") == 2
            assert count_entries(db, portfolio, market_date="2024-02-02") == 1
        finally:
            db.close()

    def test_the_direct_lane_counts_by_utc_submission_date(
        self, client, auth_headers
    ):
        portfolio_id = _portfolio(client, auth_headers)
        db = TestSession()
        try:
            portfolio = _load(db, portfolio_id)
            _order(db, portfolio, created_at=datetime(2024, 2, 1, 0, 0))
            _order(db, portfolio, created_at=datetime(2024, 2, 1, 23, 59, 59))
            _order(db, portfolio, created_at=datetime(2024, 2, 2, 0, 0))
            _order(db, portfolio, created_at=datetime(2024, 2, 1, 12, 0),
                   status=OrderStatus.rejected)
            _order(db, portfolio, created_at=datetime(2024, 2, 1, 12, 0),
                   side=OrderSide.SELL)
            db.commit()
            assert count_entries_on_utc_date(db, portfolio, day=date(2024, 2, 1)) == 2
            assert count_entries_on_utc_date(db, portfolio, day=date(2024, 2, 2)) == 1
        finally:
            db.close()

    def test_the_counter_is_per_portfolio(self, client, auth_headers):
        first_id = _portfolio(client, auth_headers)
        second_id = _portfolio(client, auth_headers)
        db = TestSession()
        try:
            first = _load(db, first_id)
            second = _load(db, second_id)
            _order(db, first, data_cutoff_at="2024-02-01")
            db.commit()
            assert count_entries(db, first, market_date="2024-02-01") == 1
            assert count_entries(db, second, market_date="2024-02-01") == 0
        finally:
            db.close()


class TestValuingTheBook:
    def test_a_known_price_wins_over_the_stored_bar(self, client, auth_headers):
        portfolio_id = _portfolio(client, auth_headers)
        db = TestSession()
        try:
            portfolio = _load(db, portfolio_id)
            db.add(PaperPosition(
                portfolio_id=portfolio.id, asset_id=SYMBOL, quantity=10,
                average_entry_price=90.0, status=PositionStatus.open,
            ))
            db.add(MarketDataBar(
                instrument_id=SYMBOL, provider_id=PROVIDER,
                bar_date=date(2024, 2, 1),
                bar_timestamp=datetime(2024, 2, 1, 0, 0),
                open=100.0, high=100.0, low=100.0, close=100.0, volume=1,
            ))
            db.commit()
            db.refresh(portfolio)

            total, unpriced = value_open_positions(
                db, portfolio, price_by_asset={SYMBOL: 120.0}
            )
            assert (total, unpriced) == (1200.0, ())

            # Without a lane price the stored bar on/before the date is used.
            total, unpriced = value_open_positions(
                db, portfolio, price_by_asset={}, on_or_before="2024-02-01",
                provider_id=PROVIDER,
            )
            assert (total, unpriced) == (1000.0, ())
        finally:
            db.close()

    def test_an_unpriceable_position_is_reported_not_worth_zero(
        self, client, auth_headers
    ):
        portfolio_id = _portfolio(client, auth_headers)
        db = TestSession()
        try:
            portfolio = _load(db, portfolio_id)
            db.add(PaperPosition(
                portfolio_id=portfolio.id, asset_id=SYMBOL, quantity=10,
                average_entry_price=90.0, status=PositionStatus.open,
            ))
            db.commit()
            db.refresh(portfolio)

            total, unpriced = value_open_positions(
                db, portfolio, price_by_asset={}
            )
            assert total == 0.0
            assert unpriced == (SYMBOL,)
        finally:
            db.close()
