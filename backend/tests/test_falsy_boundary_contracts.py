"""Empty string, NULL and zero are three different answers, not one.

The backend reads several optional fields with bare truthiness (`if row.field:`)
or with a coalescing default (`row.field or fallback`). Both spellings are fine
until the field's legal domain contains a falsy value that *means* something —
`""`, `0`, `[]` — or until a default turns "the caller asked for nothing" into
"run the whole grid". Four of those sites were adjudicated as defects by the
falsy-boundary sweep (`fix-line-20261115/tools/falsyBoundarySweep.py` plus its
verdict registry) and are pinned here:

1. `app/api/paper_portfolio.py` stored-data reads — a session with no bound
   provider used to apply **no provider filter**, so a bound account was priced
   from whichever provider held the newest bar, which contradicts the invariant
   stated in that function's own docstring. The same raw-lane hand-off also sat
   in the order lifecycle view and in the valuation provenance label, so the
   resolution now lives in one helper used by all three.
2. `app/api/learning.py` trigger — `instruments: []` / `intervals: []` are legal
   request bodies (no `min_length`) and used to be rewritten into the full
   3 x 4 scheduled grid.
3. `app/services/paper_trading.py` long-only guard — `trade.direction or "long"`
   converted an unrecorded side into a tradeable long *before* the guard that is
   supposed to reject it.
4. `app/api/model_research_export.py` — a blank `data_cutoff_at` (the sentinel
   `app/services/personal_training.py:295` has to store, because the column is
   NOT NULL) was printed into the exported artefact as an empty value instead of
   "unknown".

Synthetic data only, no provider calls, no broker, no real market download.
Passing here means these four sites distinguish absent from empty. It is not
evidence about the remaining verdicts in the registry (those are benign by
shape, by schema constraint, or by failing closed), and it is not evidence about
the NULL-vs-zero metrics contract, which is tracked with the API/frontend
contract lane because a backend-only `null` breaks `.toFixed(2)` consumers.
"""

import inspect
import uuid as _uuid
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from tests.conftest import TestSession

# Two stored-data lanes for one instrument. `_resolve_session_provider` prefers
# `twelvedata`, then `demo`, so an *unbound* session has exactly one honest
# resolution and the assertions below are deterministic.
LANE_PREFERRED = "twelvedata"
LANE_NEWER = "demo"
PREFERRED_CLOSE = 222.0
NEWER_CLOSE = 777.0  # same instrument, later date: what an unfiltered read picks
CURSOR = "2024-03-04"


def _enum(value):
    return SimpleNamespace(value=value)


def _seed_two_lanes(*, provider_id):
    """Daily session whose `provider_id` is exactly `provider_id`, plus two lanes.

    The instrument id is per-call so the market-data unique key cannot collide
    with any other test, and the prices are ones the demo fixture does not hold.
    """
    from app.models.experiment import Experiment, ExperimentStatus, Visibility
    from app.models.experiment_version import ExperimentVersion
    from app.models.market_data import MarketDataBar
    from app.models.simulation import SimulationSession, SimulationStatus
    from app.models.user import User

    asset = f"US:LANES-{_uuid.uuid4().hex[:8]}"
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        experiment = Experiment(
            id=_uuid.uuid4(), owner_id=user.id,
            title="Provider lane (synthetic)",
            hypothesis="an unbound session must not be marked across providers",
            status=ExperimentStatus.locked, visibility=Visibility.private,
            current_version_id=None,
        )
        db.add(experiment)
        db.flush()
        version = ExperimentVersion(
            id=_uuid.uuid4(), experiment_id=experiment.id, version_number=1,
            strategy_spec={
                "asset_ids": [asset],
                "start_date": "2024-03-01",
                "end_date": CURSOR,
                "commission_rate": 0.0,
            },
            created_by=user.id,
            locked_at=datetime(2024, 3, 4, 12, 0, 0), locked_by=user.id,
            lock_reason="falsy boundary fixture", dataset_cutoff_at=CURSOR,
            strategy_config_hash="synthetic-lanes",
        )
        db.add(version)
        db.flush()
        experiment.current_version_id = version.id
        session = SimulationSession(
            id=_uuid.uuid4(), experiment_version_id=version.id, owner_id=user.id,
            initial_virtual_cash=100_000.0, data_cutoff_at=CURSOR,
            current_snapshot_date=CURSOR, timeframe=None,
            status=SimulationStatus.active, provider_id=provider_id, state_version=1,
        )
        db.add(session)
        for lane_provider, lane_day, close in (
            (LANE_PREFERRED, date(2024, 3, 1), PREFERRED_CLOSE),
            (LANE_NEWER, date(2024, 3, 4), NEWER_CLOSE),
        ):
            db.add(MarketDataBar(
                id=_uuid.uuid4(), instrument_id=asset, provider_id=lane_provider,
                bar_date=lane_day,
                bar_timestamp=datetime.combine(lane_day, datetime.min.time()),
                open=close, high=close, low=close, close=close, volume=1000,
            ))
        db.commit()
        return str(session.id), asset
    finally:
        db.close()


