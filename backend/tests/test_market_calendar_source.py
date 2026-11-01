"""One exchange calendar, and the counts that follow from it.

These tests discriminate between the two calendars that used to exist: the
public ``MarketStructure.holidays`` table (which shipped empty) and a private
copy inside the synthetic generator. They also pin the boundary behaviour that
keeps an unverified date from reading as a closure.
"""

from datetime import date
from pathlib import Path

from app.data import real_synthetic_data
from app.providers.instruments import (
    HK_MARKET_HOLIDAYS,
    HK_MARKET_STRUCTURE,
    US_MARKET_HOLIDAYS,
    US_MARKET_STRUCTURE,
    Market,
    trading_days_between,
)


class TestTradingDayStatus:
    def test_new_year_is_a_closure(self):
        # The private calendar used to miss this one, which is why the same
        # 2024 window was 134 bars there and 140 in the raw weekday fixture.
        assert US_MARKET_STRUCTURE.trading_day_status(date(2024, 1, 1)) == "holiday"
        assert US_MARKET_STRUCTURE.trading_day_status(date(2025, 1, 1)) == "holiday"

    def test_observed_new_year_when_the_holiday_falls_on_a_weekend(self):
        assert US_MARKET_STRUCTURE.trading_day_status(date(2023, 1, 2)) == "holiday"

    def test_listed_2023_closures_are_present(self):
        for day in (date(2023, 4, 7), date(2023, 11, 23), date(2023, 12, 25)):
            assert US_MARKET_STRUCTURE.trading_day_status(day) == "holiday"

    def test_national_day_of_mourning_2025(self):
        assert US_MARKET_STRUCTURE.trading_day_status(date(2025, 1, 9)) == "holiday"

    def test_early_close_days_are_sessions_not_closures(self):
        # Thanksgiving Friday and the day after Independence Day are shortened
        # sessions. Treating them as holidays drops real bars.
        for day in (date(2024, 11, 29), date(2025, 11, 28), date(2024, 7, 5)):
            assert US_MARKET_STRUCTURE.trading_day_status(day) == "open"

    def test_weekend_is_reported_before_coverage(self):
        assert US_MARKET_STRUCTURE.trading_day_status(date(2026, 11, 1)) == "weekend"

    def test_dates_outside_coverage_are_unknown_not_closed(self):
        # 2022-12-30 is a Friday before the table starts; 2026-10-01 is a
        # Thursday after it ends. Neither is evidence of a closure.
        assert US_MARKET_STRUCTURE.trading_day_status(date(2022, 12, 30)) == (
            "calendar_unknown"
        )
        assert US_MARKET_STRUCTURE.trading_day_status(date(2026, 10, 1)) == (
            "calendar_unknown"
        )
        # is_trading_day keeps its documented contract: unknown reads as a
        # weekday, and trading_day_status is the honest query.
        assert US_MARKET_STRUCTURE.is_trading_day(date(2026, 10, 1)) is True

    def test_coverage_boundaries_are_inclusive(self):
        assert US_MARKET_STRUCTURE.calendar_covers(date(2023, 1, 1)) is True
        assert US_MARKET_STRUCTURE.calendar_covers(date(2025, 12, 31)) is True
        assert US_MARKET_STRUCTURE.calendar_covers(date(2022, 12, 31)) is False
        assert US_MARKET_STRUCTURE.calendar_covers(date(2026, 1, 1)) is False


class TestTradingDaysBetween:
    def test_both_ends_are_inclusive_and_closures_are_skipped(self):
        days = trading_days_between(Market.US, date(2024, 1, 1), date(2024, 1, 5))
        assert days == [
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
            date(2024, 1, 5),
        ]

    def test_single_open_day(self):
        assert trading_days_between(Market.US, date(2024, 1, 2), date(2024, 1, 2)) == [
            date(2024, 1, 2)
        ]

    def test_unverified_weekdays_are_kept_but_weekends_are_not(self):
        # 2026-10-01 is a Thursday after the table ends: it stays in the list
        # because dropping it would assert a closure the calendar cannot prove.
        days = trading_days_between(Market.US, date(2026, 10, 1), date(2026, 10, 4))
        assert days == [date(2026, 10, 1), date(2026, 10, 2)]


class TestGeneratorReadsTheSharedCalendar:
    def test_2024_window_has_133_sessions(self):
        bars = real_synthetic_data.get_synthetic_bars(
            "US:AAPL", "2024-01-01", "2024-07-12"
        )
        assert len(bars) == 133
        assert bars[0]["date"] == "2024-01-02"
        assert bars[-1]["date"] == "2024-07-12"

    def test_full_2024_is_still_252_sessions(self):
        bars = real_synthetic_data.get_synthetic_bars(
            "US:AAPL", "2024-01-01", "2024-12-31"
        )
        assert len(bars) == 252

    def test_generator_skips_holidays_and_keeps_early_closes(self):
        dates = [
            bar["date"]
            for bar in real_synthetic_data.get_synthetic_bars(
                "US:AAPL", "2024-11-27", "2024-11-30"
            )
        ]
        assert dates == ["2024-11-27", "2024-11-29"]

    def test_universe_count_follows_the_shared_calendar(self):
        assert len(trading_days_between(Market.US, date(2023, 1, 3), date(2025, 12, 31))) == 752
        assert len(real_synthetic_data.get_synthetic_bars(
            "US:AAPL", "2023-01-01", "2025-12-31"
        )) == 752

    def test_hk_generator_uses_the_hk_table(self):
        assert HK_MARKET_STRUCTURE.holidays == HK_MARKET_HOLIDAYS
        assert HK_MARKET_STRUCTURE.trading_day_status(date(2024, 1, 1)) == "holiday"
        assert HK_MARKET_STRUCTURE.trading_day_status(date(2024, 12, 25)) == "holiday"
        # The day after HKSAR Establishment Day is an ordinary session.
        assert HK_MARKET_STRUCTURE.trading_day_status(date(2024, 7, 2)) == "open"
        bars = real_synthetic_data.get_synthetic_bars(
            "HK:0700", "2024-01-01", "2024-01-05"
        )
        assert [bar["date"] for bar in bars] == [
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"
        ]

    def test_generator_holds_no_private_calendar(self):
        source = Path(real_synthetic_data.__file__).read_text()
        assert "HOLIDAYS" not in source


class TestNoSecondCalendar:
    def test_only_the_instruments_module_defines_holiday_tables(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in sorted(app_root.rglob("*.py")):
            text = path.read_text()
            for table in ("US_MARKET_HOLIDAYS", "HK_MARKET_HOLIDAYS"):
                if f"\n{table}:" in text or f"\n{table} =" in text:
                    if path.name != "instruments.py":
                        offenders.append(f"{path.relative_to(app_root)}:{table}")
        assert offenders == []

    def test_public_tables_match_what_the_structures_expose(self):
        assert US_MARKET_STRUCTURE.holidays == US_MARKET_HOLIDAYS
        # The US table lists weekday closures only; a weekend date there would
        # never be a session anyway and would hide a miscategorised holiday.
        assert all(day.weekday() < 5 for day in US_MARKET_HOLIDAYS)
