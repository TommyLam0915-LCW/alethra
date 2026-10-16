"""Platform transport abstraction for the execution agent.

Defines the interface between the agent and the Alethra platform API.
Tests use FakePlatformTransport; production uses HTTPPlatformTransport.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class IntentDTO:
    """Order intent from the Alethra platform."""
    intent_id: str
    user_id: str
    portfolio_id: str
    instrument_id: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    authorization_token: str
    expires_at: datetime
    idempotency_key: Optional[str] = None


@dataclass
class FillReportDTO:
    """Fill report to send to the platform."""
    intent_id: str
    quantity: int
    price: float
    fill_time: datetime
    broker_ref: str
    commission: float


class PlatformTransport(Protocol):
    """Interface for Alethra platform API operations."""

    def poll_order_intents(self) -> list[IntentDTO]: ...

    def report_submitted(self, intent_id: str, broker_ref: str, submitted_at: datetime) -> None: ...

    def report_fill(self, fill: FillReportDTO) -> None: ...

    def report_rejected(self, intent_id: str, reason: str) -> None: ...

    def report_cancelled(self, intent_id: str, reason: str) -> None: ...

    def report_heartbeat(self, connected: bool) -> None: ...

    def check_kill_switch(self) -> bool:
        """Returns True if kill switch is active."""
        ...


@dataclass
class FakePlatformConfig:
    """Configuration for FakePlatformTransport behavior."""
    kill_switch_active: bool = False
    intents: list[IntentDTO] = field(default_factory=list)
    submitted_intents: dict[str, dict] = field(default_factory=dict)
    fills_reported: dict[str, list[FillReportDTO]] = field(default_factory=dict)
    rejected_intents: dict[str, str] = field(default_factory=dict)
    cancelled_intents: dict[str, str] = field(default_factory=dict)
    heartbeat_log: list[dict] = field(default_factory=list)


class FakePlatformTransport:
    """Fake platform transport for offline testing.

    Tracks all interactions for test assertions.
    """

    def __init__(self, config: Optional[FakePlatformConfig] = None):
        self._config = config or FakePlatformConfig()
        self._poll_count = 0

    def poll_order_intents(self) -> list[IntentDTO]:
        self._poll_count += 1
        if self._poll_count == 1:
            return list(self._config.intents)
        return []

    def report_submitted(self, intent_id: str, broker_ref: str, submitted_at: datetime) -> None:
        self._config.submitted_intents[intent_id] = {
            "broker_ref": broker_ref,
            "submitted_at": submitted_at,
        }

    def report_fill(self, fill: FillReportDTO) -> None:
        if fill.intent_id not in self._config.fills_reported:
            self._config.fills_reported[fill.intent_id] = []
        self._config.fills_reported[fill.intent_id].append(fill)

    def report_rejected(self, intent_id: str, reason: str) -> None:
        self._config.rejected_intents[intent_id] = reason

    def report_cancelled(self, intent_id: str, reason: str) -> None:
        self._config.cancelled_intents[intent_id] = reason

    def report_heartbeat(self, connected: bool) -> None:
        self._config.heartbeat_log.append({
            "connected": connected,
            "timestamp": datetime.utcnow(),
        })

    def check_kill_switch(self) -> bool:
        return self._config.kill_switch_active
