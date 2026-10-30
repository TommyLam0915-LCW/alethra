"""The admission policy every paper lane asks before it books a trade.

There are four execution paths in this product: the direct portfolio API
(submit and fill in one request), the session lane (a human intent that waits
for the next stored bar), the rule lane (a locked strategy's signal booked at
the bar it fires on), and the offline broker intent (which never writes a paper
portfolio). Before this module existed, only the direct lane asked anything.
The session lane stamped ``risk_check_status=passed`` unconditionally, so a
paused account accepted a new entry, and the daily budget and position cap did
not exist outside one lane — switching lanes switched the policy.

The contract this module states once, for every lane:

* **A hard violation at submit is refused.** No pending order is created, so no
  executable intent exists to be filled later. The refusal is recorded where the
  lane journals decisions, with a stable code.
* **A legal order keeps its lifecycle.** The lane's own mechanics are untouched:
  the session lane still waits for the first stored bar after the decision and
  is re-checked against actual cash and holdings at that bar; the direct lane
  still fills in the request; the rule lane still books at the signal bar.
* **Exits are not entries.** A reduce or a close is never blocked by the pause,
  the allow-list, the daily entry budget or the position cap. Ownership, order
  state and held-quantity checks still apply — those protect the book, not the
  exposure budget.
* **Unknown is not passed.** When a binding cap cannot be priced, entries are
  refused rather than labelled ``passed``. At the default 100% the cap cannot
  bind a cash-funded buy, so a missing mark is not needed and is not required;
  the policy's own arithmetic is the reason, not a tolerance.
* **The counters are decision-date scoped.** Replay lanes count entries by the
  bar date the decision belongs to, never by the wall clock, so replaying a 2024
  bar in 2026 gets that bar's own budget. The direct lane counts by UTC
  submission date. Pending and cancelled entries count; a rejected submission
  does not, because it never became an intent. A same-key retry returns the
  original order — every lane checks idempotency before it evaluates the budget
  — so a retry cannot spend a second slot.

What this module deliberately does not do: reserve cash or exposure for pending
orders. A pending order holds no position and moves no cash, so it does not
count against the cap; the fill-time affordability check in ``book_fill`` is
still what stops an order the account cannot pay for. There is no reservation
ledger, and this policy must not be read as one.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    OrderSide,
    OrderStatus,
    PaperOrder,
    PaperPortfolio,
    PositionStatus,
)

DEFAULT_MAX_POSITION_PCT = 1.0
DEFAULT_MAX_DAILY_ENTRIES = 10

CODE_UNSUPPORTED_SIDE = "unsupported_side"
CODE_PORTFOLIO_PAUSED = "portfolio_paused"
CODE_PORTFOLIO_RETIRED = "portfolio_retired"
CODE_PORTFOLIO_COMPLETED = "portfolio_completed"
CODE_INSTRUMENT_NOT_ALLOWED = "instrument_not_allowed"
CODE_DAILY_ENTRY_LIMIT = "daily_entry_limit"
CODE_POSITION_LIMIT = "position_limit"
CODE_RISK_PRICE_UNAVAILABLE = "risk_price_unavailable"
CODE_QUANTITY_EXCEEDS_POSITION = "quantity_exceeds_position"


@dataclass(frozen=True)
class RiskPolicy:
    """The numbers, named once. No environment variable can change them.

    ``max_position_pct`` is a share of the book valued at decision-time prices
    (cash + open positions); it is only binding below 1.0. ``max_daily_entries``
    bounds new entries, not orders. ``allowed_instruments`` is ``None`` when the
    lane's own binding is the allow-list (the locked spec's instruments, or a
    validated catalogue id); when set it narrows every lane further.
    """

    max_position_pct: float = DEFAULT_MAX_POSITION_PCT
    max_daily_entries: int = DEFAULT_MAX_DAILY_ENTRIES
    allowed_instruments: Optional[frozenset] = None

    def facts(self) -> Dict[str, Any]:
        return {
            "max_position_pct": self.max_position_pct,
            "max_daily_entries": self.max_daily_entries,
            "allowed_instruments": (
                sorted(self.allowed_instruments)
                if self.allowed_instruments is not None
                else None
            ),
        }


DEFAULT_RISK_POLICY = RiskPolicy()


def get_policy() -> RiskPolicy:
    """The policy in force. Read per decision so a test can narrow it."""
    return DEFAULT_RISK_POLICY


@dataclass
class RiskInputs:
    """Everything a lane knows at decision time — and nothing it would guess."""

    lane: str
    side: str
    asset_id: str
    quantity: float
    portfolio_status: str
    portfolio_cash: float
    held_quantity: float = 0.0
    price: Optional[float] = None
    open_positions_value: Optional[float] = 0.0
    unpriced_assets: Tuple[str, ...] = ()
    entries_on_date: int = 0
    decision_date: Optional[str] = None


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    code: Optional[str]
    message: str
    facts: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            **self.facts,
        }


def _effective_allowed(
    policy: RiskPolicy, lane_allowed_instruments: Optional[Iterable[str]]
) -> Optional[frozenset]:
    allowed: Optional[frozenset] = None
    if policy.allowed_instruments is not None:
        allowed = frozenset(policy.allowed_instruments)
    if lane_allowed_instruments is not None:
        lane_set = frozenset(lane_allowed_instruments)
        allowed = lane_set if allowed is None else allowed & lane_set
    return allowed


def evaluate_risk(
    inputs: RiskInputs,
    *,
    policy: Optional[RiskPolicy] = None,
    lane_allowed_instruments: Optional[Iterable[str]] = None,
) -> RiskDecision:
    """Allow or refuse one intent, with the reason and the numbers behind it."""
    policy = policy or get_policy()
    side = (inputs.side or "").upper()
    facts: Dict[str, Any] = {
        "lane": inputs.lane,
        "side": side,
        "asset_id": inputs.asset_id,
        "quantity": float(inputs.quantity),
        "decision_date": inputs.decision_date,
        "entries_on_decision_date": int(inputs.entries_on_date),
        "portfolio_status": inputs.portfolio_status,
        "portfolio_cash": round(float(inputs.portfolio_cash), 4),
        "held_quantity": float(inputs.held_quantity),
        "decision_price": None if inputs.price is None else round(float(inputs.price), 4),
        "open_positions_value": (
            None
            if inputs.open_positions_value is None
            else round(float(inputs.open_positions_value), 4)
        ),
        "unpriced_assets": list(inputs.unpriced_assets),
        "policy": policy.facts(),
    }

    def refuse(code: str, message: str) -> RiskDecision:
        return RiskDecision(False, code, message, facts)

    def allow(note: str) -> RiskDecision:
        facts["note"] = note
        return RiskDecision(True, None, note, facts)

    if side not in ("BUY", "SELL"):
        return refuse(CODE_UNSUPPORTED_SIDE, f"Unsupported order side: {inputs.side!r}.")

    if inputs.portfolio_status in ("retired", "completed"):
        code = (
            CODE_PORTFOLIO_RETIRED
            if inputs.portfolio_status == "retired"
            else CODE_PORTFOLIO_COMPLETED
        )
        return refuse(
            code,
            f"This portfolio is '{inputs.portfolio_status}' and accepts no new orders.",
        )

    if side == "SELL":
        if float(inputs.quantity) - float(inputs.held_quantity) > 1e-9:
            return refuse(
                CODE_QUANTITY_EXCEEDS_POSITION,
                f"The portfolio holds {inputs.held_quantity:g} shares; it cannot sell "
                f"{inputs.quantity:g}.",
            )
        return allow(
            "Exits are exempt from the entry pause, the allowlist, the daily entry "
            "budget and the position cap."
        )

    if inputs.portfolio_status == "paused":
        return refuse(
            CODE_PORTFOLIO_PAUSED,
            "This portfolio is paused and accepts no new entries; exits are unaffected. "
            "Resume the portfolio and submit again.",
        )

    allowed = _effective_allowed(policy, lane_allowed_instruments)
    if allowed is not None and inputs.asset_id not in allowed:
        return refuse(
            CODE_INSTRUMENT_NOT_ALLOWED,
            f"{inputs.asset_id} is not on the risk allowlist, so no order was created.",
        )

    if int(inputs.entries_on_date) >= policy.max_daily_entries:
        return refuse(
            CODE_DAILY_ENTRY_LIMIT,
            f"The entry limit of {policy.max_daily_entries} for "
            f"{inputs.decision_date or 'this trading day'} has been reached; exits and "
            "cancellations are unaffected.",
        )

    # Below 100% the cap can bind, and binding it needs a valuation. At 100% a
    # cash-funded buy can never exceed cash + positions, so a missing mark is not
    # a reason to refuse a legal trade.
    if policy.max_position_pct < 1.0:
        if inputs.price is None or inputs.open_positions_value is None:
            return refuse(
                CODE_RISK_PRICE_UNAVAILABLE,
                "No price was available at decision time, so the position cap could not "
                "be computed and no new entry order was created.",
            )
        order_value = float(inputs.quantity) * float(inputs.price)
        book_value = float(inputs.portfolio_cash) + float(inputs.open_positions_value)
        ratio = (order_value / book_value) if book_value > 0 else float("inf")
        facts["order_value"] = round(order_value, 4)
        facts["portfolio_value"] = round(book_value, 4)
        facts["position_ratio"] = None if ratio == float("inf") else round(ratio, 6)
        if order_value > policy.max_position_pct * book_value + 1e-9:
            return refuse(
                CODE_POSITION_LIMIT,
                f"This buy of about {order_value:,.2f} would put a single position at "
                f"{ratio:.2%} of portfolio value, above the "
                f"{policy.max_position_pct * 100:g}% cap.",
            )

    return allow(
        "Entry checks passed; cash and holdings are re-checked at fill time."
    )


def count_entries(
    db: Session, portfolio: PaperPortfolio, *, market_date: str
) -> int:
    """Entries already decided on one replay date, counted from the rows.

    A pending entry counts (it is an intent the account holds), a cancelled one
    counts (the budget was spent), a rejected one does not (it never became an
    intent). One query, so every lane counts the same rows the same way.
    """
    return int(
        db.query(func.count(PaperOrder.id))
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.side == OrderSide.BUY,
            PaperOrder.status != OrderStatus.rejected,
            PaperOrder.data_cutoff_at == market_date,
        )
        .scalar()
        or 0
    )


def count_entries_on_utc_date(
    db: Session, portfolio: PaperPortfolio, *, day: date
) -> int:
    """Entries submitted on one UTC date, for the direct lane.

    Timestamps in this schema are naive UTC, so the boundary is the UTC
    midnight — not the host's local midnight, which would shift the budget by the
    server's timezone offset.
    """
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    return int(
        db.query(func.count(PaperOrder.id))
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.side == OrderSide.BUY,
            PaperOrder.status != OrderStatus.rejected,
            PaperOrder.created_at >= start,
            PaperOrder.created_at < end,
        )
        .scalar()
        or 0
    )


def value_open_positions(
    db: Session,
    portfolio: PaperPortfolio,
    *,
    price_by_asset: Dict[str, Optional[float]],
    on_or_before: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> Tuple[float, Tuple[str, ...]]:
    """Cash value of open positions at decision-time prices.

    ``price_by_asset`` wins where the lane already knows the price it decided
    against. Any other open position is marked from the newest stored bar on or
    before ``on_or_before`` — the same dataset the replay walks — and an asset
    with no such bar is returned as unpriced rather than valued at zero. The
    caller passes ``None`` as the total when anything is unpriced, so a binding
    cap fails closed instead of passing on a fabricated valuation.
    """
    total = 0.0
    unpriced: List[str] = []
    for position in portfolio.positions:
        if position.status != PositionStatus.open:
            continue
        quantity = float(position.quantity or 0)
        if quantity <= 0:
            continue
        price = price_by_asset.get(position.asset_id)
        if price is None and on_or_before is not None:
            query = db.query(MarketDataBar).filter(
                MarketDataBar.instrument_id == position.asset_id,
                MarketDataBar.bar_date <= date.fromisoformat(on_or_before),
            )
            if provider_id:
                query = query.filter(MarketDataBar.provider_id == provider_id)
            bar = query.order_by(MarketDataBar.bar_date.desc()).first()
            price = float(bar.close) if bar is not None else None
        if price is None:
            unpriced.append(position.asset_id)
            continue
        total += quantity * float(price)
    return round(total, 4), tuple(sorted(set(unpriced)))
