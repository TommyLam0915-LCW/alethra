"""Execution agent configuration.

This module defines the configuration schema for the IBKR execution agent.
Configuration is loaded from environment variables.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentConfig:
    """Configuration for the execution agent.
    
    Attributes:
        ib_gateway_host: IB Gateway host (default: localhost)
        ib_gateway_port: IB Gateway API port (default: 7497 for paper, 7496 for live)
        ib_client_id: IBKR client ID for this agent (default: 1)
        alethra_api_url: Alethra platform API endpoint
        alethra_api_key: User-specific API key for authentication
        poll_interval_seconds: How often to poll for new order intents
        reconnect_interval_seconds: How long to wait before reconnecting to IB Gateway
        max_reconnect_attempts: Maximum reconnection attempts before giving up
        trading_mode: "paper" (default) or "live"
        allow_live: Must be True to permit trading_mode="live" (live is disabled by default)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    ib_gateway_host: str = "localhost"
    ib_gateway_port: int = 7497  # Paper trading default
    ib_client_id: int = 1
    alethra_api_url: str = "https://api.alethra.com"
    alethra_api_key: Optional[str] = None
    poll_interval_seconds: int = 5
    reconnect_interval_seconds: int = 10
    max_reconnect_attempts: int = 5
    trading_mode: str = "paper"
    allow_live: bool = False
    log_level: str = "INFO"

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If required fields are missing or invalid.
        """
        if not self.alethra_api_key:
            raise ValueError("alethra_api_key is required")
        if not self.alethra_api_url:
            raise ValueError("alethra_api_url is required")
        if self.ib_gateway_port <= 0 or self.ib_gateway_port > 65535:
            raise ValueError(f"Invalid ib_gateway_port: {self.ib_gateway_port}")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.trading_mode not in ("paper", "live"):
            raise ValueError(
                f"Invalid trading_mode: {self.trading_mode!r} (expected 'paper' or 'live')"
            )
        if self.trading_mode == "live" and not self.allow_live:
            raise ValueError(
                "trading_mode='live' requires allow_live=True "
                "(live trading is disabled by default)"
            )


def load_config_from_env() -> AgentConfig:
    """Load configuration from environment variables.
    
    Environment variables:
        IB_GATEWAY_HOST: IB Gateway host (default: localhost)
        IB_GATEWAY_PORT: IB Gateway port (default: 7497)
        IB_CLIENT_ID: IBKR client ID (default: 1)
        ALETHRA_API_URL: Alethra API URL (default: https://api.alethra.com)
        ALETHRA_API_KEY: Alethra API key (required)
        POLL_INTERVAL: Poll interval in seconds (default: 5)
        TRADING_MODE: "paper" (default) or "live"
        ALLOW_LIVE_TRADING: Must be truthy to allow TRADING_MODE=live (default: disabled)
        LOG_LEVEL: Log level (default: INFO)

    Returns:
        AgentConfig instance

    Raises:
        ValueError: If required environment variables are missing.
    """
    import os

    def _env_bool(name: str) -> bool:
        return os.getenv(name, "").strip().lower() in ("1", "true", "yes")

    config = AgentConfig(
        ib_gateway_host=os.getenv("IB_GATEWAY_HOST", "localhost"),
        ib_gateway_port=int(os.getenv("IB_GATEWAY_PORT", "7497")),
        ib_client_id=int(os.getenv("IB_CLIENT_ID", "1")),
        alethra_api_url=os.getenv("ALETHRA_API_URL", "https://api.alethra.com"),
        alethra_api_key=os.getenv("ALETHRA_API_KEY"),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL", "5")),
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        allow_live=_env_bool("ALLOW_LIVE_TRADING"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
    config.validate()
    return config
