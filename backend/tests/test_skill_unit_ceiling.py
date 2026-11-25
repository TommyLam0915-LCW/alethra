"""§三A: the skill-unit ceiling is enforced by the backend at every entry point.

The counting object is one ``paper_portfolios`` row: that row is what carries the
virtual cash and, through ``portfolio_id``, the positions, orders, fills and
decisions of one 技能單位. These tests therefore assert on rows the ordinary user
can see back through the formal API, not on internals.

Concurrency is measured separately: ``TestCeilingUnderRealConcurrency`` skips on
the SQLite lane — where no row lock exists — and runs on the PostgreSQL lane, so
a green sqlite run never claims the race was proved.
"""

import pytest

from app.config import get_settings
from app.services.skill_units import RELEASE_ENTRYPOINT
from tests.conftest import TEST_ENGINE

_ON_SQLITE = TEST_ENGINE.dialect.name == "sqlite"


def _headers(client, email):
    """Register an ordinary main account and return its Bearer headers."""
    client.post("/api/auth/register", json={
        "risk_acknowledged": True,
        "email": email,
        "password": "testpass123",
        "display_name": "Ceiling Owner",
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={
        "email": email,
        "password": "testpass123",
    })
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create(client, headers, cash=10000.0):
    return client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
    })


def _held(client, headers):
    """Count the accounts the caller can see, i.e. the slots actually taken."""
    listed = client.get("/api/v1/paper-portfolio", headers=headers)
    assert listed.status_code == 200, listed.text
    return listed.json()


@pytest.fixture()
def ceiling(monkeypatch):
    """Set the ceiling for the process and hand back the value used."""

    def _set(limit):
        monkeypatch.setattr(get_settings(), "paper_skill_unit_max_per_user", limit)
        return limit

    return _set


