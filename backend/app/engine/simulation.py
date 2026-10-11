from typing import Dict, Any, Optional, List
import pandas as pd

SIMULATION_ENGINE_VERSION = "1.0.0"


class StepResult:
    __slots__ = (
        "decision_type",
        "decision_detail",
        "paper_cash",
        "paper_position",
        "paper_equity",
        "warnings",
        "snapshot_date",
        "data_cutoff_at",
        "engine_version",
    )

    def __init__(
        self,
        decision_type: str,
        decision_detail: Optional[Dict[str, Any]],
        paper_cash: float,
        paper_position: Optional[Dict[str, Any]],
        paper_equity: float,
        warnings: List[str],
        snapshot_date: str,
        data_cutoff_at: str,
        engine_version: str,
    ):
        self.decision_type = decision_type
        self.decision_detail = decision_detail
        self.paper_cash = round(paper_cash, 4)
        self.paper_position = paper_position
        self.paper_equity = round(paper_equity, 4)
        self.warnings = warnings
        self.snapshot_date = snapshot_date
        self.data_cutoff_at = data_cutoff_at
        self.engine_version = engine_version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_type": self.decision_type,
            "decision_detail": self.decision_detail,
            "paper_cash": self.paper_cash,
            "paper_position": self.paper_position,
            "paper_equity": self.paper_equity,
            "warnings": self.warnings,
            "snapshot_date": self.snapshot_date,
            "data_cutoff_at": self.data_cutoff_at,
            "engine_version": self.engine_version,
        }