def _marks_for(session_id, asset):
    """Call the real valuation reader with a portfolio bound to `session_id`."""
    from app.api.paper_portfolio import _session_bound_marks
    from app.models.simulation import SimulationSession

    db = TestSession()
    try:
        session = db.query(SimulationSession).filter(
            SimulationSession.id == _uuid.UUID(session_id)
        ).one()
        portfolio = SimpleNamespace(simulation_session_id=session.id)
        return _session_bound_marks(db, portfolio, [asset])
    finally:
        db.close()


class TestProviderLaneOfAnUnboundSession:
    """`if session.provider_id:` silently meant "any provider may price this"."""

    def test_null_provider_is_marked_from_the_resolved_lane_not_the_newest_bar(
        self, client, auth_headers
    ):
        session_id, asset = _seed_two_lanes(provider_id=None)
        marks = _marks_for(session_id, asset)
        assert marks[asset]["close"] == PREFERRED_CLOSE, (
            "an unbound session was priced from a provider its replay does not use"
        )
        assert marks[asset]["source"] == "stored_market_data_bar"

    def test_empty_string_provider_is_treated_as_unbound_not_as_a_filter(
        self, client, auth_headers
    ):
        # '' used to fall into the "no provider filter" branch while the response
        # label and the replay both read it as a named default lane.
        session_id, asset = _seed_two_lanes(provider_id="")
        marks = _marks_for(session_id, asset)
        assert marks[asset]["close"] == PREFERRED_CLOSE

    def test_bound_session_keeps_marking_from_its_own_lane(self, client, auth_headers):
        # Guards the fix from over-reach: an explicitly bound session still reads
        # only that provider, including when it is the non-preferred lane.
        session_id, asset = _seed_two_lanes(provider_id=LANE_NEWER)
        marks = _marks_for(session_id, asset)
        assert marks[asset]["close"] == NEWER_CLOSE

    def test_session_with_nothing_resolvable_is_left_unpriced(
        self, client, auth_headers
    ):
        from app.models.market_data import MarketDataBar

        session_id, asset = _seed_two_lanes(provider_id=None)
        db = TestSession()
        try:
            db.query(MarketDataBar).filter(
                MarketDataBar.instrument_id == asset
            ).delete()
            db.commit()
        finally:
            db.close()
        assert _marks_for(session_id, asset) == {}

    def test_the_lane_the_reader_uses_is_one_helper_not_three_reads(
        self, client, auth_headers
    ):
        """The same resolution must serve valuation, labels and the lifecycle view."""
        from app.api.paper_portfolio import _bound_session_provider
        from app.models.simulation import SimulationSession

        session_id, _asset = _seed_two_lanes(provider_id=None)
        db = TestSession()
        try:
            session = db.query(SimulationSession).filter(
                SimulationSession.id == _uuid.UUID(session_id)
            ).one()
            assert _bound_session_provider(db, session) == LANE_PREFERRED
        finally:
            db.close()

    def test_no_stored_data_reader_hands_out_the_raw_lane(self):
        """`session.provider_id` passed straight through means "no filter" upstream."""
        import app.api.paper_portfolio as pp

        src = inspect.getsource(pp)
        assert "provider_id=session.provider_id," not in src, (
            "a bound-session read was given the raw nullable lane"
        )
        assert 'session.provider_id or "stored_market_data"' not in src, (
            "the valuation label hid which lane the marks came from"
        )