class TestSkillUnitCeiling:
    def test_the_shipped_ceiling_admits_ten_and_refuses_the_eleventh(self, client):
        headers = _headers(client, "ceiling-default@alethra.io")
        assert get_settings().paper_skill_unit_max_per_user == 10

        for i in range(10):
            resp = _create(client, headers)
            assert resp.status_code == 201, (i, resp.text)

        refused = _create(client, headers)
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["error"] == "skill_unit_limit_reached"
        assert detail["limit"] == 10
        assert detail["held_skill_units"] == 10
        assert detail["release_entrypoint"] == RELEASE_ENTRYPOINT
        assert detail["message"].strip()

        # The refusal wrote nothing: the account set is exactly the ten admitted.
        held = _held(client, headers)
        assert len(held) == 10
        assert len({p["id"] for p in held}) == 10

    def test_retiring_releases_a_slot_so_the_ceiling_is_not_a_dead_end(
        self, client, ceiling
    ):
        limit = ceiling(2)
        headers = _headers(client, "ceiling-retire@alethra.io")
        opened = [_create(client, headers).json()["id"] for _ in range(limit)]
        assert _create(client, headers).status_code == 409

        retired = client.post(
            f"/api/v1/paper-portfolio/{opened[0]}/retire", headers=headers
        )
        assert retired.status_code == 200, retired.text
        assert retired.json()["status"] == "retired"

        admitted = _create(client, headers)
        assert admitted.status_code == 201, admitted.text

        held = _held(client, headers)
        assert len([p for p in held if p["status"] in ("active", "paused")]) == limit
        assert len([p for p in held if p["id"] == opened[0]]) == 1, (
            "retiring releases the slot but must not delete the account"
        )

    def test_pausing_does_not_release_a_slot(self, client, ceiling):
        limit = ceiling(2)
        headers = _headers(client, "ceiling-pause@alethra.io")
        opened = [_create(client, headers).json()["id"] for _ in range(limit)]

        paused = client.post(
            f"/api/v1/paper-portfolio/{opened[0]}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"

        refused = _create(client, headers)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["held_skill_units"] == limit, (
            "a paused account still holds cash, positions and pending orders, so "
            "it is still one of the skill units"
        )

    def test_the_ceiling_is_per_main_account(self, client, ceiling):
        limit = ceiling(1)
        a = _headers(client, "ceiling-a@alethra.io")
        b = _headers(client, "ceiling-b@alethra.io")

        assert _create(client, a).status_code == 201
        assert _create(client, a).status_code == 409
        assert _create(client, b).status_code == 201, (
            "one account being full must not lock out another customer"
        )
        assert _create(client, b).status_code == 409

        assert len(_held(client, a)) == limit
        assert len(_held(client, b)) == limit

    def test_the_refusal_does_not_emit_an_event_or_touch_balances(
        self, client, ceiling
    ):
        limit = ceiling(1)
        headers = _headers(client, "ceiling-clean@alethra.io")
        first = _create(client, headers)
        assert first.status_code == 201
        assert _create(client, headers).status_code == 409

        listed = _held(client, headers)
        assert [p["initial_virtual_cash"] for p in listed] == [10000.0]
        assert [p["current_virtual_cash"] for p in listed] == [10000.0]


class TestCeilingSharedByEveryEntryPoint:
    """A limit one entry point enforces while another bypasses is not a limit."""

    def test_paper_trading_create_refuses_at_the_same_ceiling(self, client, ceiling):
        limit = ceiling(1)
        headers = _headers(client, "ceiling-engine@alethra.io")
        assert _create(client, headers).status_code == 201

        resp = client.post(
            "/api/v1/paper-trading/create",
            headers=headers,
            json={
                "skill": "momentum",
                "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                "interval_minutes": 60,
                "exit_config": "exit_1",
                "initial_capital": 100000.0,
            },
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "skill_unit_limit_reached", (
            "a full account is a refusal, not the catch-all 500 creation_failed"
        )
        assert len(_held(client, headers)) == limit

    def test_the_daemon_lane_counts_before_it_inserts(self, client, ceiling, monkeypatch):
        """The daemon writes the same rows, so it goes through the same guard.

        Reaching the insert would prove nothing here: the daemon's SQL is
        PostgreSQL-only, so a guard that let it through would fail on ``NOW()``
        rather than on the ceiling. The assertion is therefore that the skill is
        refused *and* that no portfolio row appeared for it.
        """
        from app.services.live_trading_daemon import LivePaperTradingDaemon
        from app.services.signal_generator import StrategyType

        limit = ceiling(1)
        headers = _headers(client, "ceiling-daemon@alethra.io")
        opened = _create(client, headers)
        assert opened.status_code == 201
        owner_id = opened.json()["owner_id"]

        from tests.conftest import TestSession

        monkeypatch.setattr(
            "app.services.live_trading_daemon.SessionLocal", lambda: TestSession()
        )
        daemon = LivePaperTradingDaemon(
            symbols=["BTC/USD"],
            skills=[{
                "skill": "range_fade",
                "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                "interval_minutes": 60,
                "exit_config": "exit_1",
                "rule_id": StrategyType.RANGE_FADE.value,
                "user_id": owner_id,
            }],
        )
        assert daemon._create_portfolios() == 0
        assert daemon.ceiling_refusals == ["range_fade_BTC/USD"], (
            "the ceiling refusal must be reported as a ceiling refusal, not as "
            "an unknown strategy"
        )
        assert len(_held(client, headers)) == limit


class TestCeilingUnderRealConcurrency:
    """The concurrent case, which only a PostgreSQL server can prove.

    SQLite serialises writers and takes no row lock, so the sequential tests
    above cannot distinguish a guard that locks from one that merely counts.
    This node competes two transactions for the last free slot and says so. Its
    destination is named rather than implied: it runs under
    ``bash ./backend/scripts/run_tests.sh pg`` with ``ALETHRA_PG_TEST_URL`` set
    to a disposable database. A skip here is a statement about the lane, not a
    pass.
    """

    @pytest.mark.skipif(
        _ON_SQLITE,
        reason=(
            "two first-time creates at the last free slot need real MVCC and a "
            "PostgreSQL backend: SQLite serialises writers and takes no row lock"
        ),
    )
    def test_two_creates_at_the_last_free_slot_admit_exactly_one(self, ceiling):
        from threading import Barrier, Thread
        from uuid import UUID, uuid4

        from sqlalchemy import text

        from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
        from app.services.skill_units import (
            SkillUnitLimitReached,
            assert_slot_available,
            held_slot_count,
        )
        from tests.conftest import TestSession

        limit = ceiling(1)
        owner_id = str(uuid4())

        seed = TestSession()
        try:
            seed.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at, "
                    "updated_at, is_active, is_admin) VALUES (:id, :email, "
                    ":password_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "true, false)"
                ),
                {
                    "id": owner_id,
                    "email": f"ceiling-race-{owner_id[:8]}@test.local",
                    "password_hash": "not-a-credential",
                },
            )
            seed.commit()
        finally:
            seed.close()

        outcomes = []
        started = Barrier(2)

        def attempt():
            db = TestSession()
            try:
                started.wait()
                assert_slot_available(db, owner_id)
                db.add(PaperPortfolio(
                    owner_id=UUID(owner_id),
                    currency="USD",
                    initial_virtual_cash=1000.0,
                    current_virtual_cash=1000.0,
                    status=PortfolioStatus.active,
                ))
                db.commit()
                outcomes.append("admitted")
            except SkillUnitLimitReached:
                db.rollback()
                outcomes.append("refused")
            except Exception as exc:  # a race must not be reported as a refusal
                db.rollback()
                outcomes.append(f"error:{type(exc).__name__}:{exc}")
            finally:
                db.close()

        threads = [Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(outcomes) == ["admitted", "refused"], outcomes
        probe = TestSession()
        try:
            assert held_slot_count(probe, owner_id) == 1
        finally:
            probe.execute(text("DELETE FROM paper_portfolios WHERE owner_id = :u"), {"u": owner_id})
            probe.execute(text("DELETE FROM users WHERE id = :u"), {"u": owner_id})
            probe.commit()
            residual_portfolios = probe.execute(
                text("SELECT count(*) FROM paper_portfolios WHERE owner_id = :u"), {"u": owner_id}
            ).scalar()
            residual_users = probe.execute(
                text("SELECT count(*) FROM users WHERE id = :u"), {"u": owner_id}
            ).scalar()
            probe.close()
        assert (residual_portfolios, residual_users) == (0, 0), (
            "the seeded account must not survive this node"
        )


class TestCeilingConfiguration:
    def test_a_ceiling_of_zero_is_refused_at_startup(self):
        """0 would lock every customer out of their first skill unit."""
        from app.config import Settings

        with pytest.raises(ValueError) as exc:
            Settings(_env_file=None, paper_skill_unit_max_per_user=0)
        assert "PAPER_SKILL_UNIT_MAX_PER_USER" in str(exc.value)

    def test_the_held_statuses_are_the_documented_pair(self):
        from app.models.paper_portfolio import PortfolioStatus
        from app.services.skill_units import SLOT_HOLDING_STATUSES

        assert SLOT_HOLDING_STATUSES == (PortfolioStatus.active, PortfolioStatus.paused)