class ForwardSimulator:
    def __init__(
        self,
        strategy_spec: Dict[str, Any],
        initial_cash: float,
        commission_rate: float,
        slippage_rate: float,
        dataset_cutoff_at: str,
        engine_version: str,
    ):
        self.strategy_spec = strategy_spec
        self.template_type = strategy_spec["template_type"]
        self.symbol = strategy_spec["asset_ids"][0]
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.shares = 0
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.dataset_cutoff_at = dataset_cutoff_at
        self.engine_version = engine_version

        self.entry_price: Optional[float] = None
        self.entry_date: Optional[str] = None

        self._prev_sma_short: Optional[float] = None
        self._prev_sma_long: Optional[float] = None

        self._holding_steps_remaining: Optional[int] = None

        self._bought = False

        self._pending_order: Optional[Dict[str, Any]] = None

        self._step_count = 0
        self._last_snapshot_date: Optional[str] = None

    def _current_equity(self, price: float) -> float:
        return self.cash + self.shares * price

    def _position_dict(self, current_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if self.shares == 0:
            return None
        pos: Dict[str, Any] = {
            "symbol": self.symbol,
            "shares": self.shares,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
        }
        if current_price is not None:
            pos["unrealized_pnl"] = round((current_price - self.entry_price) * self.shares, 4)
        return pos

    def step(self, ohlcv: pd.DataFrame, snapshot_date: str) -> StepResult:
        self._step_count += 1
        warnings: List[str] = []

        if self._last_snapshot_date == snapshot_date:
            return self._no_action(snapshot_date, warnings, ohlcv)

        self._last_snapshot_date = snapshot_date

        if self._pending_order is not None:
            return self._execute_pending(ohlcv, snapshot_date, warnings)

        if self.template_type == "buy_and_hold":
            return self._step_buy_and_hold(ohlcv, snapshot_date, warnings)
        elif self.template_type == "sma_crossover":
            return self._step_sma_crossover(ohlcv, snapshot_date, warnings)
        elif self.template_type == "price_change_fixed_hold":
            return self._step_price_change_fixed_hold(ohlcv, snapshot_date, warnings)
        else:
            warnings.append(f"Unknown template_type: {self.template_type}")
            return self._no_action(snapshot_date, warnings, ohlcv)

    def force_exit(self, ohlcv: pd.DataFrame, snapshot_date: str) -> StepResult:
        warnings: List[str] = []
        self._pending_order = None

        if self.shares == 0:
            return self._no_action(snapshot_date, warnings, ohlcv)

        last_close = ohlcv.iloc[-1]["close"]
        sell_price = last_close * (1 - self.slippage_rate)
        gross = self.shares * sell_price
        commission = round(gross * self.commission_rate, 4)
        self.cash = round(self.cash + gross - commission, 4)

        detail = {
            "symbol": self.symbol,
            "side": "SELL",
            "quantity": self.shares,
            "observed_price": round(last_close, 4),
            "execution_price": round(sell_price, 4),
            "commission": commission,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
            "exit_reason": "forced_exit",
        }

        self.shares = 0
        self.entry_price = None
        self.entry_date = None
        self._holding_steps_remaining = None

        equity = self.cash
        warnings.append(f"Forced exit at {snapshot_date} close — end of available data")

        return StepResult(
            decision_type="forced_exit",
            decision_detail=detail,
            paper_cash=self.cash,
            paper_position=None,
            paper_equity=equity,
            warnings=warnings,
            snapshot_date=snapshot_date,
            data_cutoff_at=self.dataset_cutoff_at,
            engine_version=self.engine_version,
        )

    def _execute_pending(
        self, ohlcv: pd.DataFrame, snapshot_date: str, warnings: List[str]
    ) -> StepResult:
        order = self._pending_order
        self._pending_order = None

        today_open = ohlcv.iloc[-1]["open"]
        today_close = ohlcv.iloc[-1]["close"]

        if order["side"] == "BUY":
            exec_price = round(today_open * (1 + self.slippage_rate), 4)
            max_shares = int(self.cash / (exec_price * (1 + self.commission_rate)))

            if max_shares <= 0:
                warnings.append(
                    f"Blocked BUY on {snapshot_date}: insufficient cash "
                    f"(cash={self.cash:.2f}, price={exec_price:.2f})"
                )
                return self._make_result(
                    "blocked_action",
                    {
                        "blocked_side": "BUY",
                        "reason": "insufficient_cash",
                        "observed_open": round(today_open, 4),
                        "execution_price": exec_price,
                    },
                    snapshot_date,
                    warnings,
                    today_close,
                )

            gross = round(max_shares * exec_price, 4)
            commission = round(gross * self.commission_rate, 4)
            self.cash = round(self.cash - gross - commission, 4)
            self.shares = max_shares
            self.entry_price = exec_price
            self.entry_date = snapshot_date

            if self.template_type == "price_change_fixed_hold":
                holding_days = self.strategy_spec.get("signal_parameters", {}).get("holding_days", 1)
                self._holding_steps_remaining = holding_days

            detail = {
                "symbol": self.symbol,
                "side": "BUY",
                "quantity": max_shares,
                "observed_price": round(today_open, 4),
                "execution_price": exec_price,
                "slippage_amount": round(abs(exec_price - today_open) * max_shares, 4),
                "commission": commission,
                "signal_date": order.get("signal_date"),
                "rationale": order.get("rationale", ""),
            }

            equity = self._current_equity(today_close)
            return self._make_result(
                "simulated_entry", detail, snapshot_date, warnings, today_close
            )

        else:
            exec_price = round(today_open * (1 - self.slippage_rate), 4)
            gross = round(self.shares * exec_price, 4)
            commission = round(gross * self.commission_rate, 4)
            self.cash = round(self.cash + gross - commission, 4)

            detail = {
                "symbol": self.symbol,
                "side": "SELL",
                "quantity": self.shares,
                "observed_price": round(today_open, 4),
                "execution_price": exec_price,
                "slippage_amount": round(abs(today_open - exec_price) * self.shares, 4),
                "commission": commission,
                "entry_price": self.entry_price,
                "entry_date": self.entry_date,
                "signal_date": order.get("signal_date"),
                "rationale": order.get("rationale", ""),
            }

            self.shares = 0
            self.entry_price = None
            self.entry_date = None
            self._holding_steps_remaining = None

            equity = self.cash
            return self._make_result(
                "simulated_exit", detail, snapshot_date, warnings, today_close
            )

    def _step_buy_and_hold(
        self, ohlcv: pd.DataFrame, snapshot_date: str, warnings: List[str]
    ) -> StepResult:
        if not self._bought and self.shares == 0:
            today_open = ohlcv.iloc[-1]["open"]
            today_close = ohlcv.iloc[-1]["close"]
            exec_price = round(today_open * (1 + self.slippage_rate), 4)
            max_shares = int(self.cash / (exec_price * (1 + self.commission_rate)))

            if max_shares <= 0:
                warnings.append(
                    f"Blocked BUY on {snapshot_date}: insufficient cash "
                    f"(cash={self.cash:.2f}, price={exec_price:.2f})"
                )
                return self._make_result(
                    "blocked_action",
                    {
                        "blocked_side": "BUY",
                        "reason": "insufficient_cash",
                        "observed_open": round(today_open, 4),
                        "execution_price": exec_price,
                    },
                    snapshot_date,
                    warnings,
                    today_close,
                )

            gross = round(max_shares * exec_price, 4)
            commission = round(gross * self.commission_rate, 4)
            self.cash = round(self.cash - gross - commission, 4)
            self.shares = max_shares
            self.entry_price = exec_price
            self.entry_date = snapshot_date
            self._bought = True

            detail = {
                "symbol": self.symbol,
                "side": "BUY",
                "quantity": max_shares,
                "observed_price": round(today_open, 4),
                "execution_price": exec_price,
                "slippage_amount": round(abs(exec_price - today_open) * max_shares, 4),
                "commission": commission,
                "rationale": "Initial allocation at first tradable session (buy-and-hold)",
            }

            equity = self._current_equity(today_close)
            return self._make_result(
                "simulated_entry", detail, snapshot_date, warnings, today_close
            )

        return self._no_action(snapshot_date, warnings, ohlcv)

    def _step_sma_crossover(
        self, ohlcv: pd.DataFrame, snapshot_date: str, warnings: List[str]
    ) -> StepResult:
        sig_params = self.strategy_spec.get("signal_parameters", {})
        short_window = sig_params.get("short_window", 3)
        long_window = sig_params.get("long_window", 5)

        if len(ohlcv) < long_window:
            return self._no_action(
                snapshot_date,
                warnings + [f"Warming up: need {long_window} days for SMA (have {len(ohlcv)})"],
                ohlcv,
            )

        closes = ohlcv["close"].values
        n = len(closes)

        sma_short = float(sum(closes[n - short_window : n]) / short_window)
        sma_long = float(sum(closes[n - long_window : n]) / long_window)

        today_close = ohlcv.iloc[-1]["close"]

        cross_up = False
        cross_down = False
        if self._prev_sma_short is not None and self._prev_sma_long is not None:
            cross_up = self._prev_sma_short <= self._prev_sma_long and sma_short > sma_long
            cross_down = self._prev_sma_short >= self._prev_sma_long and sma_short < sma_long

        self._prev_sma_short = sma_short
        self._prev_sma_long = sma_long

        if cross_up and self.shares == 0:
            self._pending_order = {
                "side": "BUY",
                "signal_date": snapshot_date,
                "rationale": f"SMA({short_window},{long_window}) cross-up signal",
            }
            return self._no_action(
                snapshot_date,
                warnings + [f"SMA cross-up signal generated pending order for next session"],
                ohlcv,
            )

        if cross_down and self.shares > 0:
            self._pending_order = {
                "side": "SELL",
                "signal_date": snapshot_date,
                "rationale": f"SMA({short_window},{long_window}) cross-down signal",
            }
            return self._no_action(
                snapshot_date,
                warnings + [f"SMA cross-down signal generates pending order for next session"],
                ohlcv,
            )

        return self._no_action(snapshot_date, warnings, ohlcv)

    def _step_price_change_fixed_hold(
        self, ohlcv: pd.DataFrame, snapshot_date: str, warnings: List[str]
    ) -> StepResult:
        sig_params = self.strategy_spec.get("signal_parameters", {})
        lookback_days = sig_params.get("lookback_days", 3)
        threshold_pct = sig_params.get("threshold_pct", 0.05)

        if self.shares > 0 and self._holding_steps_remaining is not None:
            self._holding_steps_remaining -= 1
            if self._holding_steps_remaining <= 0:
                today_close = ohlcv.iloc[-1]["close"]
                sell_price = round(today_close * (1 - self.slippage_rate), 4)
                gross = round(self.shares * sell_price, 4)
                commission = round(gross * self.commission_rate, 4)
                self.cash = round(self.cash + gross - commission, 4)

                holding_days = sig_params.get("holding_days", 1)
                detail = {
                    "symbol": self.symbol,
                    "side": "SELL",
                    "quantity": self.shares,
                    "observed_price": round(today_close, 4),
                    "execution_price": sell_price,
                    "slippage_amount": round(abs(today_close - sell_price) * self.shares, 4),
                    "commission": commission,
                    "entry_price": self.entry_price,
                    "entry_date": self.entry_date,
                    "rationale": f"Fixed holding period exit after {holding_days} sessions",
                }

                self.shares = 0
                self.entry_price = None
                self.entry_date = None
                self._holding_steps_remaining = None

                equity = self.cash
                return self._make_result(
                    "simulated_exit", detail, snapshot_date, warnings, today_close
                )

            return self._no_action(snapshot_date, warnings, ohlcv)

        if len(ohlcv) < lookback_days + 1:
            return self._no_action(
                snapshot_date,
                warnings + [f"Warming up: need {lookback_days + 1} days for lookback (have {len(ohlcv)})"],
                ohlcv,
            )

        closes = ohlcv["close"].values
        n = len(closes)
        current_close = closes[n - 1]
        lookback_close = closes[n - 1 - lookback_days]
        pct_change = (current_close - lookback_close) / lookback_close

        if pct_change <= -threshold_pct and self.shares == 0:
            self._pending_order = {
                "side": "BUY",
                "signal_date": snapshot_date,
                "rationale": (
                    f"Price drop {pct_change * 100:.1f}% over {lookback_days} days "
                    f"(threshold: -{threshold_pct * 100:.0f}%)"
                ),
            }
            return self._no_action(
                snapshot_date,
                warnings + [f"Price drop signal generates pending order for next session"],
                ohlcv,
            )

        return self._no_action(snapshot_date, warnings, ohlcv)

    def _no_action(
        self, snapshot_date: str, warnings: List[str], ohlcv: pd.DataFrame
    ) -> StepResult:
        last_close = ohlcv.iloc[-1]["close"] if not ohlcv.empty else 0.0
        equity = self._current_equity(last_close)
        return self._make_result("no_action", None, snapshot_date, warnings, last_close)

    def _make_result(
        self,
        decision_type: str,
        detail: Optional[Dict[str, Any]],
        snapshot_date: str,
        warnings: List[str],
        current_price: float,
    ) -> StepResult:
        equity = self._current_equity(current_price)
        return StepResult(
            decision_type=decision_type,
            decision_detail=detail,
            paper_cash=self.cash,
            paper_position=self._position_dict(current_price),
            paper_equity=equity,
            warnings=warnings,
            snapshot_date=snapshot_date,
            data_cutoff_at=self.dataset_cutoff_at,
            engine_version=self.engine_version,
        )
