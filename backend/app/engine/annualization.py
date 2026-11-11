"""The annualization contract: what one observation is, and how many fit in a year.

Metrics that carry a "per year" meaning need two numbers that the engine used to
guess from one constant (252):

* the **elapsed length** of the sample, which turns a total return into an annual
  return; and
* the **observation frequency** of the series, which scales a per-period
  standard deviation to a per-year one.

Both come from the single exchange-calendar source in
``app/providers/instruments.py`` plus the series' own observation dates, so a US
daily bar and a crypto daily bar annualize differently instead of both inheriting
an equity-trading-day constant. Nothing here keeps its own copy of a holiday
table, and nothing here falls back to 252 when the information is missing: an
unknown market, an unreadable date or a series with two grains in it yields an
unavailable basis with a reason, and the caller reports the metric as
unavailable rather than inventing a scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.providers.instruments import Market, MarketStructure, get_market_structure, trading_days_between

# Bumped whenever the meaning of an annualized number changes, so a stored run can
# be read back under the method that produced it.
METHOD_VERSION = "annualization-2026-11-10.1"

MEAN_CALENDAR_DAYS_PER_YEAR = 365.25
MINUTES_PER_DAY = 1440

# The old rule was "at least 252 bars", which annualized 252 five-minute bars as a
# year and refused a 300-day crypto series that is not one. What the gate protects
# is short-sample exaggeration, so it measures the sample in years.
#
# The threshold is 0.95 years rather than a nominal 1.0 because the sample is
# measured between its first and last observation, and a calendar year of session
# data never spans a whole one: US sessions run 2024-01-02 to 2024-12-31, which is
# 0.9966 years, and a year whose last sessions fall after the Christmas closures is
# shorter still. Refusing those would hide the metric over a boundary artifact
# instead of a short sample, while 252 hourly bars (about ten dates) and a
# six-month daily sample (0.5 years) stay refused.
MIN_YEARS_FOR_ANNUALIZATION = 0.95

CALENDAR_SOURCE = "app/providers/instruments.py"


@dataclass(frozen=True)
class Basis:
    """How one series is annualized, and whether it can be at all.

    Attributes:
        available: False when the frequency or the window is unknowable; every
            annualized metric derived from this basis is then reported as
            unavailable instead of being computed on a guessed scale.
        frequency: "daily", "intraday" or "coarser-than-daily" for the declared
            bar interval, "unknown" when there is nothing to derive from.
        market: Market the series belongs to, None when it could not be resolved.
        interval_minutes: Minutes per bar as declared by the caller. Daily and
            sub-daily grains annualize from the session calendar; a bar longer
            than a day is refused rather than scaled.
        observations_per_year: The scale a per-observation standard deviation is
            multiplied by ``sqrt()`` of. None when unavailable.
        trading_days_per_year: Sessions per year for this market over the measured
            window (365.25 calendar days for a continuously quoted venue).
        bars_per_trading_day: Observations inside one trading day.
        session_minutes_per_day: Quotable minutes per trading day.
        elapsed_years: Years between the first and last observation.
        first_observation: ISO stamp of the earliest observation the basis was
            derived from — a plain date for a daily-or-coarser grain, a full
            timestamp below that. None when no dated series was supplied.
        last_observation: ISO stamp of the latest observation, same convention.
        observation_count: Rows the series actually carries.
        expected_observations: Sessions this window holds times the bars one
            session carries — the rows the calendar says the series should hold,
            so a gapless series reads 100% coverage and missing rows pull it down.
        coverage_pct: observation_count / expected_observations, when both exist.
        calendar_source: Where the calendar came from.
        inside_declared_coverage: True when the holiday calendar declares coverage
            for the whole window. Declared coverage is not independent
            verification of every listed closure — the HK dates in
            ``instruments.py`` are published but unreverified — it only says no
            part of the window fell outside the table the engine is willing to
            count sessions from.
        frequency_source: "calendar" when the market resolved the cadence,
            "measured" when only the series' own timestamps could.
        reason: Why the basis is unavailable, verbatim in the metric struct.
    """

    available: bool
    method_version: str = METHOD_VERSION
    frequency: str = "unknown"
    market: Optional[str] = None
    interval_minutes: Optional[int] = None
    observations_per_year: Optional[float] = None
    trading_days_per_year: Optional[float] = None
    bars_per_trading_day: Optional[float] = None
    session_minutes_per_day: Optional[float] = None
    elapsed_years: Optional[float] = None
    first_observation: Optional[str] = None
    last_observation: Optional[str] = None
    observation_count: Optional[int] = None
    expected_observations: Optional[float] = None
    coverage_pct: Optional[float] = None
    calendar_source: str = CALENDAR_SOURCE
    inside_declared_coverage: bool = False
    frequency_source: str = "unknown"
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "method_version": self.method_version,
            "frequency": self.frequency,
            "market": self.market,
            "interval_minutes": self.interval_minutes,
            "observations_per_year": self.observations_per_year,
            "trading_days_per_year": self.trading_days_per_year,
            "bars_per_trading_day": self.bars_per_trading_day,
            "session_minutes_per_day": self.session_minutes_per_day,
            "elapsed_years": self.elapsed_years,
            "first_observation": self.first_observation,
            "last_observation": self.last_observation,
            "observation_count": self.observation_count,
            "expected_observations": self.expected_observations,
            "coverage_pct": self.coverage_pct,
            "calendar_source": self.calendar_source,
            "inside_declared_coverage": self.inside_declared_coverage,
            "frequency_source": self.frequency_source,
            "reason": self.reason,
        }


def unavailable(reason: str, **partial: Any) -> Basis:
    """A basis that refuses to be computed, carrying whatever was knowable."""
    return Basis(available=False, reason=reason, **partial)


def _date_of(value: Any) -> Optional[date]:
    """Coerce one observation stamp to a calendar date, or None if unreadable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for parser in (date.fromisoformat, datetime.fromisoformat):
        try:
            parsed = parser(text)
        except ValueError:
            continue
        return parsed.date() if isinstance(parsed, datetime) else parsed
    return None


