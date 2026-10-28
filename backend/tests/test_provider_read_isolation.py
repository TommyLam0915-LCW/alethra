"""Provider read isolation for manual replay.

These reads decide which provider's bars a replay session sees, so silently
mixing providers corrupts every downstream paper number. The previous version
of this file asserted against a Mock query chain, which cannot observe a
SQLAlchemy expression passed positionally to ``filter``: the "filter applied"
cases failed while the "filter not applied" case passed for the wrong reason.
They now run against the real test database and assert on the rows returned.
"""
from datetime import date, datetime
from uuid import uuid4

import pytest

from app.api.simulations import (
    _get_stored_trade_dates,
    _load_stored_ohlcv,
    _resolve_session_provider,
)
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.simulation import SimulationSession
from tests.conftest import TestSession

SYMBOL = "US:AAPL"
# Deliberately different prices per provider so leakage shows up in the data.
TWELVEDATA_OPEN = 190.94
DEMO_OPEN = 200.176


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _bar(provider_id: str, day: int, open_: float) -> MarketDataBar:
    bar_date = date(2024, 1, day)
    return MarketDataBar(
        instrument_id=SYMBOL,
        provider_id=provider_id,
        bar_date=bar_date,
        bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
        open=open_,
        high=open_ + 1,
        low=open_ - 1,
        close=open_ + 0.5,
        volume=1_000_000,
    )


def _seed_two_providers(db):
    """twelvedata owns 01-29/01-30, demo owns 01-29/01-31."""
    db.add(_bar("twelvedata", 29, TWELVEDATA_OPEN))
    db.add(_bar("twelvedata", 30, TWELVEDATA_OPEN + 1))
    db.add(_bar("demo", 29, DEMO_OPEN))
    db.add(_bar("demo", 31, DEMO_OPEN + 2))
    db.commit()


def _session(db, provider_id=None, data_cutoff_at="2024-01-30"):
    owner_id = uuid4()
    experiment = Experiment(id=uuid4(), owner_id=owner_id, title="Provider isolation probe")
    db.add(experiment)
    version = ExperimentVersion(
        id=uuid4(),
        experiment_id=experiment.id,
        version_number=1,
        created_by=owner_id,
        strategy_spec={
            "template_type": "buy_and_hold",
            "asset_ids": [SYMBOL],
            "start_date": "2024-01-29",
            "end_date": "2024-01-30",
            "initial_cash": 10000,
        },
    )
    db.add(version)
    experiment.current_version_id = version.id
    session = SimulationSession(
        id=uuid4(),
        experiment_version_id=version.id,
        owner_id=owner_id,
        initial_virtual_cash=10000.0,
        data_cutoff_at=data_cutoff_at,
        provider_id=provider_id,
    )
    db.add(session)
    db.commit()
    db.expire_all()
    return db.query(SimulationSession).filter(SimulationSession.id == session.id).one()


class TestProviderReadIsolation:
    def test_load_stored_ohlcv_filters_by_provider(self, db):
        _seed_two_providers(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id="twelvedata")

        assert sorted(df.index.tolist()) == ["2024-01-29", "2024-01-30"]
        assert df.iloc[0]["open"] == pytest.approx(TWELVEDATA_OPEN)
        assert DEMO_OPEN not in df["open"].tolist(), "demo bars leaked into a twelvedata read"

    def test_load_stored_ohlcv_without_provider_loads_all(self, db):
        _seed_two_providers(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id=None)

        # Without a provider the read is deliberately unfiltered; that is what
        # makes an explicit provider_id necessary.
        assert len(df) == 4
        assert set(df["open"]) == {TWELVEDATA_OPEN, TWELVEDATA_OPEN + 1, DEMO_OPEN, DEMO_OPEN + 2}

    def test_load_stored_ohlcv_isolates_the_other_provider(self, db):
        _seed_two_providers(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id="demo")

        assert sorted(df.index.tolist()) == ["2024-01-29", "2024-01-31"]
        assert TWELVEDATA_OPEN not in df["open"].tolist()

    def test_get_stored_trade_dates_filters_by_provider(self, db):
        _seed_two_providers(db)

        dates = _get_stored_trade_dates(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id="twelvedata")

        assert dates == ["2024-01-29", "2024-01-30"], "2024-01-31 exists only on demo"

    def test_get_stored_trade_dates_without_provider_merges_providers(self, db):
        _seed_two_providers(db)

        dates = _get_stored_trade_dates(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id=None)

        assert dates == ["2024-01-29", "2024-01-30", "2024-01-31"]

    def test_resolve_session_provider_uses_explicit_provider_id(self, db):
        _seed_two_providers(db)
        session = _session(db, provider_id="twelvedata")

        assert _resolve_session_provider(session, db) == "twelvedata"

    def test_resolve_session_provider_infers_from_stored_data(self, db):
        db.add(_bar("twelvedata", 29, TWELVEDATA_OPEN))
        db.commit()
        session = _session(db, provider_id=None)

        assert _resolve_session_provider(session, db) == "twelvedata"

    def test_resolve_session_provider_prefers_twelvedata_over_demo(self, db):
        _seed_two_providers(db)
        session = _session(db, provider_id=None)

        assert _resolve_session_provider(session, db) == "twelvedata"

    def test_resolve_session_provider_demo_only_session_resolves_demo(self, db):
        db.add(_bar("demo", 29, DEMO_OPEN))
        db.commit()
        session = _session(db, provider_id=None)

        assert _resolve_session_provider(session, db) == "demo"

    def test_resolve_session_provider_returns_none_when_no_data(self, db):
        session = _session(db, provider_id=None)

        assert _resolve_session_provider(session, db) is None

    def test_resolve_session_provider_respects_the_cutoff_window(self, db):
        # The only stored bar sits outside the session window, so there is
        # nothing to infer from. Returning a provider here would replay against
        # data the user could not have seen at the cutoff.
        db.add(_bar("twelvedata", 29, TWELVEDATA_OPEN))
        db.commit()
        session = _session(db, provider_id=None, data_cutoff_at="2024-01-28")

        assert _resolve_session_provider(session, db) is None
