"""Execution agent configuration.

Defines the configuration schema for the execution agent. Configuration is
loaded from environment variables.

SAFETY: Live trading mode requires explicit ALETHRA_LIVE_TRADING_ENABLED=true.
Without this flag, mode defaults to "paper" and any attempt to set mode="live"
raises ValueError.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentConfig:
    """Configuration for the execution agent.

    Attributes:
        mode: "paper" or "live". Live requires live_trading_enabled=True.
        ib_gateway_host: IB Gateway host (default: localhost)
        ib_gateway_port: IB Gateway API port (default: 7497 paper, 7496 live)
        ib_client_id: IBKR client ID for this agent (default: 1)
        alethra_api_url: Alethra platform API endpoint
        alethra_api_key: User-specific API key for authentication
        poll_interval_seconds: How often to poll for new order intents
        reconnect_interval_seconds: How long to wait before reconnecting
        max_reconnect_attempts: Maximum reconnection attempts before giving up
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    mode: str = "paper"
    ib_gateway_host: str = "localhost"
    ib_gateway_port: int = 7497
    ib_client_id: int = 1
    alethra_api_url: str = "https://api.alethra.com"
    alethra_api_key: Optional[str] = None
    poll_interval_seconds: int = 5
    reconnect_interval_seconds: int = 10
    max_reconnect_attempts: int = 5
    log_level: str = "INFO"

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        if self.mode not in ("paper", "live"):
            raise ValueError(f"mode must be 'paper' or 'live', got '{self.mode}'")
        if self.mode == "live" and not _live_trading_enabled():
            raise ValueError(
                "Live trading mode requires ALETHRA_LIVE_TRADING_ENABLED=true. "
                "This is a safety guard — set the environment variable to acknowledge."
            )
        if not self.alethra_api_key:
            raise ValueError("alethra_api_key is required")
        if not self.alethra_api_url:
            raise ValueError("alethra_api_url is required")
        if self.ib_gateway_port <= 0 or self.ib_gateway_port > 65535:
            raise ValueError(f"Invalid ib_gateway_port: {self.ib_gateway_port}")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")


def _live_trading_enabled() -> bool:
    return os.getenv("ALETHRA_LIVE_TRADING_ENABLED", "").lower() == "true"


def load_config_from_env() -> AgentConfig:
    """Load configuration from environment variables.

    Environment variables:
        MODE: "paper" or "live" (default: paper)
        IB_GATEWAY_HOST: IB Gateway host (default: localhost)
        IB_GATEWAY_PORT: IB Gateway port (default: 7497)
        IB_CLIENT_ID: IBKR client ID (default: 1)
        ALETHRA_API_URL: Alethra API URL (default: https://api.alethra.com)
        ALETHRA_API_KEY: Alethra API key (required)
        POLL_INTERVAL: Poll interval in seconds (default: 5)
        LOG_LEVEL: Log level (default: INFO)

    Returns:
        AgentConfig instance

    Raises:
        ValueError: If required environment variables are missing or invalid.
    """
    config = AgentConfig(
        mode=os.getenv("MODE", "paper"),
        ib_gateway_host=os.getenv("IB_GATEWAY_HOST", "localhost"),
        ib_gateway_port=int(os.getenv("IB_GATEWAY_PORT", "7497")),
        ib_client_id=int(os.getenv("IB_CLIENT_ID", "1")),
        alethra_api_url=os.getenv("ALETHRA_API_URL", "https://api.alethra.com"),
        alethra_api_key=os.getenv("ALETHRA_API_KEY"),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL", "5")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
    config.validate()
    return config
