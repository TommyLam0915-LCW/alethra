"""ALETHRA-LEDGER-02: Explicit daily accounting output.

Exercises the full integration path:
  env settings -> get_data_provider() -> TwelveDataProvider -> run_backtest()

Synthetic OHLCV bars are served via a mocked requests.Session.get (the
upstream HTTP layer).  No real API credentials or network traffic.

Covers:
  A. Zero-cost buy-and-hold — daily state and terminal P&L
  B. Nonzero-commission buy-and-hold — daily state and terminal P&L
  C. Daily equity identity: equity = cash_balance + position_qty * valuation_price
  D. Provider selection
  E. Upstream request count (get_bars + compute_checksum duplicate)
  F. Determinism: identical repeated runs produce same daily state and checksum
  G. requests.Session.send is blocked (no unmocked external traffic)

Daily output contract (per entry in daily_equity_curve):
  {date, cash_balance, position_qty, valuation_price, equity}
"""

import json
import os
from copy import deepcopy
from unittest.mock import patch, Mock, MagicMock

import pytest

from app.config import get_settings
from app.engine.backtest import run_backtest


# ---------------------------------------------------------------------------
# Safety: block unmocked HTTP within this test file
# ---------------------------------------------------------------------------

def _block_real_session_send(*args, **kwargs):
    raise RuntimeError(
        "Unmocked requests.Session.send() was called inside "
        "test_twelve_data_backtest_accounting — all HTTP must be mocked."
    )


# ---------------------------------------------------------------------------
# Synthetic OHLCV fixture — 5 trading days, zero slippage
# ---------------------------------------------------------------------------

SYNTHETIC_BARS = [
    # datetime (reverse order in API response — API returns newest-first)
    {"datetime": "2024-01-08", "open": "105.00", "high": "107.00",
     "low": "104.00", "close": "106.00", "volume": "62000"},
    {"datetime": "2024-01-05", "open": "104.50", "high": "106.00",
     "low": "103.00", "close": "105.50", "volume": "58000"},
    {"datetime": "2024-01-04", "open": "102.00", "high": "105.00",
     "low": "101.00", "close": "104.00", "volume": "60000"},
    {"datetime": "2024-01-03", "open": "101.50", "high": "103.00",
     "low": "100.50", "close": "102.00", "volume": "55000"},
    {"datetime": "2024-01-02", "open": "100.00", "high": "102.00",
     "low": "99.00", "close": "101.00", "volume": "50000"},
]

SYNTHETIC_META = {
    "symbol": "AAPL",
    "interval": "1day",
    "currency": "USD",
    "exchange_timezone": "America/New_York",
    "exchange": "NASDAQ",
    "mic_code": "XNGS",
    "type": "Common Stock",
}

SYNTHETIC_API_RESPONSE = {
    "meta": SYNTHETIC_META,
    "values": SYNTHETIC_BARS,
    "status": "ok",
}

# Chronologically sorted bars (after provider sorts by date)
#   Date        Open    High    Low     Close
#   2024-01-02  100.00  102.00  99.00   101.00
#   2024-01-03  101.50  103.00  100.50  102.00
#   2024-01-04  102.00  105.00  101.00  104.00
#   2024-01-05  104.50  106.00  103.00  105.50
#   2024-01-08  105.00  107.00  104.00  106.00

INITIAL_CASH = 100_000.0
START_DATE = "2024-01-01"
END_DATE = "2024-01-10"
ASSET_ID = "US:AAPL"


# ---------------------------------------------------------------------------
# Independent expected-accounting table
# ---------------------------------------------------------------------------

