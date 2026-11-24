"""Custom exceptions for paper trading service."""


class PaperTradingError(Exception):
    """Base exception for paper trading errors."""
    pass


class AuthorizationError(PaperTradingError):
    """Raised when user attempts to operate on resources they don't own."""
    
    def __init__(self, user_id: str, resource_type: str, resource_id: str, owner_id: str):
        self.user_id = user_id
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.owner_id = owner_id
        message = (
            f"Authorization failed: user {user_id} cannot operate on "
            f"{resource_type} {resource_id} (owned by {owner_id})"
        )
        super().__init__(message)


class ResourceNotFoundError(PaperTradingError):
    """Raised when a requested resource does not exist."""
    
    def __init__(self, resource_type: str, resource_id: str):
        self.resource_type = resource_type
        self.resource_id = resource_id
        message = f"{resource_type} {resource_id} not found"
        super().__init__(message)


class InvalidStateError(PaperTradingError):
    """Raised when an operation is attempted in an invalid state."""
    
    def __init__(self, resource_type: str, resource_id: str, current_state: str, required_states: list[str]):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.current_state = current_state
        self.required_states = required_states
        message = (
            f"{resource_type} {resource_id} is in state '{current_state}', "
            f"required states: {required_states}"
        )
        super().__init__(message)


class UpstreamLineageDrift(PaperTradingError):
    """Raised when the upstream rows a replay is bound to no longer match.

    A run that has recorded ``source_lineage`` is bound to that exact
    population. If a pinned trade has gone, or its recorded price, side,
    quantity or P&L no longer hashes to the digest on record, replaying the
    rows that happen to match today's query would produce a ledger whose
    blood-line names a different dataset. Refusing is the honest answer; the
    counts and digests travel with the message so the gap is diagnosable.

    The descriptive key is named ``explanation``, not ``reason``: at the HTTP
    layer this codebase reserves ``detail["reason"]`` for the machine-readable
    code, and a handler that spreads this dictionary over it would silently
    replace one with the other.
    """

    def __init__(self, run_id: str, detail: dict):
        self.run_id = run_id
        self.detail = detail
        message = (
            f"Run {run_id} is bound to {detail.get('recorded_trade_count')} upstream "
            f"trades at digest {detail.get('recorded_digest')}, but that population no "
            f"longer reads back the same: {detail.get('explanation')}"
        )
        super().__init__(message)