def market_of(instrument_id: Optional[str]) -> Optional[Market]:
    """The market an instrument id belongs to, or None when it does not resolve.

    Crypto ids carry the venue as a third part (CRYPTO:KRAKEN:BTC-USD); the market
    is still the first part, so one split answers both shapes.
    """
    if not instrument_id or ":" not in str(instrument_id):
        return None
    try:
        return Market(str(instrument_id).split(":", 1)[0])
    except ValueError:
        return None


def structure_of(market: Optional[Market]) -> Optional[MarketStructure]:
    """The market's structure, or None when ``instruments.py`` declares none.

    ``Market`` has members the structure table does not describe (FOREX is in the
    enum with no entry), and ``get_market_structure`` raises for those. A market
    with no calendar here is a market whose cadence is unknown, which the callers
    report as unavailable or measured instead of letting the exception reach a
    metrics or journal response.
    """
    if market is None:
        return None
    try:
        return get_market_structure(market)
    except ValueError:
        return None


def regular_session_minutes(market: Market) -> float:
    """Quotable minutes inside one session for this market.

    Only ``is_regular`` sessions count. A continuously quoted venue has no
    session break, so its day is the full 1440 minutes.
    """
    structure = get_market_structure(market)
    if structure.always_open:
        return float(MINUTES_PER_DAY)
    minutes = 0.0
    for session in structure.sessions:
        if not session.is_regular:
            continue
        minutes += (
            datetime.combine(date(2000, 1, 1), session.end)
            - datetime.combine(date(2000, 1, 1), session.start)
        ).total_seconds() / 60.0
    return minutes


