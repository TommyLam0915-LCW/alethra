"""Golden cases for the annualization contract (Phase 3).

Every expected number below is hand-derived from the calendar in
``app/providers/instruments.py`` and written into the test as arithmetic, not
copied out of the code under test, so these fail when the method drifts:

* US sessions listed for 2024 run 2024-01-02 .. 2024-12-31: 252 of them, separated
  by 364 days, so a year of them carries ``252 * 365.25 / 364`` = 252.865385
  observations and spans ``364 / 365.25`` = 0.996578 elapsed years.
* A crypto daily series is quoted every calendar day, so 720 consecutive bars span
  ``719 / 365.25`` = 1.968515 years at 365.25 observations a year. Compounding
  +100% over that window is ``2 ** (1 / 1.968515) - 1`` = 42.2075%, where the rule
  this contract replaced read the same rows as ``720 / 252`` = 2.857143 years and
  reported 27.4561%.
* A per-observation standard deviation scales by ``sqrt(observations per year)``,
  so a 3% crypto daily standard deviation is ``0.03 * sqrt(365.25) * 100`` =
  57.3345%, not ``0.03 * sqrt(252) * 100`` = 47.6235%.

The legacy column is spelled out here as ``252 * (1440 / interval)`` — the deleted
``_trades_per_year`` — so a regression that reintroduces it fails a test instead of
looking plausible.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List
from uuid import uuid4

import pytest

from app.api.runs import METRICS_FIELDS
from app.api.schemas import AdHocBacktestResponse, AdHocDurableRunHint, ResearchRunResponse
from app.data.demo_data import get_demo_ohlcv
from app.engine import annualization
from app.engine.annualization import (
    CALENDAR_SOURCE,
    METHOD_VERSION,
    MEAN_CALENDAR_DAYS_PER_YEAR,
    MINUTES_PER_DAY,
    MIN_YEARS_FOR_ANNUALIZATION,
    Basis,
    annualized_return_pct,
    annualized_volatility_pct,
    basis_for_series,
    basis_from_calendar,
    market_of,
    measured_basis,
    observations_per_year,
    unavailable,
)
from app.engine.backtest import run_backtest
from app.engine.metrics import compute_metrics
from app.providers.instruments import Market, trading_days_between
from app.services.skill_attribution import _compute_group_metrics, _group_basis, _sharpe_ratio
from app.services.strategy_lab import LabBar
from app.services.strategy_research import prepare_indicators

AAPL = "US:NASDAQ:AAPL"
TENCENT = "HK:HKEX:00700"
BTC = "CRYPTO:KRAKEN:BTC-USD"
# Declared in the Market enum but given no structure in instruments.py.
EUR_USD = "FOREX:OANDA:EUR-USD"
# Demo assets carry no market prefix at all, so only their timestamps describe them.
DEMO_ASSET = "DEMO-ALPHA"

MEAN = MEAN_CALENDAR_DAYS_PER_YEAR
BTC_FIRST = date(2024, 11, 13)
BTC_ROWS = 720


def us_sessions(first: date, last: date) -> List[date]:
    return list(trading_days_between(Market.US, first, last))


def consecutive_days(first: date, count: int) -> List[date]:
    return [first + timedelta(days=i) for i in range(count)]


def legacy_observations_per_year(interval_minutes: int) -> float:
    """The deleted ``skill_attribution._trades_per_year``, kept to be contradicted."""
    return 252 * (MINUTES_PER_DAY / interval_minutes)


def legacy_annualized_return(total_return_pct: float, bars: int) -> float:
    """The deleted ``metrics`` rule: bars over 252, whatever the market or grain."""
    years = bars / 252
    return ((1 + total_return_pct / 100) ** (1 / years) - 1) * 100


def _num(value: Any) -> float:
    """Journal money fields arrive pre-formatted for the API; compare them as numbers."""
    return float(value)


def _btc_rows() -> List[date]:
    return consecutive_days(BTC_FIRST, BTC_ROWS)


# ---------------------------------------------------------------------------
# Equity daily: the case the 252 constant was actually written for
# ---------------------------------------------------------------------------

class TestEquityDaily:
    def test_2024_session_list_comes_from_the_calendar(self):
        sessions = us_sessions(date(2024, 1, 1), date(2024, 12, 31))
        # Guards the arithmetic in the tests below: 252 sessions, 364 days apart.
        assert len(sessions) == 252
        assert sessions[0] == date(2024, 1, 2)
        assert sessions[-1] == date(2024, 12, 31)

    def test_observations_per_year_is_derived_not_assumed(self):
        basis = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 12, 31)))
        assert basis.available is True
        assert basis.observations_per_year == round(252 * MEAN / 364, 6)
        assert basis.observations_per_year == pytest.approx(252.865385, abs=1e-6)
        # The corrected rule lands near 252 for US equities because the calendar
        # says so, not because the code said so.
        assert abs(basis.observations_per_year - 252) < 1.5

    def test_equity_daily_does_not_become_365(self):
        basis = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 12, 31)))
        assert basis.observations_per_year != MEAN
        assert basis.trading_days_per_year != MEAN

    def test_us_daily_sample_shape(self):
        basis = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 12, 31)))
        assert basis.frequency == "daily"
        assert basis.frequency_source == "calendar"
        assert basis.market == "US"
        assert basis.interval_minutes == MINUTES_PER_DAY
        assert basis.bars_per_trading_day == 1.0
        # 09:30-16:00 ET, with the pre-market and after-hours sessions excluded.
        assert basis.session_minutes_per_day == 390.0
        assert basis.calendar_source == CALENDAR_SOURCE
        assert basis.inside_declared_coverage is True
        assert basis.elapsed_years == round(364 / MEAN, 6)

    def test_gapless_year_reports_full_coverage(self):
        basis = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 12, 31)))
        assert basis.observation_count == 252
        assert basis.expected_observations == 252.0
        assert basis.coverage_pct == 100.0

    def test_one_calendar_year_still_gets_an_annualized_return(self):
        # 252 sessions between Jan 2 and Dec 31 span 0.9966 years: a whole calendar
        # year of session data can never span 365.25 days between its first and last
        # observation, so the gate keeps its tolerance and this metric survives.
        basis = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 12, 31)))
        assert basis.elapsed_years >= MIN_YEARS_FOR_ANNUALIZATION
        annualized = annualized_return_pct(20.0, basis)
        assert annualized == pytest.approx(((1.20) ** (1 / (364 / MEAN)) - 1) * 100, abs=1e-4)
        assert annualized == pytest.approx(20.0751, abs=1e-4)

    def test_two_us_years_annualize_on_their_own_session_count(self):
        sessions = us_sessions(date(2024, 1, 1), date(2025, 12, 31))
        basis = basis_for_series(AAPL, sessions)
        assert len(sessions) == 502
        assert basis.observations_per_year == round(502 * MEAN / 729, 6)
        assert basis.elapsed_years == round(729 / MEAN, 6)
        assert annualized_return_pct(100.0, basis) == pytest.approx(
            (2 ** (1 / (729 / MEAN)) - 1) * 100, abs=1e-4
        )


# ---------------------------------------------------------------------------
# Crypto daily: the market 252 was wrong for
# ---------------------------------------------------------------------------

class TestCryptoDaily:
    def test_observations_per_year_is_the_mean_calendar_year(self):
        basis = basis_for_series(BTC, _btc_rows())
        assert basis.available is True
        # A continuously quoted venue has no closures to subtract: 365.25 exactly.
        assert basis.observations_per_year == MEAN
        assert basis.trading_days_per_year == MEAN
        assert basis.market == "CRYPTO"

    def test_720_bars_span_their_own_gap_not_720_over_252(self):
        basis = basis_for_series(BTC, _btc_rows())
        assert basis.observation_count == 720
        assert basis.elapsed_years == round(719 / MEAN, 6)
        assert basis.elapsed_years == pytest.approx(1.968515, abs=1e-6)
        assert basis.expected_observations == 720.0
        assert basis.coverage_pct == 100.0

    def test_annualized_return_is_not_bar_count_over_252(self):
        basis = basis_for_series(BTC, _btc_rows())
        corrected = annualized_return_pct(100.0, basis)
        assert corrected == pytest.approx((2 ** (1 / (719 / MEAN)) - 1) * 100, abs=1e-4)
        assert corrected == pytest.approx(42.2075, abs=1e-4)
        stale = legacy_annualized_return(100.0, BTC_ROWS)
        assert stale == pytest.approx(27.4561, abs=1e-4)
        # The old rule called a 2-year sample a 2.86-year one and diluted the return.
        assert corrected != pytest.approx(stale, abs=1.0)

    def test_annualized_volatility_is_not_scaled_by_252(self):
        basis = basis_for_series(BTC, _btc_rows())
        corrected = annualized_volatility_pct(0.03, basis)
        assert corrected == pytest.approx(0.03 * math.sqrt(365.25) * 100, abs=1e-4)
        assert corrected == pytest.approx(57.3345, abs=1e-4)
        assert corrected != pytest.approx(0.03 * math.sqrt(252) * 100, abs=1e-4)

    def test_crypto_and_equity_daily_rates_differ(self):
        crypto = basis_for_series(BTC, _btc_rows())
        equity = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 12, 31)))
        assert crypto.observations_per_year != equity.observations_per_year
        assert crypto.session_minutes_per_day == MINUTES_PER_DAY
        assert equity.session_minutes_per_day == 390.0


class TestHkDaily:
    def test_hk_sessions_and_their_own_rate(self):
        hk = list(trading_days_between(Market.HK, date(2024, 1, 1), date(2025, 12, 31)))
        basis = basis_for_series(TENCENT, hk)
        assert len(hk) == 493
        # HK's two regular sessions total 330 minutes, not the US 390.
        assert basis.session_minutes_per_day == 330.0
        assert basis.observations_per_year == round(493 * MEAN / 729, 6)
        assert basis.observations_per_year == pytest.approx(247.007202, abs=1e-6)
        assert basis.market == "HK"


# ---------------------------------------------------------------------------
# Irregular dates: holes are recorded, not smoothed over
# ---------------------------------------------------------------------------

class TestIrregularObservations:
    def _gapless(self) -> List[date]:
        return us_sessions(date(2024, 1, 1), date(2024, 12, 31))

    def test_missing_rows_lower_coverage_without_changing_the_rate(self):
        gapped = [d for d in self._gapless() if not date(2024, 4, 1) <= d <= date(2024, 6, 28)]
        basis = basis_for_series(AAPL, gapped)
        full = basis_for_series(AAPL, self._gapless())
        assert basis.available is True
        assert len(gapped) == 189
        assert basis.observation_count == 189
        assert basis.coverage_pct == round(189 / 252 * 100, 4)
        assert basis.coverage_pct == 75.0
        # The frequency is a property of the market, so holes do not change it.
        assert basis.observations_per_year == full.observations_per_year

    def test_three_missing_sessions_are_visible_in_coverage(self):
        sessions = self._gapless()
        holes = [d for i, d in enumerate(sessions) if i not in (10, 50, 100)]
        basis = basis_for_series(AAPL, holes)
        assert basis.coverage_pct == round(249 / 252 * 100, 4)

    def test_order_of_arrival_does_not_change_the_basis(self):
        sessions = self._gapless()
        shuffled = list(sessions)
        random.Random(20261110).shuffle(shuffled)
        assert basis_for_series(AAPL, shuffled).as_dict() == basis_for_series(
            AAPL, sessions
        ).as_dict()

    def test_iso_strings_and_datetimes_agree_with_dates(self):
        as_dates = self._gapless()
        as_strings = [d.isoformat() for d in as_dates]
        as_stamps = [
            datetime(d.year, d.month, d.day, 21, 0, tzinfo=timezone.utc) for d in as_dates
        ]
        from_strings = basis_for_series(AAPL, as_strings)
        from_dates = basis_for_series(AAPL, as_dates)
        assert from_strings.as_dict() == from_dates.as_dict()
        # Times of day are ignored on the daily path; the session date is the key.
        assert basis_for_series(AAPL, as_stamps).as_dict() == from_dates.as_dict()

    def test_sub_daily_series_may_repeat_a_session_date(self):
        stamps = [
            datetime(2024, 3, 1, 14, t, tzinfo=timezone.utc) for t in (0, 15, 30, 45)
        ] + [datetime(2024, 3, 4, 14, t, tzinfo=timezone.utc) for t in (0, 15, 30, 45)]
        basis = basis_for_series(AAPL, stamps, interval_minutes=15)
        assert basis.available is True
        assert basis.frequency == "intraday"
        # Two US sessions in the window, 26 fifteen-minute bars each.
        assert basis.bars_per_trading_day == 26.0
        assert basis.trading_days_per_year == round(2 * MEAN / 3, 6)
        assert basis.observations_per_year == round(2 * MEAN / 3 * 26, 6)
        assert basis.coverage_pct == round(8 / 52 * 100, 4)


# ---------------------------------------------------------------------------
# Leap years: the span comes from the calendar, not from a year length
# ---------------------------------------------------------------------------


class TestLeapDay:
    """2024 has 366 calendar days and 252 US sessions; 2025 has 365 and 250.

    Elapsed years are ``(last - first).days / 365.25``, so a leap day has to
    show up as one more day of span and one more observation — never as a
    longer year, and never as a rate of 366.
    """

    def test_the_leap_day_is_a_real_observation(self):
        leap = consecutive_days(date(2024, 2, 26), 5)
        assert leap == [
            date(2024, 2, 26),
            date(2024, 2, 27),
            date(2024, 2, 28),
            date(2024, 2, 29),
            date(2024, 3, 1),
        ]
        # Five names from the same start land on a different last date in a
        # year without a 29 February.
        plain = consecutive_days(date(2025, 2, 26), 5)
        assert plain == [
            date(2025, 2, 26),
            date(2025, 2, 27),
            date(2025, 2, 28),
            date(2025, 3, 1),
            date(2025, 3, 2),
        ]

    def test_span_across_the_leap_day_counts_the_extra_day(self):
        leap = basis_for_series(BTC, consecutive_days(date(2024, 2, 26), 6))
        plain = basis_for_series(BTC, consecutive_days(date(2025, 2, 26), 5))
        assert leap.observation_count == 6
        assert plain.observation_count == 5
        # 2024-02-26..2024-03-02 is 5 days apart; 2025-02-26..2025-03-02 is 4.
        assert leap.elapsed_years == round(5 / MEAN, 6)
        assert plain.elapsed_years == round(4 / MEAN, 6)
        assert leap.elapsed_years != plain.elapsed_years

    def test_a_full_leap_year_spans_365_days_not_366(self):
        basis = basis_for_series(BTC, consecutive_days(date(2024, 1, 1), 366))
        assert basis.observation_count == 366
        assert basis.available is True
        # 2024-01-01 to 2024-12-31 is 365 days of span even though the year has
        # 366 of them: the first day carries no span before it.
        assert basis.elapsed_years == round(365 / MEAN, 6)
        assert basis.elapsed_years == pytest.approx(0.999316, abs=1e-6)

    def test_a_full_common_year_spans_one_day_less(self):
        leap = basis_for_series(BTC, consecutive_days(date(2024, 1, 1), 366))
        common = basis_for_series(BTC, consecutive_days(date(2025, 1, 1), 365))
        assert common.observation_count == 365
        assert common.elapsed_years == round(364 / MEAN, 6)
        assert common.elapsed_years == pytest.approx(0.996578, abs=1e-6)
        assert round(leap.elapsed_years - common.elapsed_years, 6) == round(1 / MEAN, 6)

    def test_leap_day_does_not_become_the_observations_per_year(self):
        basis = basis_for_series(BTC, consecutive_days(date(2024, 1, 1), 366))
        # The rate is the mean calendar year, defined once for the contract;
        # it is never the count of days in the sample's own year.
        assert basis.observations_per_year == MEAN
        assert basis.observations_per_year not in (366.0, 365.0, 364.0)

    def test_two_years_around_a_leap_day_keep_their_real_span(self):
        basis = basis_for_series(BTC, consecutive_days(date(2024, 1, 1), 731))
        assert basis.observation_count == 731
        # 2024-01-01..2025-12-31 is 730 days: 366 + 365 - 1.
        assert basis.elapsed_years == round(730 / MEAN, 6)
        assert basis.elapsed_years == pytest.approx(1.998631, abs=1e-6)

    def test_the_us_calendar_lists_the_leap_day_session(self):
        sessions_2024 = us_sessions(date(2024, 1, 1), date(2024, 12, 31))
        sessions_2025 = us_sessions(date(2025, 1, 1), date(2025, 12, 31))
        assert len(sessions_2024) == 252
        assert len(sessions_2025) == 250
        assert date(2024, 2, 29) in sessions_2024
        assert date(2025, 2, 28) in sessions_2025
        # Equity keeps its own session count across the leap year: 252 sessions
        # 364 days apart, so the rate is derived, not assumed to be 252.
        basis = basis_for_series(AAPL, sessions_2024)
        assert basis.elapsed_years == round(364 / MEAN, 6)
        assert basis.observations_per_year == round(252 * MEAN / 364, 6)


# ---------------------------------------------------------------------------
# Observation frequency per grain
# ---------------------------------------------------------------------------

# (instrument, interval, observations per year derived by hand)
US_COVERAGE_RATE = 752 * MEAN / 1095   # 752 US sessions over 2023-01-01..2025-12-31
HK_COVERAGE_RATE = 493 * MEAN / 730    # 493 HK sessions over 2024-01-01..2025-12-31
FREQUENCY_CASES = [
    (BTC, MINUTES_PER_DAY, MEAN),
    (BTC, 60, MEAN * 24),
    (BTC, 15, MEAN * 96),
    (BTC, 1, MEAN * 1440),
    (AAPL, MINUTES_PER_DAY, US_COVERAGE_RATE),
    (AAPL, 60, US_COVERAGE_RATE * (390 / 60)),
    (AAPL, 240, US_COVERAGE_RATE * (390 / 240)),
    (AAPL, 5, US_COVERAGE_RATE * (390 / 5)),
    (TENCENT, MINUTES_PER_DAY, HK_COVERAGE_RATE),
    (TENCENT, 60, HK_COVERAGE_RATE * (330 / 60)),
    (TENCENT, 5, HK_COVERAGE_RATE * (330 / 5)),
]


class TestObservationFrequency:
    @pytest.mark.parametrize(
        "instrument,interval,expected",
        FREQUENCY_CASES,
        ids=[f"{c[0].split(':')[-1]}-{c[1]}m" for c in FREQUENCY_CASES],
    )
    def test_rate_matches_the_hand_derivation(self, instrument, interval, expected):
        market = market_of(instrument)
        basis = basis_from_calendar(market, interval)
        assert basis.available is True
        assert basis.observations_per_year == pytest.approx(round(expected, 6), abs=1e-6)
        assert basis.observations_per_year != legacy_observations_per_year(interval)

    def test_crypto_hourly_is_not_252_times_24(self):
        basis = basis_from_calendar(Market.CRYPTO, 60)
        assert basis.observations_per_year == 365.25 * 24 == 8766.0
        assert basis.observations_per_year != legacy_observations_per_year(60) == 6048

    def test_us_hourly_counts_sessions_not_clock_hours(self):
        basis = basis_from_calendar(Market.US, 60)
        # 6.5 hourly bars fit in a 390-minute session, so a US hour is not a clock hour.
        assert basis.bars_per_trading_day == 6.5
        assert basis.observations_per_year == pytest.approx(
            round(752 * MEAN / 1095 * 6.5, 6), abs=1e-6
        )
        assert basis.observations_per_year != legacy_observations_per_year(60)

    def test_rate_is_the_product_of_sessions_and_bars(self):
        for market in (Market.US, Market.HK, Market.CRYPTO):
            for interval in (1, 5, 15, 30, 60, 240, MINUTES_PER_DAY):
                basis = basis_from_calendar(market, interval)
                # Each field is rounded to 6 decimals separately, so the identity is
                # checked relatively: on 81,400 observations a year the rounded
                # fields differ from the rounded product by ~1e-4 in absolute terms.
                assert basis.observations_per_year == pytest.approx(
                    basis.trading_days_per_year * basis.bars_per_trading_day, rel=1e-6
                )

    def test_frequency_names_the_grain(self):
        assert basis_from_calendar(Market.US, MINUTES_PER_DAY).frequency == "daily"
        assert basis_from_calendar(Market.US, 5).frequency == "intraday"
        assert annualization.frequency_name(10080) == "coarser-than-daily"
        assert annualization.frequency_name(0) == "unknown"
        assert annualization.frequency_name(None) == "unknown"

    def test_declared_rates_are_market_specific(self):
        rates = {
            m.value: basis_from_calendar(m, MINUTES_PER_DAY).observations_per_year
            for m in (Market.US, Market.HK, Market.CRYPTO)
        }
        assert len(set(rates.values())) == 3, rates
        assert rates["CRYPTO"] == MEAN
        # Averaged over each table's own declared coverage window.
        assert rates["US"] == pytest.approx(round(US_COVERAGE_RATE, 6), abs=1e-6)
        assert rates["HK"] == pytest.approx(round(HK_COVERAGE_RATE, 6), abs=1e-6)


# ---------------------------------------------------------------------------
# Refusals: missing information reports missing, never a guess and never 0.0
# ---------------------------------------------------------------------------

def _refusing_series_cases() -> List[Dict[str, Any]]:
    two_days = [date(2024, 1, 2), date(2024, 1, 3)]
    return [
        {
            "id": "unreadable stamp",
            "basis": basis_for_series(AAPL, [date(2024, 1, 2), "not-a-date"]),
            "names": "not a readable date",
        },
        {
            "id": "one observation",
            "basis": basis_for_series(AAPL, two_days[:1]),
            "names": "at least two dated observations",
        },
        {
            "id": "no observations",
            "basis": basis_for_series(AAPL, []),
            "names": "at least two dated observations",
        },
        {
            "id": "repeated session date",
            "basis": basis_for_series(AAPL, [date(2024, 1, 2), date(2024, 1, 2), date(2024, 3, 1)]),
            "names": "repeat a session date",
        },
        {
            "id": "zero-length interval",
            "basis": basis_for_series(AAPL, two_days, interval_minutes=0),
            "names": "not a positive bar length",
        },
        {
            "id": "negative interval",
            "basis": basis_for_series(AAPL, two_days, interval_minutes=-60),
            "names": "not a positive bar length",
        },
        {
            "id": "one-date sample",
            "basis": basis_for_series(
                AAPL,
                [datetime(2024, 3, 1, 15, 0, tzinfo=timezone.utc),
                 datetime(2024, 3, 1, 16, 0, tzinfo=timezone.utc)],
                interval_minutes=60,
            ),
            "names": "same date, so the sample has no elapsed length",
        },
    ]


@pytest.mark.parametrize("case", _refusing_series_cases(), ids=lambda c: c["id"])
class TestRefusalsCarryAReason:
    def test_basis_is_unavailable(self, case):
        basis: Basis = case["basis"]
        assert basis.available is False
        assert basis.observations_per_year is None
        assert basis.reason and case["names"] in basis.reason

    def test_sharpe_rate_is_none_not_252(self, case):
        assert observations_per_year(case["basis"]) is None

    def test_annualized_metrics_are_refused_structs(self, case):
        for metric in (
            annualized_return_pct(12.5, case["basis"]),
            annualized_volatility_pct(0.02, case["basis"]),
        ):
            assert isinstance(metric, dict)
            assert metric["value"] is None
            assert "Annualization refused:" in metric["reason"]
            assert METHOD_VERSION in metric["reason"]
            assert metric["annualization"]["available"] is False

    def test_a_zero_return_is_not_annualized_as_zero(self, case):
        # A flat sample is not evidence of a flat year: the metric stays missing.
        assert annualized_return_pct(0.0, case["basis"])["value"] is None


class TestCalendarOnlyRefusals:
    def test_missing_market(self):
        basis = basis_from_calendar(None, MINUTES_PER_DAY)
        assert basis.available is False
        assert "no market could be resolved" in basis.reason

    def test_missing_interval(self):
        basis = basis_from_calendar(Market.CRYPTO, None)
        assert basis.available is False
        assert "not a positive bar length" in basis.reason

    def test_market_declared_without_a_calendar(self):
        # Market.FOREX is in the enum; instruments.py describes no sessions for it.
        basis = basis_from_calendar(Market.FOREX, MINUTES_PER_DAY)
        assert basis.available is False
        assert "declares no market structure" in basis.reason
        assert CALENDAR_SOURCE in basis.reason

    def test_coarser_than_daily_is_refused_not_scaled(self):
        # A weekly bar is ~52 observations a year in any market; deriving it from
        # sessions would report 250.8/7 = 35.8, which is the same class of error as
        # the constant this module replaced.
        for interval in (10080, 43200):
            basis = basis_from_calendar(Market.US, interval)
            assert basis.available is False
            assert basis.frequency == "coarser-than-daily"
            assert "no session-based observations-per-year applies" in basis.reason
            assert basis.observations_per_year is None

    def test_series_path_refuses_the_coarse_grain_too(self):
        basis = basis_for_series(BTC, _btc_rows(), interval_minutes=10080)
        assert basis.available is False
        assert annualized_volatility_pct(0.03, basis)["value"] is None

    def test_unresolvable_market_falls_back_to_measured(self):
        basis = basis_for_series(DEMO_ASSET, _btc_rows())
        assert basis.available is True
        assert basis.frequency_source == "measured"
        assert basis.inside_declared_coverage is False
        assert basis.market is None
        # 720 rows over 719 days, measured from the series itself.
        assert basis.observations_per_year == pytest.approx(
            round(720 / (719 / MEAN), 6), abs=1e-4
        )
        assert "no resolvable market" in basis.reason

    def test_declared_market_without_calendar_also_measures(self):
        basis = basis_for_series(EUR_USD, _btc_rows())
        assert basis.frequency_source == "measured"
        assert "FOREX declares no market structure" in basis.reason

    def test_window_outside_declared_coverage_is_flagged_as_assumption(self):
        # US holiday coverage stops at 2025-12-31; a 2026 window still annualizes,
        # but says it only removed weekends.
        weekdays = [d for d in consecutive_days(date(2026, 1, 1), 400) if d.weekday() < 5]
        basis = basis_for_series(AAPL, weekdays)
        assert basis.available is True
        assert basis.inside_declared_coverage is False
        assert "removes weekends only" in basis.reason
        assert "not a fact about the exchange" in basis.reason


class TestMeasuredBasis:
    def test_intraday_cadence_keeps_its_fraction_of_a_day(self):
        # 60 four-hour bars span 9 and 2/3 days; truncating that to 9 whole days
        # would measure the cadence 9% too fast.
        stamps = [
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i)
            for i in range(60)
        ]
        basis = measured_basis(stamps, interval_minutes=240)
        assert basis.observations_per_year == pytest.approx(
            round(60 / ((59 * 4 / 24) / MEAN), 6), abs=1e-4
        )
        assert basis.elapsed_years == pytest.approx(round((59 / 6) / MEAN, 6), abs=1e-5)

    def test_dates_still_measure_in_whole_days(self):
        basis = measured_basis(consecutive_days(date(2024, 1, 1), 100))
        assert basis.elapsed_years == round(99 / MEAN, 6)
        assert basis.observations_per_year == round(100 / (99 / MEAN), 6)

    def test_iso_strings_measure(self):
        stamps = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
        assert measured_basis([s.isoformat() for s in stamps]).as_dict() == measured_basis(
            stamps
        ).as_dict()

    def test_same_instant_refuses(self):
        stamp = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        basis = measured_basis([stamp, stamp])
        assert basis.available is False
        assert "same timestamp" in basis.reason

    def test_two_instants_on_one_date_are_measurable(self):
        basis = measured_basis(
            [datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
             datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)]
        )
        # 09:30 -> 16:00 is 6.5 hours, not zero days: the cadence is measured from
        # the instants, so a same-date intraday pair yields a rate instead of a refusal.
        assert basis.available is True
        expected_years = 6.5 / 24.0 / MEAN
        assert basis.elapsed_years == round(expected_years, 6)
        assert basis.observations_per_year == round(2 / expected_years, 6)

    def test_unreadable_stamp_refuses(self):
        basis = measured_basis([date(2024, 1, 1), "yesterday"])
        assert basis.available is False
        assert "not readable" in basis.reason


# ---------------------------------------------------------------------------
# Contract fields and the sample gate
# ---------------------------------------------------------------------------

class TestContractRecordKeeping:
    def test_method_version_is_pinned(self):
        assert METHOD_VERSION == "annualization-2026-11-10.1"
        assert annualization.METHOD_VERSION == Basis(available=True).method_version

    def test_every_basis_records_the_version_and_source(self):
        for basis in (
            basis_for_series(BTC, _btc_rows()),
            basis_from_calendar(Market.US, 60),
            measured_basis(consecutive_days(date(2024, 1, 1), 10)),
            unavailable("a refusal"),
        ):
            assert basis.method_version == METHOD_VERSION
            assert basis.calendar_source == CALENDAR_SOURCE

    def test_dict_round_trips_the_dataclass_and_serializes(self):
        record = basis_for_series(BTC, _btc_rows()).as_dict()
        assert set(record) == {f.name for f in dataclasses.fields(Basis)}
        assert json.loads(json.dumps(record)) == record

    def test_sample_gate_is_a_year_less_the_session_edge(self):
        # A calendar year of US sessions spans 0.9966 years, so a gate at exactly
        # 1.0 would refuse it for a boundary artifact rather than a short sample.
        assert MIN_YEARS_FOR_ANNUALIZATION == 0.95
        assert 364 / MEAN >= MIN_YEARS_FOR_ANNUALIZATION

    def test_sample_below_the_gate_is_refused(self):
        basis = Basis(available=True, elapsed_years=0.94, observation_count=240)
        result = annualized_return_pct(3.5, basis)
        assert result["value"] is None
        assert "below the 0.95-year minimum" in result["reason"]

    def test_sample_at_or_above_the_gate_is_annualized(self):
        basis = Basis(
            available=True, elapsed_years=0.96, observation_count=240, observations_per_year=240.0
        )
        assert annualized_return_pct(3.5, basis) == pytest.approx(
            ((1.035) ** (1 / 0.96) - 1) * 100, abs=1e-4
        )
        assert annualized_volatility_pct(0.01, basis) == pytest.approx(
            0.01 * math.sqrt(240) * 100, abs=1e-4
        )

    def test_volatility_gate_names_the_sample_it_refused(self):
        basis = basis_for_series(AAPL, us_sessions(date(2024, 1, 1), date(2024, 6, 30)))
        result = annualized_volatility_pct(0.01, basis)
        assert result["value"] is None
        assert "0.4873 years" in result["reason"]
        assert "124 observations" in result["reason"]

    def test_total_loss_has_no_annualized_return(self):
        basis = basis_for_series(BTC, _btc_rows())
        result = annualized_return_pct(-100.0, basis)
        assert result["value"] is None
        assert "compounding base is zero" in result["reason"]

    def test_flat_equity_reports_insufficient_variation(self):
        basis = basis_for_series(BTC, _btc_rows())
        result = annualized_volatility_pct(0.0, basis)
        assert result["value"] is None
        assert result["reason"] == "Insufficient return variation"

    def test_calendar_basis_without_dates_refuses_a_return(self):
        # basis_from_calendar knows the cadence but no window, so it cannot state
        # how long a year of that cadence ran.
        result = annualized_return_pct(10.0, basis_from_calendar(Market.US, 60))
        assert result["value"] is None
        assert "but no observation dates" in result["reason"]

    @pytest.mark.parametrize("text", [
        basis_for_series(AAPL, []).reason,
        basis_from_calendar(Market.FOREX, 1440).reason,
        basis_from_calendar(Market.US, 10080).reason,
        measured_basis([date(2024, 1, 1)]).reason,
        basis_for_series(EUR_USD, _btc_rows()).reason,
    ])
    def test_reasons_are_causes_without_delegated_none(self, text):
        assert text
        assert "None" not in text
        assert not text.startswith("Annualization refused")
        assert not text.endswith(".")  # the refusal wrapper terminates the sentence


# ---------------------------------------------------------------------------
# compute_metrics: the contract at the metric boundary
# ---------------------------------------------------------------------------

def _metrics_kwargs(**overrides) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "initial_cash": 10000.0,
        "final_equity": 20000.0,
        "transactions": [],
        "daily_equity": [10000.0],
        "benchmark_equity": None,
        "trading_days": 720,
        "attribution": None,
        "trading_dates": None,
    }
    kwargs.update(overrides)
    return kwargs


class TestComputeMetrics:
    def test_no_basis_means_no_annualized_number(self):
        metrics = compute_metrics(**_metrics_kwargs())
        assert metrics["trading_days"] == 720
        assert metrics["annualization"]["available"] is False
        assert metrics["annualization"]["method_version"] == METHOD_VERSION
        assert "no annualization basis" in metrics["annualization"]["reason"]
        for key in ("annualized_return_pct", "annualized_volatility_pct"):
            assert metrics[key]["value"] is None
            assert "Annualization refused:" in metrics[key]["reason"]

    def test_total_return_survives_without_a_basis(self):
        # The sample's own return needs no annualization contract; only the per-year
        # figures go missing.
        metrics = compute_metrics(**_metrics_kwargs())
        assert metrics["total_return_pct"] == 100.0

    def test_crypto_basis_annualizes_the_run_it_describes(self):
        basis = basis_for_series(BTC, _btc_rows())
        metrics = compute_metrics(
            **_metrics_kwargs(daily_equity=[10000.0, 11000.0, 9900.0], basis=basis)
        )
        assert metrics["annualization"]["observations_per_year"] == MEAN
        assert metrics["annualized_return_pct"] == pytest.approx(42.2075, abs=1e-4)
        # 0.1 and -0.1 equity returns: sample standard deviation sqrt(0.02).
        assert metrics["annualized_volatility_pct"] == pytest.approx(
            math.sqrt(0.02) * math.sqrt(365.25) * 100, abs=1e-4
        )

    def test_two_point_equity_has_no_sign_of_variation(self):
        basis = basis_for_series(BTC, _btc_rows())
        metrics = compute_metrics(
            **_metrics_kwargs(daily_equity=[10000.0, 20000.0], basis=basis)
        )
        assert metrics["annualized_volatility_pct"]["value"] is None
        assert metrics["annualized_volatility_pct"]["reason"] == "Insufficient return variation"
        # The return still annualizes: one observation-to-observation return is a
        # complete return, even though it is no evidence of a standard deviation.
        assert metrics["annualized_return_pct"] == pytest.approx(42.2075, abs=1e-4)


# ---------------------------------------------------------------------------
# The batch path: a run records the basis it used
# ---------------------------------------------------------------------------

def _demo_spec(**overrides) -> Dict[str, Any]:
    spec = {
        "schema_version": "1.0",
        "template_type": "buy_and_hold",
        "asset_ids": [DEMO_ASSET],
        "frequency": "1d",
        "start_date": "2024-01-02",
        "end_date": "2024-02-14",
        "initial_cash": 10000,
        "signal_parameters": {},
        "entry_rule": {},
        "exit_rule": {},
        "sizing_rule": {},
        "execution_timing": {"execute_at": "next_open"},
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "benchmark_id": "DEMO-BENCHMARK",
        "currency": "USD",
    }
    spec.update(overrides)
    return spec


class TestRunBacktestRecordsItsBasis:
    def test_run_metrics_carry_the_annualization_block(self):
        result = run_backtest(_demo_spec())
        assert result["run_status"] == "completed"
        record = result["metrics"]["annualization"]
        assert record["method_version"] == METHOD_VERSION
        assert record["frequency"] == "daily"
        # A demo asset has no market prefix, so its cadence is measured from its own
        # observation dates rather than claimed from a calendar.
        assert record["frequency_source"] == "measured"
        assert record["inside_declared_coverage"] is False
        assert record["observation_count"] == result["metrics"]["trading_days"]

    def test_measured_rate_follows_the_dataset_it_priced_against(self):
        result = run_backtest(_demo_spec())
        frame = get_demo_ohlcv([DEMO_ASSET], "2024-01-02", "2024-02-14")[DEMO_ASSET]
        span_days = (frame["date"].iloc[-1] - frame["date"].iloc[0]).days
        assert len(frame) == result["metrics"]["annualization"]["observation_count"]
        assert result["metrics"]["annualization"]["observations_per_year"] == pytest.approx(
            len(frame) / (span_days / MEAN), abs=1e-4
        )

    def test_short_run_warns_instead_of_annualizing(self):
        result = run_backtest(_demo_spec())
        assert result["metrics"]["annualized_return_pct"]["value"] is None
        assert result["metrics"]["annualized_volatility_pct"]["value"] is None
        warning = next(w for w in result["warnings"] if "year minimum" in w)
        assert METHOD_VERSION in warning
        assert "not displayed" in warning

    def test_methodology_summary_publishes_the_rule(self):
        result = run_backtest(_demo_spec())
        block = result["methodology_summary"]["annualization"]
        assert block["method_version"] == METHOD_VERSION
        assert "sqrt(observations per year)" in block["rule"]
        assert CALENDAR_SOURCE in block["rule"]
        assert "0.95" in block["sample_gate"]
        assert "metrics.annualization" in block["basis"]

    def test_two_runs_of_the_same_spec_record_the_same_basis(self):
        first = run_backtest(_demo_spec())["metrics"]["annualization"]
        second = run_backtest(_demo_spec())["metrics"]["annualization"]
        assert first == second


# ---------------------------------------------------------------------------
# Journal attribution: pooled trades have to share a frequency to annualize
# ---------------------------------------------------------------------------

def _trade(instrument: str, interval: int, pnl: str) -> Dict[str, Any]:
    return {"instrument_id": instrument, "interval_minutes": interval, "net_pnl": pnl}


# mean 10, population standard deviation sqrt(((0+100+225+25)/4)) = sqrt(87.5).
GROUP_PNLS = ("10", "20", "-5", "15")


class TestJournalGroupBasis:
    def test_single_market_and_grain_uses_its_calendar(self):
        trades = [_trade(BTC, MINUTES_PER_DAY, p) for p in GROUP_PNLS]
        basis = _group_basis(trades)
        assert basis.observations_per_year == MEAN
        assert basis.market == "CRYPTO"

    def test_sharpe_scales_on_the_groups_own_cadence(self):
        trades = [_trade(BTC, MINUTES_PER_DAY, p) for p in GROUP_PNLS]
        metrics = _compute_group_metrics(trades)
        # mean 10, population standard deviation sqrt(87.5), times sqrt(365.25).
        expected = 10 / math.sqrt(87.5) * math.sqrt(365.25)
        assert _num(metrics["sharpe_ratio"]) == pytest.approx(expected, abs=1e-4)
        assert _num(metrics["sharpe_ratio"]) == pytest.approx(20.4311, abs=1e-4)
        assert metrics["annualization"]["observations_per_year"] == MEAN

    def test_hourly_group_scales_differently_than_daily(self):
        trades = [_trade(BTC, 60, p) for p in GROUP_PNLS]
        metrics = _compute_group_metrics(trades)
        assert metrics["annualization"]["observations_per_year"] == 8766.0
        assert _num(metrics["sharpe_ratio"]) == pytest.approx(
            10 / math.sqrt(87.5) * math.sqrt(8766.0), abs=1e-4
        )

    def test_mixed_markets_have_no_single_frequency(self):
        trades = [
            _trade(BTC, MINUTES_PER_DAY, "10"),
            _trade(AAPL, MINUTES_PER_DAY, "20"),
        ]
        metrics = _compute_group_metrics(trades)
        assert metrics["annualization"]["available"] is False
        assert "pools 2 markets" in metrics["annualization"]["reason"]
        assert metrics["sharpe_ratio"] is None

    def test_mixed_grains_have_no_single_frequency(self):
        trades = [
            _trade(BTC, MINUTES_PER_DAY, "10"),
            _trade(BTC, 60, "20"),
        ]
        metrics = _compute_group_metrics(trades)
        assert metrics["annualization"]["available"] is False
        assert "pools 2 bar grains" in metrics["annualization"]["reason"]
        assert metrics["sharpe_ratio"] is None

    def test_unmarketable_instrument_reports_unavailable(self):
        trades = [_trade(DEMO_ASSET, MINUTES_PER_DAY, p) for p in GROUP_PNLS]
        metrics = _compute_group_metrics(trades)
        assert metrics["annualization"]["available"] is False
        assert "no market could be resolved" in metrics["annualization"]["reason"]
        assert metrics["sharpe_ratio"] is None

    def test_empty_group_records_why(self):
        metrics = _compute_group_metrics([])
        assert metrics["annualization"]["available"] is False
        assert "no trades" in metrics["annualization"]["reason"]
        assert metrics["sharpe_ratio"] is None

    @pytest.mark.parametrize("rate", [None, 0, -252])
    def test_sharpe_refuses_without_a_rate(self, rate):
        pnls = [Decimal(p) for p in GROUP_PNLS]
        assert _sharpe_ratio(pnls, rate) is None

    def test_trades_per_year_helper_is_gone(self):
        # The constant-returning helper this contract replaced must not come back.
        import app.services.skill_attribution as skill_attribution

        assert not hasattr(skill_attribution, "_trades_per_year")


# ---------------------------------------------------------------------------
# The research lane: HV annualizes on the cadence it was handed
# ---------------------------------------------------------------------------

def _lab_bars(closes: List[float], step_minutes: int) -> List[LabBar]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        LabBar(
            open_time=base + timedelta(minutes=step_minutes * i),
            interval_minutes=step_minutes,
            open=Decimal(str(close)),
            high=Decimal(str(close + 1)),
            low=Decimal(str(close - 1)),
            close=Decimal(str(close)),
            volume=Decimal("1"),
        )
        for i, close in enumerate(closes)
    ]


class TestResearchIndicators:
    CLOSES = [100 + (i % 7) - 3 for i in range(60)]

    def _pair(self, step: int):
        indicators = prepare_indicators(_lab_bars(self.CLOSES, step))
        return indicators, step

    def test_hv_basis_is_measured_from_the_bars_it_received(self):
        for step in (MINUTES_PER_DAY, 240, 60):
            indicators, _ = self._pair(step)
            record = indicators.hv_basis
            assert record["frequency_source"] == "measured"
            assert record["method_version"] == METHOD_VERSION
            # 60 bars arriving every `step` minutes span 59*step/1440 days.
            assert record["observations_per_year"] == pytest.approx(
                60 / ((59 * step / MINUTES_PER_DAY) / MEAN), abs=1e-4
            )
            # hv_factor is Decimal while the basis record carries floats.
            assert float(indicators.hv_factor) == pytest.approx(
                math.sqrt(record["observations_per_year"]), rel=1e-12
            )

    def test_annualized_hv_differs_by_grain(self):
        daily, _ = self._pair(MINUTES_PER_DAY)
        hourly, _ = self._pair(60)
        assert hourly.hv[25] > daily.hv[25]

    def test_unannualized_hv_is_grain_invariant(self):
        # What the volatility-ratio signal compares against ATR% is the per-bar
        # figure: the same closes must give the same per-bar volatility whatever
        # cadence they arrived on, because the factor divides back out.
        daily, _ = self._pair(MINUTES_PER_DAY)
        four_hour, _ = self._pair(240)
        assert float(daily.hv[25]) / float(daily.hv_factor) == pytest.approx(
            float(four_hour.hv[25]) / float(four_hour.hv_factor), rel=1e-12
        )

    def test_hv_is_unavailable_when_the_cadence_cannot_be_measured(self):
        # Two bars stamped at the same instant describe no cadence at all, so there
        # is no factor to annualize with and hv stays empty rather than assuming one.
        same_instant = _lab_bars([100.0, 101.0], MINUTES_PER_DAY)
        same_instant[1] = dataclasses.replace(
            same_instant[1], open_time=same_instant[0].open_time
        )
        indicators = prepare_indicators(same_instant)
        assert indicators.hv_factor is None
        assert indicators.hv_basis["available"] is False
        assert "same timestamp" in indicators.hv_basis["reason"]
        assert all(value is None for value in indicators.hv)


# ---------------------------------------------------------------------------
# The API surfaces that carry these structs
# ---------------------------------------------------------------------------

class TestStructsSurviveTheApi:
    """A refusal is only a refusal if it reaches the caller as one."""

    @staticmethod
    def _ad_hoc_payload(metrics: Dict[str, Any]) -> Dict[str, Any]:
        response = AdHocBacktestResponse(
            status="completed",
            experiment_id=str(uuid4()),
            version_id=str(uuid4()),
            metrics=metrics,
            computed_at="2026-11-10T00:00:00+00:00",
            completed_at="2026-11-10T00:00:00+00:00",
            durable_run=AdHocDurableRunHint(
                path="/api/v1/versions/{version_id}/runs",
                note="creates a stored, citable run",
            ),
        )
        return json.loads(response.model_dump_json())

    def test_ad_hoc_backtest_returns_the_block_and_the_refusals(self):
        payload = self._ad_hoc_payload(run_backtest(_demo_spec())["metrics"])
        block = payload["metrics"]["annualization"]
        assert block["method_version"] == METHOD_VERSION
        assert block["frequency_source"] == "measured"
        assert block["calendar_source"] == CALENDAR_SOURCE
        refusal = payload["metrics"]["annualized_return_pct"]
        assert refusal["value"] is None
        assert f"{MIN_YEARS_FOR_ANNUALIZATION}-year minimum" in refusal["reason"]
        assert refusal["annualization"]["method_version"] == METHOD_VERSION

    def test_persisted_run_response_round_trips_the_block(self):
        metrics = run_backtest(_demo_spec())["metrics"]
        response = ResearchRunResponse(
            id=uuid4(),
            experiment_version_id=uuid4(),
            dataset_snapshot_id=None,
            dataset_checksum="0" * 64,
            engine_version="1.1.0",
            config_hash="deadbeef",
            status="completed",
            metrics=metrics,
            warnings=[],
            methodology_json=None,
            result_json=None,
            error_details=None,
            started_at=None,
            completed_at=None,
        )
        restored = json.loads(response.model_dump_json())["metrics"]
        assert restored["annualization"] == metrics["annualization"]
        assert restored["annualized_volatility_pct"]["value"] is None

    def test_comparison_fields_stay_numeric(self):
        # The compare screen subtracts value_a from value_b, so a struct listed
        # there would render as an object. The basis travels with the run, not
        # with the diff, which is why it is not in this field list.
        metrics = run_backtest(_demo_spec())["metrics"]
        for field in METRICS_FIELDS:
            assert not isinstance(metrics.get(field), dict)
        assert "annualization" not in METRICS_FIELDS
