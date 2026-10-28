"""The methodology summary must describe the bars a run actually used.

It used to hardcode "Demo data only — no live market data" for every run, which
mislabels storage- or provider-backed runs — the exact confusion the acceptance
brief forbids. The end-to-end case below covers the synthetic path through
``run_backtest``; the non-synthetic branches are exercised at builder level,
because producing live-natured stored bars needs a seeded MarketDataMetadata row
rather than a strategy spec.
"""

from app.engine.backtest import _build_methodology_summary, run_backtest

SYNTHETIC_CONSTRAINT = "Synthetic demo bars only — no real market data in this run."
UNRESOLVED_CONSTRAINT = (
    "Data provenance unresolved for this run — do not read these results as market-based."
)


def _base_spec(**overrides):
    spec = {
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
        "benchmark_id": "DEMO-BENCHMARK",
        "currency": "USD",
    }
    spec.update(overrides)
    return spec


def test_a_synthetic_run_says_synthetic_and_no_longer_clains_demo_only_by_default():
    result = run_backtest(_base_spec())
    assert result["run_status"] == "completed"
    assert result["data_nature"] == {"DEMO-ALPHA": "synthetic"}

    constraints = result["methodology_summary"]["constraints"]
    assert SYNTHETIC_CONSTRAINT in constraints
    # The old blanket wording asserted a data source the run never inspected.
    assert not any("Demo data only" in c for c in constraints)


def test_a_provider_backed_summary_names_the_provider_and_each_asset_nature():
    summary = _build_methodology_summary(
        _base_spec(),
        "buy_and_hold",
        data_nature={"AAPL": "delayed", "DEMO-ALPHA": "synthetic"},
        provider_name="twelve_data",
        feed_mode="delayed",
    )
    constraint = next(c for c in summary["constraints"] if c.startswith("Data nature per asset"))

    assert "AAPL=delayed" in constraint
    assert "DEMO-ALPHA=synthetic" in constraint
    assert "twelve_data" in constraint
    assert "delayed" in constraint
    # A mixed run must still flag the synthetic leg rather than let the real one
    # lend it credibility.
    assert "Any asset marked synthetic is not real market data." in constraint
    assert SYNTHETIC_CONSTRAINT not in summary["constraints"]


def test_unresolved_provenance_is_flagged_instead_of_being_assumed_synthetic():
    summary = _build_methodology_summary(_base_spec(), "buy_and_hold", data_nature=None)
    assert UNRESOLVED_CONSTRAINT in summary["constraints"]
    assert SYNTHETIC_CONSTRAINT not in summary["constraints"]


def test_the_executed_exit_policy_is_disclosed_with_its_conservative_assumptions():
    summary = _build_methodology_summary(_base_spec(), "buy_and_hold", data_nature={})
    policy = summary["exit_policy"]

    assert policy["source"].startswith("app/engine/exit_rules.py")
    assert "min(session_open, stop_level)" in policy["stop"]
    assert "fills at the target level exactly" in policy["target"]
    assert "stop is assumed to fill first" in policy["same_bar_stop_and_target"]
    # The ordering is an assumption, and the disclosure must say so rather than
    # imply the daily bar evidenced it.
    assert "not inferred from the data" in policy["same_bar_stop_and_target"]
    assert "bar after the entry execution bar" in policy["level_evaluation_start"]
    assert "end_of_data" in policy["end_of_data"]

    constraints = summary["constraints"]
    assert not any("No intraday stops or targets" in c for c in constraints)
    assert not any("neither level is ever executed" in c for c in constraints)
    same_bar = next(c for c in constraints if "same-bar" in c or "touching both levels" in c)
    assert "stop-first" in same_bar

    # realized_r moved out of the constraints list into the field it describes.
    realized_r = summary["journal_field_semantics"]["realized_r"]
    assert "never floored at -1R" in realized_r


def test_the_disclosed_exit_policy_matches_what_the_engine_executes():
    """Guard against the disclosure drifting from exit_rules again.

    The last time these disagreed, the methodology panel told users a declared
    stop was never simulated while the engine was trading it.
    """
    from app.engine import exit_rules

    summary = _build_methodology_summary(
        {**_base_spec(), "risk_parameters": {"stop_pct": 0.05}}, "buy_and_hold", data_nature={}
    )
    assert summary["parameters"]["risk_parameters_declared"] is True
    assert any("exit_policy" in str(rule) or "Declared stop and target" in rule
               for rule in summary["strategy_rules"])

    # A gapped stop fills at the open, which is exactly what the disclosure says.
    decision = exit_rules.evaluate_bar(
        {"open": 80.0, "high": 82.0, "low": 79.0, "close": 81.0}, 95.0, None
    )
    assert decision.reason == exit_rules.STOP
    assert decision.observed_price == 80.0
    assert "min(session_open, stop_level)" in summary["exit_policy"]["stop"]

    # A gapped target fills at the level, not at the better open.
    decision = exit_rules.evaluate_bar(
        {"open": 120.0, "high": 125.0, "low": 119.0, "close": 124.0}, None, 110.0
    )
    assert decision.observed_price == 110.0
    assert "fills at the target level exactly" in summary["exit_policy"]["target"]


def test_an_undeclared_plan_is_not_described_as_protected():
    summary = _build_methodology_summary(_base_spec(), "buy_and_hold", data_nature={})

    assert summary["parameters"]["risk_parameters_declared"] is False
    assert not any("Declared stop and target" in rule for rule in summary["strategy_rules"])
    assert "no stop and no target" in summary["exit_policy"]["undeclared_plan"]


def test_the_sell_convention_documents_the_fixed_holding_period_exit():
    summary = _build_methodology_summary(_base_spec(), "buy_and_hold", data_nature={})
    sell = summary["execution_convention"]["sell"]

    assert "observed_exit_price * (1 - slippage_rate)" in sell
    assert "holding-period or end-of-data exit" in sell
    assert "SMA cross-down" in sell
    assert "min(session_open, stop_level)" in sell