def bars_per_trading_day(market: Market, interval_minutes: int) -> float:
    """Observations one trading day holds at this grain.

    A daily bar is one observation per session date. The calendar builders refuse
    a grain coarser than a day before reaching this helper, so it never has to
    scale a weekly bar by session minutes — that is how the old constant reported
    35.8 observations a year for weekly data, when a week is a week in any market.
    """
    if interval_minutes >= MINUTES_PER_DAY:
        return 1.0
    return regular_session_minutes(market) / interval_minutes


def coarse_grain_reason(market: Optional[Market], interval_minutes: int) -> str:
    days = interval_minutes / MINUTES_PER_DAY
    label = market.value if market is not None else "the series"
    return (
        f"{label} is quoted on a {interval_minutes}-minute bar, which is {round(days, 4)} "
        "calendar days long; this contract derives observation frequency from exchange sessions, "
        "and a bar longer than a session is anchored to calendar weeks rather than to sessions, "
        "so no session-based observations-per-year applies to it"
    )


def sessions_in_window(market: Market, first: date, last: date) -> int:
    """Days this market could have been quoted between ``first`` and ``last``, inclusive.

    Closures the calendar lists are excluded; dates outside the declared table
    count as open, which is why ``inside_declared_coverage`` travels with any
    number derived from this count.
    """
    return len(trading_days_between(market, first, last))


def calendar_trading_days_per_year(
    market: Market,
    first: Optional[date] = None,
    last: Optional[date] = None,
) -> Tuple[float, bool]:
    """Sessions per year for this market, and whether the calendar covers the window.

    With a window, the rate is that window's session count over its elapsed years,
    so a holiday table is used where it is authoritative. Without one, the rate is
    averaged over the calendar's own covered years. ``always_open`` venues need no
    table at all: every calendar day is quoted, so 365.25 is exact by definition.
    """
    structure = get_market_structure(market)
    if structure.always_open:
        return MEAN_CALENDAR_DAYS_PER_YEAR, True

    if first is not None and last is not None and last > first:
        days = sessions_in_window(market, first, last)
        elapsed = (last - first).days / MEAN_CALENDAR_DAYS_PER_YEAR
        return days / elapsed, (structure.calendar_covers(first) and structure.calendar_covers(last))

    if structure.calendar_coverage is None:
        raise ValueError(f"{market.value} declares no holiday coverage window")
    start, end = structure.calendar_coverage
    years = (end - start).days / MEAN_CALENDAR_DAYS_PER_YEAR
    return sessions_in_window(market, start, end) / years, True


def frequency_name(interval_minutes: Optional[int]) -> str:
    if interval_minutes is None or interval_minutes <= 0:
        return "unknown"
    if interval_minutes == MINUTES_PER_DAY:
        return "daily"
    return "intraday" if interval_minutes < MINUTES_PER_DAY else "coarser-than-daily"


def basis_for_series(
    instrument_id: Optional[str],
    observation_dates: Iterable[Any],
    interval_minutes: int = MINUTES_PER_DAY,
) -> Basis:
    """The basis for a stored or simulated series, keyed on its own observation dates.

    ``observation_dates`` are the dates the run actually priced against, one per
    row. Two rows sharing a date means the series is not one-observation-per-day —
    an intraday or mixed-grain set reaching a daily calculation — which no
    annualization constant could fix, so the basis refuses instead of silently
    treating 5-minute bars as sessions.
    """
    dates: List[date] = []
    for value in observation_dates:
        parsed = _date_of(value)
        if parsed is None:
            return unavailable(
                f"observation stamp {value!r} is not a readable date, so the annualization "
                "window is unknown",
                market=instrument_id,
                interval_minutes=interval_minutes,
            )
        dates.append(parsed)

    if len(dates) < 2:
        return unavailable(
            f"the series carries {len(dates)} observation(s); at least two dated observations "
            "are needed to establish an annualization window",
            market=instrument_id,
            interval_minutes=interval_minutes,
            observation_count=len(dates),
        )

    duplicates = len(dates) - len(set(dates))
    if duplicates and interval_minutes >= MINUTES_PER_DAY:
        return unavailable(
            f"{duplicates} of {len(dates)} observations repeat a session date, so this series is "
            "not one observation per day; annualizing it on a daily basis would misrepresent its "
            "grain",
            interval_minutes=interval_minutes,
            observation_count=len(dates),
        )

    dates.sort()
    first, last = dates[0], dates[-1]
    elapsed_years = (last - first).days / MEAN_CALENDAR_DAYS_PER_YEAR
    count = len(dates)
    market = market_of(instrument_id)
    if structure_of(market) is None:
        return measured_basis(
            dates,
            interval_minutes=interval_minutes,
            cause=(
                f"{market.value} declares no market structure in {CALENDAR_SOURCE}"
                if market is not None
                else "The instrument carries no resolvable market"
            ),
        )

    return _calendar_basis(market, interval_minutes, first, last, count, elapsed_years)


