"""A benchmark that was requested but has no bars must be reported, not dropped.

A null ``benchmark_total_return_pct`` is ambiguous on its own: no benchmark was
asked for, or one was asked for and its feed carried nothing in the window. The
run-detail page resolves that from the stored request plus the run warnings, so
the backend has to keep both halves truthful. The "none requested" half was
covered; the "requested but unavailable" branch was not.
"""

from types import SimpleNamespace

import pandas as pd

from app.engine.backtest import run_backtest


class _AssetOnlyProvider:
    """Serves the traded asset, and nothing for any other symbol.

    The benchmark is deliberately absent rather than missing from
    ``get_available_symbols``: the engine only validates traded assets there, so
    this isolates the post-fetch branch that names an unavailable benchmark.
    """

    provider_id = "asset-only"
    provider_name = "Asset Only Provider"
    feed_mode = "test"

    def get_available_symbols(self):
        return ["DEMO-ALPHA"]

    def get_bars(self, symbol, start_date, end_date):
        if symbol != "DEMO-ALPHA":
            return []
        rows = []
        day = pd.Timestamp("2024-01-02")
        end = pd.Timestamp(end_date)
        price = 100.0
        while day <= end:
            rows.append(
                SimpleNamespace(
                    date=day.strftime("%Y-%m-%d"),
                    open=price,
                    high=price + 1.0,
                    low=price - 1.0,
                    close=price,
                )
            )
            day += pd.Timedelta(days=1)
            price += 0.5
        return rows

    def get_data_quality(self, symbol):
        return {"data_nature": "synthetic"}


def _spec(benchmark_id):
    return {
        "schema_version": "1.0",
        "template_type": "buy_and_hold",
        "asset_ids": ["DEMO-ALPHA"],
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
        "benchmark_id": benchmark_id,
        "currency": "USD",
    }


def test_a_requested_benchmark_without_bars_is_named_and_not_invented():
    result = run_backtest(
        _spec("DEMO-MISSING"),
        data_provider=_AssetOnlyProvider(),
        data_source="provider",
    )

    assert result["run_status"] == "completed"
    assert result["metrics"]["benchmark_total_return_pct"] is None
    assert result["metrics"]["excess_return_pct"] is None
    assert result["benchmark_equity_curve"] == []
    assert "Benchmark DEMO-MISSING unavailable for this date range." in result["warnings"]
    # The request survives in the methodology snapshot, so the reader can tell
    # "requested but absent" from "nothing was requested".
    assert result["methodology_summary"]["parameters"]["benchmark_id"] == "DEMO-MISSING"


def test_no_benchmark_requested_reports_no_comparison_without_a_warning():
    result = run_backtest(
        _spec(None),
        data_provider=_AssetOnlyProvider(),
        data_source="provider",
    )

    assert result["run_status"] == "completed"
    assert result["metrics"]["benchmark_total_return_pct"] is None
    assert result["benchmark_equity_curve"] == []
    assert not any("enchmark" in w for w in result["warnings"])
    assert result["methodology_summary"]["parameters"]["benchmark_id"] is None