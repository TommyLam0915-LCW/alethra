"""The skill-unit ceiling, enforced by the backend at every entry point.

A skill unit (技能單位) is a paper portfolio: it is the row that carries its own
virtual cash and, through ``portfolio_id``, its positions, orders, fills and
decisions. The product contract caps how many of these one main account may hold
open, so the cap is checked before any row is written — a UI that merely hides
the button is not a limit, and a limit one entry point enforces while another
bypasses is not a limit either.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.lib.uuid_spelling import stored_as
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.user import User

# Retiring or completing an account releases its slot. Pausing does not: the
# account still holds cash, open positions and pending orders, so it is still
# one of the skill units.
SLOT_HOLDING_STATUSES = (PortfolioStatus.active, PortfolioStatus.paused)

# Named in the refusal so a caller is never left with a ceiling and no door out.
# Retirement is terminal and keeps the journal; see retire_portfolio.
RELEASE_ENTRYPOINT = "POST /api/v1/paper-portfolio/{portfolio_id}/retire"


class SkillUnitLimitReached(Exception):
    """Raised at the entry point when the account already holds the ceiling."""

    def __init__(self, limit: int, held: int):
        self.limit = limit
        self.held = held
        super().__init__(
            f"{held} of {limit} skill-unit slots are already held open"
        )

    def detail(self) -> dict:
        """Machine-readable refusal body shared by every entry point."""
        return {
            "error": "skill_unit_limit_reached",
            "limit": self.limit,
            "held_skill_units": self.held,
            "release_entrypoint": RELEASE_ENTRYPOINT,
            "message": (
                f"This account already holds {self.held} of {self.limit} open "
                "skill units. Retire one — it is terminal, keeps its cash, "
                "positions, orders and decisions for the journal, and releases "
                "its slot — then create again."
            ),
        }


def held_slot_count(db: Session, owner_id) -> int:
    """How many accounts this main account currently holds open.

    The owner column is asked in both spellings because the count is a ceiling:
    accounts written by ``POST /api/v1/paper-trading/create`` and the daemon
    store the dashed form, and an identity-key count that cannot see them admits
    more slots than the contract allows. See ``app/lib/uuid_spelling.py``.
    """
    if not isinstance(owner_id, UUID):
        owner_id = UUID(str(owner_id))
    return (
        db.query(func.count(PaperPortfolio.id))
        .filter(
            stored_as(PaperPortfolio.owner_id, owner_id),
            PaperPortfolio.status.in_(SLOT_HOLDING_STATUSES),
        )
        .scalar()
    )


def assert_slot_available(
    db: Session, owner_id, limit: Optional[int] = None
) -> int:
    """Return the slots held, after refusing if the ceiling is already reached.

    Every lane that writes a ``paper_portfolios`` row calls this first —
    ``POST /paper-portfolio``, ``POST /paper-trading/create`` and the live
    trading daemon — so the three entry points cannot drift apart.

    The account's own ``users`` row is locked before the count is taken, because
    that is the only row guaranteed to exist for an account holding zero skill
    units: two concurrent first creates would otherwise both count nothing and
    both insert. ``reserve_quota`` serializes the same way for the same reason.

    Locking the user row and then counting with a plain read also keeps this
    transaction from requesting a second lock after it holds one. The paper
    order paths lock a portfolio row and may later reach the user row through
    entitlements; a guard that locked portfolios first would sit in exactly the
    cycle that turns one of the two into an aborted transaction.

    SQLite takes no row locks, so on that lane this proves the sequential case
    only; the concurrent case is a PostgreSQL measurement.
    """
    if not isinstance(owner_id, UUID):
        owner_id = UUID(str(owner_id))
    if limit is None:
        limit = get_settings().paper_skill_unit_max_per_user

    db.query(User).filter(User.id == owner_id).with_for_update().first()

    held = held_slot_count(db, owner_id)
    if held >= limit:
        raise SkillUnitLimitReached(limit=limit, held=held)
    return held