def _calendar_basis(
    market: Market,
    interval_minutes: int,
    first: date,
    last: date,
    count: int,
    elapsed_years: float,
) -> Basis:
    if interval_minutes <= 0:
        return unavailable(
            f"interval {interval_minutes} minutes is not a positive bar length for {market.value}",
            market=market.value,
            interval_minutes=interval_minutes,
            observation_count=count,
            elapsed_years=round(elapsed_years, 6),
            first_observation=first.isoformat(),
            last_observation=last.isoformat(),
        )

    if interval_minutes > MINUTES_PER_DAY:
        return unavailable(
            coarse_grain_reason(market, interval_minutes),
            market=market.value,
            interval_minutes=interval_minutes,
            frequency=frequency_name(interval_minutes),
            observation_count=count,
            elapsed_years=round(elapsed_years, 6),
            first_observation=first.isoformat(),
            last_observation=last.isoformat(),
        )

    if elapsed_years <= 0:
        return unavailable(
            "the first and last observation fall on the same date, so the sample has no elapsed length",
            market=market.value,
            interval_minutes=interval_minutes,
            observation_count=count,
            first_observation=first.isoformat(),
            last_observation=last.isoformat(),
        )

    days_per_year, covered = calendar_trading_days_per_year(market, first, last)
    per_day = bars_per_trading_day(market, interval_minutes)
    observations_per_year = days_per_year * per_day
    expected = sessions_in_window(market, first, last) * per_day

    reason = None
    if not covered:
        reason = (
            f"the {market.value} holiday calendar does not cover "
            f"{first.isoformat()}..{last.isoformat()}; trading_days_per_year="
            f"{round(days_per_year, 4)} removes weekends only, so it is an assumption "
            "about unverified dates, not a fact about the exchange"
        )

    return Basis(
        available=True,
        frequency=frequency_name(interval_minutes),
        market=market.value,
        interval_minutes=interval_minutes,
        observations_per_year=round(observations_per_year, 6),
        trading_days_per_year=round(days_per_year, 6),
        bars_per_trading_day=round(per_day, 6),
        session_minutes_per_day=regular_session_minutes(market),
        elapsed_years=round(elapsed_years, 6),
        first_observation=first.isoformat(),
        last_observation=last.isoformat(),
        observation_count=count,
        expected_observations=round(expected, 4),
        coverage_pct=round(count / expected * 100, 4) if expected > 0 else None,
        inside_declared_coverage=covered,
        frequency_source="calendar",
        reason=reason,
    )