class _InertExecutor:
    """A scheduler the admission check can read, with no thread behind it."""

    running = True

    def get_job(self, job_id):
        from app.workers.learning_scheduler import DRAIN_JOB_ID

        class _Drain:
            next_run_time = datetime(2099, 1, 1)

        return _Drain() if job_id == DRAIN_JOB_ID else None


class TestLearningTriggerKeepsTheRequestedScope:
    """Omitting the field means "default"; sending [] means "nothing"."""

    URL = "/api/v1/learning/jobs/trigger"

    @pytest.fixture(autouse=True)
    def _inert_executor(self):
        """Let the trigger admit, with nothing that could run research.

        Admission is pinned in ``test_learning_api.py``; these nodes are about
        what the queued row records. The stub reports a next tick in 2099 and
        never fires, so no provider call can start from here.
        """
        from app.workers.learning_scheduler import register_learning_scheduler

        register_learning_scheduler(_InertExecutor())
        yield
        register_learning_scheduler(None)

    def _post(self, client, auth_headers, body):
        return client.post(self.URL, json=body, headers=auth_headers)

    def _queue_then_drain(self, client, auth_headers, body):
        """Queue through the API, then let the real executor run that row."""
        from unittest.mock import patch

        import app.workers.learning_scheduler as ls

        resp = self._post(client, auth_headers, body)
        assert resp.status_code == 201, resp.text
        job_id = _uuid.UUID(resp.json()["job"]["id"])

        calls = []

        def _record(db, user_id, instrument_id, interval_minutes):
            calls.append((instrument_id, interval_minutes))
            return {"walk_forward": {"out_of_sample": {"trades": 0}}}

        with patch.object(ls, "_run_single_study", side_effect=_record):
            assert ls.drain_learning_queue() == 1
        return job_id, calls

    def test_empty_instruments_is_refused_not_expanded_into_the_grid(
        self, client, auth_headers
    ):
        r = self._post(
            client, auth_headers, {"job_type": "scheduled_study", "instruments": []}
        )
        assert r.status_code == 400, r.text
        assert "instruments" in r.json()["detail"]

    def test_empty_intervals_is_refused_not_expanded_into_the_grid(
        self, client, auth_headers
    ):
        r = self._post(
            client, auth_headers, {"job_type": "scheduled_study", "intervals": []}
        )
        assert r.status_code == 400, r.text
        assert "intervals" in r.json()["detail"]

    def test_omitted_scope_records_the_scheduled_grid(self, client, auth_headers):
        from app.workers.learning_scheduler import (
            SCHEDULED_INSTRUMENTS,
            SCHEDULED_INTERVALS,
        )

        r = self._post(client, auth_headers, {"job_type": "scheduled_study"})
        assert r.status_code == 201, r.text
        config = r.json()["job"]["config_json"]
        assert config["instruments"] == SCHEDULED_INSTRUMENTS
        assert config["intervals"] == SCHEDULED_INTERVALS

    def test_requested_scope_is_recorded_and_then_run(self, client, auth_headers):
        """The config the row records is the grid the executor actually runs.

        When the trigger ran the batch inline, recording and running could not
        drift apart. Under the queue they are two calls at two times, so the
        executor has to iterate what this row froze rather than whatever the
        settings say when it arrives.
        """
        from app.models.learning_job import LearningJob

        job_id, calls = self._queue_then_drain(
            client,
            auth_headers,
            {
                "job_type": "scheduled_study",
                "instruments": ["CRYPTO:KRAKEN:SOL-USD"],
                "intervals": [240, 60],
            },
        )

        db = TestSession()
        try:
            job = db.get(LearningJob, job_id)
            config, result = job.config_json, job.result_json
        finally:
            db.close()

        assert config["instruments"] == ["CRYPTO:KRAKEN:SOL-USD"]
        assert config["intervals"] == [240, 60]
        assert calls == [("CRYPTO:KRAKEN:SOL-USD", 240), ("CRYPTO:KRAKEN:SOL-USD", 60)]
        assert result["total_combos"] == 2
        assert result["successful"] == 2

    def test_none_still_means_the_scheduled_default(self, client, auth_headers):
        from app.workers.learning_scheduler import (
            SCHEDULED_INSTRUMENTS,
            SCHEDULED_INTERVALS,
        )

        _, calls = self._queue_then_drain(
            client, auth_headers, {"job_type": "scheduled_study"}
        )

        assert calls == [
            (instrument, interval)
            for instrument in SCHEDULED_INSTRUMENTS
            for interval in SCHEDULED_INTERVALS
        ]