def _expected_buy_and_hold(
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
) -> dict:
    """Hand-calculated buy-and-hold accounting for the synthetic fixture.

    Strategy contract (from strategies.run_buy_and_hold):
      - Signal: day-0; execution: day-0 open
      - exec_price_buy = open[0] * (1 + slippage_rate)
      - max_shares = int(initial_cash / (exec_price_buy * (1 + commission_rate)))
      - gross_buy = max_shares * exec_price_buy
      - commission_buy = gross_buy * commission_rate
      - cash_after_buy = initial_cash - gross_buy - commission_buy

      - Signal: last day; execution: last day close
      - exec_price_sell = close[-1] * (1 - slippage_rate)
      - gross_sell = max_shares * exec_price_sell
      - commission_sell = gross_sell * commission_rate
      - cash_after_sell = cash_after_buy + gross_sell - commission_sell

    _build_daily_equity contract:
      - For each trading day in chronological order:
        1. Apply transactions dated on this day (cash += net_cash_change,
           shares = t.shares_after)
        2. equity = cash + shares * close
    """
    opens = [100.00, 101.50, 102.00, 104.50, 105.00]
    closes = [101.00, 102.00, 104.00, 105.50, 106.00]
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]

    # BUY
    exec_price_buy = opens[0] * (1 + slippage_rate)
    max_shares = int(initial_cash / (exec_price_buy * (1 + commission_rate)))
    gross_buy = max_shares * exec_price_buy
    commission_buy = round(gross_buy * commission_rate, 4)
    cash_after_buy = initial_cash - gross_buy - commission_buy

    # SELL
    exec_price_sell = closes[-1] * (1 - slippage_rate)
    gross_sell = max_shares * exec_price_sell
    commission_sell = round(gross_sell * commission_rate, 4)
    cash_after_sell = cash_after_buy + gross_sell - commission_sell

    # BUY transaction accounting
    buy_net_cash_change = round(-(gross_buy + commission_buy), 4)

    # SELL transaction accounting
    sell_net_cash_change = round(gross_sell - commission_sell, 4)

    # Daily equity (value = cash + shares * close) and daily state
    daily_equity = []
    daily_state = []
    cash = initial_cash
    shares = 0
    for i, d in enumerate(dates):
        if d == dates[0]:  # BUY day
            cash += buy_net_cash_change
            shares = max_shares
        if d == dates[-1]:  # SELL day
            cash += sell_net_cash_change
            shares = 0
        equity = round(cash + shares * closes[i], 4)
        daily_equity.append(equity)
        daily_state.append({
            "date": d,
            "cash_balance": round(cash, 4),
            "position_qty": shares,
            "valuation_price": closes[i],
            "equity": equity,
        })

    return {
        "exec_price_buy": exec_price_buy,
        "exec_price_sell": exec_price_sell,
        "max_shares": max_shares,
        "gross_buy": round(gross_buy, 4),
        "commission_buy": commission_buy,
        "cash_after_buy": round(cash_after_buy, 4),
        "gross_sell": round(gross_sell, 4),
        "commission_sell": commission_sell,
        "cash_after_sell": round(cash_after_sell, 4),
        "buy_net_cash_change": buy_net_cash_change,
        "sell_net_cash_change": sell_net_cash_change,
        "daily_equity": daily_equity,
        "daily_state": daily_state,
        "final_equity": daily_equity[-1],
        "net_pnl": round(daily_equity[-1] - initial_cash, 4),
        "total_commission": round(commission_buy + commission_sell, 4),
        "dates": dates,
        "closes": closes,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_settings(**env_overrides):
    env = {
        "APP_ENV": "development",
        "JWT_SECRET_KEY": "change-me-in-production-use-a-long-random-string",
    }
    env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        from app.config import Settings
        return Settings(_env_file=None)


def _make_mock_session_get():
    """Return a Mock for requests.Session.get that serves SYNTHETIC_API_RESPONSE."""
    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = deepcopy(SYNTHETIC_API_RESPONSE)

    mock_get = Mock(return_value=mock_resp)
    return mock_get


def _make_spec(commission_rate=0.0, slippage_rate=0.0):
    return {
        "schema_version": "1.0",
        "template_type": "buy_and_hold",
        "asset_ids": [ASSET_ID],
        "frequency": "1d",
        "start_date": START_DATE,
        "end_date": END_DATE,
        "initial_cash": INITIAL_CASH,
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
        "signal_parameters": {},
        "entry_rule": {},
        "exit_rule": {},
        "sizing_rule": {},
        "execution_timing": {"execute_at": "next_open"},
        "currency": "USD",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _block_unmocked_http():
    """Block any unmocked requests.Session.send in this test file."""
    with patch("requests.Session.send", side_effect=_block_real_session_send):
        yield


@pytest.fixture
def reset_env_and_registry():
    """Save and restore environment, settings cache, and provider registry."""
    import app.providers.provider_registry as reg

    saved_environ = {
        "DATA_PROVIDER": os.environ.get("DATA_PROVIDER"),
        "TWELVE_DATA_API_KEY": os.environ.get("TWELVE_DATA_API_KEY"),
    }
    saved_providers = dict(reg._PROVIDERS)
    saved_lseg = reg._LSEG_INITIALIZED
    saved_td = reg._TWELVE_DATA_INITIALIZED

    yield

    # Restore
    for k, v in saved_environ.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reg._PROVIDERS.clear()
    reg._PROVIDERS.update(saved_providers)
    reg._LSEG_INITIALIZED = saved_lseg
    reg._TWELVE_DATA_INITIALIZED = saved_td
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Scenario A: zero-cost buy-and-hold through TwelveDataProvider
# ---------------------------------------------------------------------------

class TestA_ZeroCostAccounting:
    """Zero commission, zero slippage — verify pure price-change P&L."""

    @patch("requests.Session.get")
    def test_zero_cost_daily_equity_and_terminal_pnl(
        self, mock_session_get, reset_env_and_registry
    ):
        mock_session_get.side_effect = _make_mock_session_get().side_effect
        mock_session_get.return_value = _make_mock_session_get()()

        # Actually use a proper side-effect function for consistent returns
        def _serve_synthetic(*args, **kwargs):
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = deepcopy(SYNTHETIC_API_RESPONSE)
            return resp

        mock_session_get.side_effect = _serve_synthetic

        os.environ["DATA_PROVIDER"] = "twelve_data"
        os.environ["TWELVE_DATA_API_KEY"] = "fake-backtest-accounting-key"
        get_settings.cache_clear()

        from app.providers.provider_registry import get_data_provider
        provider = get_data_provider()

        spec = _make_spec(commission_rate=0.0, slippage_rate=0.0)
        result = run_backtest(spec, data_provider=provider, data_source="provider")

        assert result["run_status"] == "completed", f"errors: {result['errors']}"
        assert result["errors"] == []

        expected = _expected_buy_and_hold(
            initial_cash=INITIAL_CASH, commission_rate=0.0, slippage_rate=0.0,
        )

        # --- Verify transactions ---
        txns = result["transactions"]
        assert len(txns) == 2, f"Expected 2 transactions, got {len(txns)}"

        buy = txns[0]
        sell = txns[1]

        assert buy["side"] == "BUY"
        assert sell["side"] == "SELL"
        assert buy["quantity"] == expected["max_shares"]
        assert sell["quantity"] == expected["max_shares"]

        # Execution prices (zero slippage -> exec = observed)
        assert abs(buy["execution_price_before_costs"] - expected["exec_price_buy"]) < 1e-6
        assert abs(sell["execution_price_before_costs"] - expected["exec_price_sell"]) < 1e-6

        # Commission must be zero
        assert abs(buy["commission_amount"]) < 1e-6
        assert abs(sell["commission_amount"]) < 1e-6

        # Cash after
        assert abs(buy["cash_after"] - expected["cash_after_buy"]) < 1e-4
        assert abs(sell["cash_after"] - expected["cash_after_sell"]) < 1e-4

        # shares_after
        assert buy["shares_after"] == expected["max_shares"]
        assert sell["shares_after"] == 0

        # --- Verify daily equity with explicit state ---
        daily_curve = result["daily_equity_curve"]
        assert len(daily_curve) == len(expected["dates"])

        for i, (got, exp) in enumerate(zip(daily_curve, expected["daily_state"])):
            assert got["date"] == exp["date"], f"Date mismatch at day {i}"

            # --- Direct daily-state assertions ---
            assert abs(got["cash_balance"] - exp["cash_balance"]) < 1e-4, (
                f"Day {i} ({exp['date']}): cash_balance {got['cash_balance']} "
                f"!= expected {exp['cash_balance']}"
            )
            assert got["position_qty"] == exp["position_qty"], (
                f"Day {i} ({exp['date']}): position_qty {got['position_qty']} "
                f"!= expected {exp['position_qty']}"
            )
            assert abs(got["valuation_price"] - exp["valuation_price"]) < 1e-6, (
                f"Day {i} ({exp['date']}): valuation_price {got['valuation_price']} "
                f"!= expected {exp['valuation_price']}"
            )
            assert abs(got["equity"] - exp["equity"]) < 1e-2, (
                f"Day {i} ({exp['date']}): equity {got['equity']} "
                f"!= expected {exp['equity']}"
            )

            # --- Identity: equity = cash_balance + position_qty * valuation_price ---
            identity = got["cash_balance"] + got["position_qty"] * got["valuation_price"]
            assert abs(got["equity"] - identity) < 1e-4, (
                f"Day {i} ({exp['date']}): equity identity violated: "
                f"equity={got['equity']}, cash+qty*price={identity}"
            )

        # --- Terminal flat position and net-P&L identity ---
        terminal = daily_curve[-1]
        assert terminal["position_qty"] == 0, "Terminal position must be flat"
        assert abs(terminal["cash_balance"] - expected["cash_after_sell"]) < 1e-4

        # --- Verify final equity / P&L ---
        assert abs(result["final_equity"] - expected["final_equity"]) < 1e-2
        assert abs(result["metrics"]["final_equity"] - expected["final_equity"]) < 1e-2
        assert abs(result["metrics"]["total_return_pct"] - (expected["net_pnl"] / INITIAL_CASH * 100)) < 1e-2

        # net_pnl = final_equity - initial_cash
        implied_pnl = result["final_equity"] - INITIAL_CASH
        assert abs(implied_pnl - expected["net_pnl"]) < 1e-2


# ---------------------------------------------------------------------------
# Scenario B: nonzero-commission buy-and-hold through TwelveDataProvider
# ---------------------------------------------------------------------------

class TestB_NonzeroCommissionAccounting:
    """Nonzero commission, zero slippage — verify fee drag on P&L."""

    @patch("requests.Session.get")
    def test_commission_reduces_equity_vs_zero_cost(
        self, mock_session_get, reset_env_and_registry
    ):
        def _serve_synthetic(*args, **kwargs):
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = deepcopy(SYNTHETIC_API_RESPONSE)
            return resp

        mock_session_get.side_effect = _serve_synthetic

        os.environ["DATA_PROVIDER"] = "twelve_data"
        os.environ["TWELVE_DATA_API_KEY"] = "fake-backtest-commission-key"
        get_settings.cache_clear()

        from app.providers.provider_registry import get_data_provider
        provider = get_data_provider()

        comm_rate = 0.001  # 10 bps
        spec = _make_spec(commission_rate=comm_rate, slippage_rate=0.0)
        result = run_backtest(spec, data_provider=provider, data_source="provider")

        assert result["run_status"] == "completed", f"errors: {result['errors']}"
        assert result["errors"] == []

        expected = _expected_buy_and_hold(
            initial_cash=INITIAL_CASH, commission_rate=comm_rate, slippage_rate=0.0,
        )

        # --- Verify transactions ---
        txns = result["transactions"]
        assert len(txns) == 2

        buy = txns[0]
        sell = txns[1]

        # Share count — must be floor(initial_cash / (exec*(1+rate)))
        assert buy["quantity"] == expected["max_shares"]

        # Commission amounts
        assert abs(buy["commission_amount"] - expected["commission_buy"]) < 1e-2
        assert abs(sell["commission_amount"] - expected["commission_sell"]) < 1e-2

        # Total commissions in metrics
        assert abs(
            result["metrics"]["total_commissions"] - expected["total_commission"]
        ) < 1e-2

        # --- Verify daily equity with explicit state ---
        daily_curve = result["daily_equity_curve"]
        assert len(daily_curve) == len(expected["dates"])

        for i, (got, exp) in enumerate(zip(daily_curve, expected["daily_state"])):
            assert got["date"] == exp["date"], f"Date mismatch at day {i}"

            # --- Direct daily-state assertions ---
            assert abs(got["cash_balance"] - exp["cash_balance"]) < 1e-4, (
                f"Day {i} ({exp['date']}): cash_balance {got['cash_balance']} "
                f"!= expected {exp['cash_balance']}"
            )
            assert got["position_qty"] == exp["position_qty"], (
                f"Day {i} ({exp['date']}): position_qty {got['position_qty']} "
                f"!= expected {exp['position_qty']}"
            )
            assert abs(got["valuation_price"] - exp["valuation_price"]) < 1e-6, (
                f"Day {i} ({exp['date']}): valuation_price {got['valuation_price']} "
                f"!= expected {exp['valuation_price']}"
            )
            assert abs(got["equity"] - exp["equity"]) < 1e-2, (
                f"Day {i} ({exp['date']}): equity {got['equity']} "
                f"!= expected {exp['equity']}"
            )

            # --- Identity: equity = cash_balance + position_qty * valuation_price ---
            identity = got["cash_balance"] + got["position_qty"] * got["valuation_price"]
            assert abs(got["equity"] - identity) < 1e-4, (
                f"Day {i} ({exp['date']}): equity identity violated: "
                f"equity={got['equity']}, cash+qty*price={identity}"
            )

        # --- Terminal flat position and net-P&L identity with commission ---
        terminal = daily_curve[-1]
        assert terminal["position_qty"] == 0, "Terminal position must be flat"
        assert abs(terminal["cash_balance"] - expected["cash_after_sell"]) < 1e-4
        assert abs(result["final_equity"] - expected["cash_after_sell"]) < 1e-2

        implied_pnl = result["final_equity"] - INITIAL_CASH
        assert abs(implied_pnl - expected["net_pnl"]) < 1e-2

        # Commission must be > 0
        assert result["metrics"]["total_commissions"] > 0

        # Compare to zero-commission run: final equity must be lower
        expected_zero = _expected_buy_and_hold(
            initial_cash=INITIAL_CASH, commission_rate=0.0, slippage_rate=0.0,
        )
        assert result["final_equity"] < expected_zero["final_equity"]


# ---------------------------------------------------------------------------
# Scenario C: Daily equity identity (equity = cash + qty * price)
# ---------------------------------------------------------------------------

class TestC_DailyEquityIdentity:
    """Verify equity = cash_balance + position_qty * valuation_price on each day.

    Uses the directly exposed daily-state fields, not reconstruction from
    transactions.
    """

    @patch("requests.Session.get")
    def test_equity_identity_holds_daily(self, mock_session_get, reset_env_and_registry):
        def _serve_synthetic(*args, **kwargs):
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = deepcopy(SYNTHETIC_API_RESPONSE)
            return resp

        mock_session_get.side_effect = _serve_synthetic

        os.environ["DATA_PROVIDER"] = "twelve_data"
        os.environ["TWELVE_DATA_API_KEY"] = "fake-backtest-identity-key"
        get_settings.cache_clear()

        from app.providers.provider_registry import get_data_provider
        provider = get_data_provider()

        comm_rate = 0.0005
        spec = _make_spec(commission_rate=comm_rate, slippage_rate=0.0)
        result = run_backtest(spec, data_provider=provider, data_source="provider")

        assert result["run_status"] == "completed"

        expected = _expected_buy_and_hold(
            initial_cash=INITIAL_CASH, commission_rate=comm_rate, slippage_rate=0.0,
        )

        daily_curve = result["daily_equity_curve"]
        assert len(daily_curve) == len(expected["daily_state"])

        for i, (got, exp) in enumerate(zip(daily_curve, expected["daily_state"])):
            d = got["date"]

            # Verify expected values match
            assert abs(got["equity"] - exp["equity"]) < 1e-2, (
                f"Day {d}: equity {got['equity']} != expected {exp['equity']}"
            )

            # Verify the identity DIRECTLY from exposed daily fields:
            #   equity = cash_balance + position_qty * valuation_price
            identity = got["cash_balance"] + got["position_qty"] * got["valuation_price"]
            assert abs(got["equity"] - identity) < 1e-4, (
                f"Day {d}: equity identity violated: "
                f"equity={got['equity']}, cash+qty*price={identity}"
            )

        # Terminal flat position
        terminal = daily_curve[-1]
        assert terminal["position_qty"] == 0, "Terminal position must be flat"
        assert abs(terminal["cash_balance"] - expected["cash_after_sell"]) < 1e-4


# ---------------------------------------------------------------------------
# Scenario E: Upstream request count
# ---------------------------------------------------------------------------

class TestE_UpstreamRequestCount:
    """Assert the actual mocked upstream request count.

    run_backtest(data_source='provider') calls:
      1. get_bars() via _fetch_ohlcv_via_provider → 1 Session.get
      2. compute_checksum() → 1 Session.get (duplicate fetch)
    Total: 2 Session.get calls for 1 asset.
    """

    @patch("requests.Session.get")
    def test_upstream_request_count_is_two(
        self, mock_session_get, reset_env_and_registry
    ):
        def _serve_synthetic(*args, **kwargs):
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = deepcopy(SYNTHETIC_API_RESPONSE)
            return resp

        mock_session_get.side_effect = _serve_synthetic

        os.environ["DATA_PROVIDER"] = "twelve_data"
        os.environ["TWELVE_DATA_API_KEY"] = "fake-backtest-count-key"
        get_settings.cache_clear()

        from app.providers.provider_registry import get_data_provider
        provider = get_data_provider()

        spec = _make_spec(commission_rate=0.0, slippage_rate=0.0)
        result = run_backtest(spec, data_provider=provider, data_source="provider")

        assert result["run_status"] == "completed"

        # 2 calls: 1 for _fetch_ohlcv_via_provider + 1 for compute_checksum
        assert mock_session_get.call_count == 2, (
            f"Expected exactly 2 upstream HTTP calls, got {mock_session_get.call_count}"
        )


# ---------------------------------------------------------------------------
# Scenario F: Determinism — identical runs produce same economics and checksum
# ---------------------------------------------------------------------------

class TestF_Determinism:
    """Run identical backtests with reset mock responses; compare stable outputs."""

    @patch("requests.Session.get")
    def test_deterministic_economics_and_checksum(
        self, mock_session_get, reset_env_and_registry
    ):
        def _serve_synthetic(*args, **kwargs):
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = deepcopy(SYNTHETIC_API_RESPONSE)
            return resp

        os.environ["DATA_PROVIDER"] = "twelve_data"
        os.environ["TWELVE_DATA_API_KEY"] = "fake-backtest-determ-key"
        get_settings.cache_clear()

        from app.providers.provider_registry import get_data_provider

        comm_rate = 0.001
        spec = _make_spec(commission_rate=comm_rate, slippage_rate=0.0)

        results = []
        for _ in range(2):
            # Reset mock between runs
            mock_session_get.reset_mock()
            mock_session_get.side_effect = _serve_synthetic

            provider = get_data_provider()
            result = run_backtest(spec, data_provider=provider, data_source="provider")
            assert result["run_status"] == "completed"
            results.append(result)

        r1, r2 = results

        # --- Exclude generated IDs/timestamps from comparison ---
        # dataset_snapshot_id is uuid.uuid4()
        # transaction_id is uuid.uuid4() per fill — compared separately below
        exclude_keys = {"dataset_snapshot_id", "transactions"}

        for key in r1:
            if key in exclude_keys:
                continue
            assert r1[key] == r2[key], f"Non-deterministic divergence in field '{key}'"

        # --- Checksum stability ---
        assert r1["dataset_checksum"] == r2["dataset_checksum"]
        assert r1["dataset_checksum"] is not None

        # --- Economic output stability ---
        assert r1["final_equity"] == r2["final_equity"]
        assert r1["daily_equity_curve"] == r2["daily_equity_curve"]
        assert r1["metrics"] == r2["metrics"]

        # --- Daily state stability (cash_balance, position_qty, valuation_price) ---
        for d1, d2 in zip(r1["daily_equity_curve"], r2["daily_equity_curve"]):
            assert d1["date"] == d2["date"]
            assert d1["cash_balance"] == d2["cash_balance"]
            assert d1["position_qty"] == d2["position_qty"]
            assert d1["valuation_price"] == d2["valuation_price"]
            assert d1["equity"] == d2["equity"]

        # --- Transaction stability (excluding transaction_id UUIDs) ---
        for t1, t2 in zip(r1["transactions"], r2["transactions"]):
            for k, v in t1.items():
                if k == "transaction_id":
                    continue
                assert t1[k] == t2[k], (
                    f"Transaction field '{k}' diverged: {t1[k]} vs {t2[k]}"
                )

        # --- Request count stability ---
        assert mock_session_get.call_count == 2


# ---------------------------------------------------------------------------
# Scenario D: get_data_provider() selects TwelveDataProvider
# ---------------------------------------------------------------------------

class TestD_ProviderSelection:
    """get_data_provider() must return TwelveDataProvider with fake key."""

    def test_provider_selection(self, reset_env_and_registry):
        os.environ["DATA_PROVIDER"] = "twelve_data"
        os.environ["TWELVE_DATA_API_KEY"] = "fake-selection-key"
        get_settings.cache_clear()

        from app.providers.provider_registry import get_data_provider
        from app.providers.twelve_data_provider import TwelveDataProvider

        provider = get_data_provider()
        assert isinstance(provider, TwelveDataProvider)
        assert provider.provider_id == "twelve_data"
        assert provider.is_available()


# ---------------------------------------------------------------------------
# Limitations
# ---------------------------------------------------------------------------

_LIMITATIONS = """
DAILY RECONCILIATION — RESOLVED (ALETHRA-LEDGER-02)
-----------------------------------------------------
The daily_equity_curve now exposes per-day:
  {date, cash_balance, position_qty, valuation_price, equity}

The equity identity (equity = cash_balance + position_qty * valuation_price)
is verified for every trading day in TestA, TestB, and TestC.

REMAINING LIMITATIONS:
  - Single-instrument path only. The engine operates on asset_ids[0].
  - No multi-asset portfolio valuation. If the engine is extended to
    support multiple concurrent positions, per-asset decomposition
    would be needed.
  - valuation_price is the OHLCV close price; intraday or alternative
    pricing sources are not supported.
"""


# ---------------------------------------------------------------------------
# Smoke: run this file standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(_LIMITATIONS)
    pytest.main([__file__, "-v"])