def basis_from_calendar(
    market: Optional[Market],
    interval_minutes: Optional[int],
) -> Basis:
    """The annual cadence for a market and grain, with no observation window.

    For callers that hold a market and a bar length but no dated series (journal
    aggregates, indicator warmups). The session rate is averaged over the
    calendar's declared coverage instead of over an unstated window, and a missing
    market or grain returns an unavailable basis: the alternative is assuming a
    frequency, which is the bug this module exists to close.
    """
    if market is None:
        return unavailable(
            "no market could be resolved, so observations per year is unknown; annualizing on an "
            "assumed frequency would report a number the data never supported",
            interval_minutes=interval_minutes,
        )
    if structure_of(market) is None:
        return unavailable(
            f"{market.value} declares no market structure in {CALENDAR_SOURCE}, so its sessions and "
            "closures are unknown; observations per year is reported as unavailable rather than "
            "scaled by a frequency the repository does not hold",
            market=market.value,
            interval_minutes=interval_minutes,
        )
    if not interval_minutes or interval_minutes <= 0:
        return unavailable(
            f"interval {interval_minutes} minutes is not a positive bar length for {market.value}",
            market=market.value,
            interval_minutes=interval_minutes,
        )
    if interval_minutes > MINUTES_PER_DAY:
        return unavailable(
            coarse_grain_reason(market, interval_minutes),
            market=market.value,
            interval_minutes=interval_minutes,
            frequency=frequency_name(interval_minutes),
        )

    days_per_year, covered = calendar_trading_days_per_year(market)
    per_day = bars_per_trading_day(market, interval_minutes)

    return Basis(
        available=True,
        frequency=frequency_name(interval_minutes),
        market=market.value,
        interval_minutes=interval_minutes,
        observations_per_year=round(days_per_year * per_day, 6),
        trading_days_per_year=round(days_per_year, 6),
        bars_per_trading_day=round(per_day, 6),
        session_minutes_per_day=regular_session_minutes(market),
        inside_declared_coverage=covered,
        frequency_source="calendar",
    )


def measured_basis(
    observation_times: Iterable[Any],
    interval_minutes: Optional[int] = None,
    cause: Optional[str] = None,
) -> Basis:
    """A basis measured from the series' own timestamps, when no calendar resolves it.

    This is the fallback of last resort: the cadence is what the data happened to
    deliver, so gaps lower the measured rate rather than raising a complaint. The
    result is marked ``frequency_source="measured"`` and
    ``inside_declared_coverage=False`` so a reader knows no exchange calendar
    stands behind it. ``cause`` says why no calendar was used, and defaults to an
    instrument whose market does not resolve.
    """
    stamps: List[datetime] = []
    for value in observation_times:
        if isinstance(value, datetime):
            stamp = value
        else:
            as_date = value if isinstance(value, date) else _date_of(value)
            if as_date is None:
                return unavailable(
                    "at least one observation stamp is not readable, so no observation frequency can be measured",
                    interval_minutes=interval_minutes,
                )
            stamp = datetime(as_date.year, as_date.month, as_date.day)
        stamps.append(stamp)
    if len(stamps) < 2:
        return unavailable(
            f"the series carries {len(stamps)} observation(s); frequency needs at least two",
            interval_minutes=interval_minutes,
            observation_count=len(stamps),
        )

    ordered = sorted(stamps)
    # Whole seconds, not timedelta.days: 60 four-hour bars span 9.83 days, and
    # truncating that to 9 measures the cadence about 9% too fast.
    elapsed_days = (ordered[-1] - ordered[0]).total_seconds() / 86400.0
    stamp_of = (
        (lambda ts: ts.date().isoformat())
        if interval_minutes and interval_minutes >= MINUTES_PER_DAY
        else (lambda ts: ts.isoformat())
    )
    if elapsed_days <= 0:
        return unavailable(
            "every observation carries the same timestamp, so no observation frequency is measurable",
            interval_minutes=interval_minutes,
            observation_count=len(stamps),
            first_observation=stamp_of(ordered[0]),
            last_observation=stamp_of(ordered[-1]),
        )

    elapsed_years = elapsed_days / MEAN_CALENDAR_DAYS_PER_YEAR
    observations_per_year = len(stamps) / elapsed_years

    return Basis(
        available=True,
        frequency=frequency_name(interval_minutes),
        market=None,
        interval_minutes=interval_minutes,
        observations_per_year=round(observations_per_year, 6),
        elapsed_years=round(elapsed_years, 6),
        first_observation=stamp_of(ordered[0]),
        last_observation=stamp_of(ordered[-1]),
        observation_count=len(stamps),
        inside_declared_coverage=False,
        frequency_source="measured",
        reason=(
            f"{cause or 'The instrument carries no resolvable market'}, so "
            f"observations_per_year={round(observations_per_year, 4)} is measured from the "
            "series' own timestamps rather than from an exchange calendar; missing bars lower "
            "this rate"
        ),
    )