class TestLongOnlyGuardFailsClosed:
    """A side that was never recorded is not a long side."""

    @pytest.mark.parametrize("side", [None, "", "   ", "short", "sell", "1", "-1"])
    def test_blank_and_non_long_sides_are_refused(self, side):
        from app.services.paper_trading import _require_long_only_side

        with pytest.raises(ValueError, match="Long-only mode"):
            _require_long_only_side(side, "trade-under-test")

    @pytest.mark.parametrize(
        "side,expected", [("long", "long"), ("Long", "long"), ("buy", "buy"), ("BUY", "buy")]
    )
    def test_long_sides_pass_through_normalised(self, side, expected):
        from app.services.paper_trading import _require_long_only_side

        assert _require_long_only_side(side, "trade-under-test") == expected

    def test_replay_reads_the_side_through_the_guard(self):
        from app.services.paper_trading import PaperTradingEngine

        src = inspect.getsource(PaperTradingEngine.execute_historical_replay)
        assert "_require_long_only_side(" in src
        assert 'or "long"' not in src, (
            "the replay defaulted an unrecorded side back to long"
        )


def _evaluation(**over):
    base = dict(
        evaluation_type=_enum("historical_test"),
        engine_version="engine-1",
        evaluator=_enum("system"),
        data_cutoff_at="",
        methodology_version="mv-1",
        result_summary="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _record_and_version():
    record = SimpleNamespace(
        title="Cutoff rendering (synthetic)",
        purpose="blank cutoff must not read as a real one",
        status=_enum("completed"),
        description="",
    )
    version = SimpleNamespace(
        version_number=1,
        model_type=_enum("lgbm"),
        created_at=None,
        locked_at=None,
        revoked_at=None,
        revocation_reason=None,
        model_specification_json={"features": []},
    )
    return record, version


class TestBlankCutoffIsLabelledUnknown:
    """`''` in a NOT NULL date column is 'unknown', and must be printed so."""

    def test_methodology_document_says_unknown_not_nothing(self):
        from app.api.model_research_export import _build_methodology_md

        record, version = _record_and_version()
        md = _build_methodology_md(record, version, [_evaluation()])
        assert "- Data Cutoff: unknown" in md
        assert "- Result:" not in md  # blank summary omits the line rather than blanking it

    def test_research_summary_document_says_unknown_not_parens(self):
        from app.api.model_research_export import _build_research_summary_md

        record, version = _record_and_version()
        md = _build_research_summary_md(record, version, [_evaluation()], None)
        assert "[historical_test] No summary (unknown)" in md
        assert "()" not in md

    def test_a_real_cutoff_is_still_rendered_verbatim(self):
        from app.api.model_research_export import _build_methodology_md

        record, version = _record_and_version()
        md = _build_methodology_md(
            record, version, [_evaluation(data_cutoff_at="2024-03-04", result_summary="ok")]
        )
        assert "- Data Cutoff: 2024-03-04" in md
        assert "- Result: ok" in md