def _refused(basis: Basis, metric: str, alternative: str) -> Dict[str, Any]:
    """The unavailable struct an annualized metric takes when the basis refuses.

    ``value`` is None, never 0.0: an annual figure that could not be derived has to
    read as missing, not as a flat year.
    """
    cause = basis.reason or (
        f"the basis records an observation frequency for {basis.market or 'this series'} but no "
        "observation dates, so the elapsed length of the sample is unknown"
    )
    return {
        "value": None,
        "reason": (
            f"Annualization refused: {cause.rstrip('.')}. Therefore annualized {metric} is "
            f"unavailable under {basis.method_version}, {alternative}."
        ),
        "annualization": basis.as_dict(),
    }


def annualized_return_pct(total_return_pct: float, basis: Basis) -> Any:
    """Compound the sample's total return over its elapsed years.

    Returns an unavailable struct, never 0.0, when the basis refuses or the sample
    is shorter than a year.
    """
    if not basis.available or basis.elapsed_years is None:
        return _refused(
            basis,
            "return",
            "rather than annualized on an assumed window",
        )
    if total_return_pct <= -100:
        return {
            "value": None,
            "reason": (
                "The sample lost everything, so the compounding base is zero and no annualized "
                "return exists."
            ),
            "annualization": basis.as_dict(),
        }
    if basis.elapsed_years < MIN_YEARS_FOR_ANNUALIZATION:
        return {
            "value": None,
            "reason": (
                f"Sample spans {round(basis.elapsed_years, 4)} years "
                f"({basis.observation_count} observations), below the "
                f"{MIN_YEARS_FOR_ANNUALIZATION}-year minimum (a sample must cover about a year "
                "before it is annualized as one); annualized return is not displayed "
                "because short-period annualization can exaggerate apparent performance."
            ),
            "annualization": basis.as_dict(),
        }
    annualized = ((1 + total_return_pct / 100) ** (1 / basis.elapsed_years) - 1) * 100
    return round(annualized, 4)


def annualized_volatility_pct(period_return_std: float, basis: Basis) -> Any:
    """Scale a per-observation standard deviation by sqrt(observations per year)."""
    if not basis.available or basis.observations_per_year is None:
        return _refused(
            basis,
            "volatility",
            "rather than scaled by an assumed observation frequency",
        )
    short = basis.elapsed_years is not None and basis.elapsed_years < MIN_YEARS_FOR_ANNUALIZATION
    if short:
        return {
            "value": None,
            "reason": (
                f"Sample spans {round(basis.elapsed_years, 4)} years "
                f"({basis.observation_count} observations), below the "
                f"{MIN_YEARS_FOR_ANNUALIZATION}-year minimum (a sample must cover about a year "
                "before it is annualized as one); annualized volatility is not displayed "
                "because short-period estimates have wide confidence intervals."
            ),
            "annualization": basis.as_dict(),
        }
    if period_return_std <= 0:
        return {
            "value": None,
            "reason": "Insufficient return variation",
            "annualization": basis.as_dict(),
        }
    return round(period_return_std * math.sqrt(basis.observations_per_year) * 100, 4)


def observations_per_year(basis: Basis) -> Optional[float]:
    """The Sharpe/HV scaling rate, or None when the basis refuses.

    Callers that would have reached for a constant go through this, so an
    unresolvable frequency becomes an unavailable metric instead of a silent 252.
    """
    if not basis.available or not basis.observations_per_year or basis.observations_per_year <= 0:
        return None
    return basis.observations_per_year
