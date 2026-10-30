import os

from pydantic_settings import BaseSettings
from pydantic import model_validator, ConfigDict
from functools import lru_cache

from app.auth.candidate_access import is_local_database_url, parse_account_allowlist

_INSECURE_JWT_DEFAULTS = {
    "change-me-in-production-use-a-long-random-string",
    "secret",
    "changeme",
    "test",
    "",
}

_MIN_JWT_SECRET_LENGTH = 32

_KNOWN_DATA_PROVIDERS = {"demo", "lseg", "twelvedata"}
_KNOWN_AI_PROVIDERS = {"mock", "openai_compatible"}
_INTRADAY_INTERVAL_LABELS = {"1min", "5min", "15min", "30min"}


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ai_investment_lab"
    jwt_secret_key: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440
    ai_provider: str = "mock"
    ai_model: str = "mock-model"
    ai_api_key: str = "not-used-in-phase-1"
    ai_base_url: str = ""  # empty = provider default (OpenAI API)
    ai_timeout_seconds: float = 30.0
    ai_max_output_tokens: int = 1024
    ai_cost_per_1k_tokens: float = 0.0  # USD; 0 = not priced
    data_provider: str = "demo"
    app_env: str = "development"
    enable_live_payments: bool = False
    enable_public_sharing: bool = False
    enable_external_notifications: bool = False
    cors_origins: str = "http://localhost:3000"
    redis_url: str = ""
    api_key_rate_limit_requests: int = 60
    api_key_rate_limit_window_seconds: int = 60
    # Local candidate trial entry: issues a short-lived JWT for one synthetic
    # allowlisted account. Off by default; refused outside local development.
    candidate_passwordless_mode: bool = False
    candidate_passwordless_accounts: str = ""
    candidate_passwordless_session_minutes: int = 30
    # Bounded intraday REST updates. Off by default: enabling it spends real
    # provider quota, and a real request also requires the interval to be
    # listed in intraday_verified_intervals after it was confirmed against the
    # actual account. Adapter support alone is never treated as permission.
    intraday_enabled: bool = False
    intraday_default_interval: str = "1min"
    intraday_refresh_seconds: int = 60
    intraday_outputsize: int = 390
    intraday_instruments: str = "US:AAPL"
    intraday_verified_intervals: str = ""
    intraday_max_requests_per_hour: int = 60
    intraday_max_requests_per_day: int = 400
    intraday_backoff_base_seconds: int = 30
    intraday_backoff_max_seconds: int = 300

    @property
    def intraday_instrument_list(self) -> list[str]:
        return [
            item.strip().upper()
            for item in self.intraday_instruments.split(",")
            if item.strip()
        ]

    @property
    def intraday_verified_interval_list(self) -> list[str]:
        return [
            item.strip().lower()
            for item in self.intraday_verified_intervals.split(",")
            if item.strip()
        ]

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("production", "staging")

    @model_validator(mode="after")
    def _validate_production_safety(self) -> "Settings":
        self._validate_candidate_passwordless()
        self._validate_intraday_bounds()

        if not self.is_production_like:
            return self

        if self.jwt_secret_key in _INSECURE_JWT_DEFAULTS:
            raise ValueError(
                f"JWT_SECRET_KEY must not be a well-known default when APP_ENV={self.app_env}. "
                "Set a long, random secret (minimum 32 characters)."
            )
        if len(self.jwt_secret_key) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {_MIN_JWT_SECRET_LENGTH} characters "
                f"when APP_ENV={self.app_env} (got {len(self.jwt_secret_key)})."
            )

        if self.cors_origins.strip() == "*":
            raise ValueError(
                f"CORS_ORIGINS must not be '*' when APP_ENV={self.app_env}. "
                "Specify explicit allowed origins."
            )

        if self.data_provider not in _KNOWN_DATA_PROVIDERS:
            raise ValueError(
                f"DATA_PROVIDER={self.data_provider!r} is not a known provider. "
                f"Known providers: {sorted(_KNOWN_DATA_PROVIDERS)}"
            )

        if self.ai_provider not in _KNOWN_AI_PROVIDERS:
            api_key = self.ai_api_key.strip()
            if not api_key or api_key in ("not-used-in-phase-1", ""):
                raise ValueError(
                    f"AI_API_KEY is required when AI_PROVIDER={self.ai_provider!r} "
                    f"and APP_ENV={self.app_env}."
                )

        if self.enable_live_payments:
            if not os.environ.get("STRIPE_SECRET_KEY"):
                raise ValueError(
                    "ENABLE_LIVE_PAYMENTS=true requires STRIPE_SECRET_KEY to be set."
                )
            if not os.environ.get("STRIPE_WEBHOOK_SECRET"):
                raise ValueError(
                    "ENABLE_LIVE_PAYMENTS=true requires STRIPE_WEBHOOK_SECRET to be set."
                )

        if "localhost" in self.database_url or "127.0.0.1" in self.database_url:
            raise ValueError(
                f"DATABASE_URL must not point to localhost when APP_ENV={self.app_env}. "
                "Use a managed database host."
            )

        return self

    def _validate_intraday_bounds(self) -> None:
        if not 1 <= self.intraday_outputsize <= 5000:
            raise ValueError("INTRADAY_OUTPUTSIZE must be between 1 and 5000.")
        if self.intraday_refresh_seconds < 1:
            raise ValueError("INTRADAY_REFRESH_SECONDS must be positive.")
        if self.intraday_max_requests_per_hour < 1:
            raise ValueError("INTRADAY_MAX_REQUESTS_PER_HOUR must be positive.")
        if self.intraday_max_requests_per_day < 1:
            raise ValueError("INTRADAY_MAX_REQUESTS_PER_DAY must be positive.")
        if self.intraday_max_requests_per_day < self.intraday_max_requests_per_hour:
            raise ValueError(
                "INTRADAY_MAX_REQUESTS_PER_DAY must be >= INTRADAY_MAX_REQUESTS_PER_HOUR."
            )
        if self.intraday_backoff_max_seconds < self.intraday_backoff_base_seconds:
            raise ValueError(
                "INTRADAY_BACKOFF_MAX_SECONDS must be >= INTRADAY_BACKOFF_BASE_SECONDS."
            )
        if self.intraday_default_interval not in _INTRADAY_INTERVAL_LABELS:
            raise ValueError(
                f"INTRADAY_DEFAULT_INTERVAL must be one of "
                f"{sorted(_INTRADAY_INTERVAL_LABELS)}."
            )

    def _validate_candidate_passwordless(self) -> None:
        if not self.candidate_passwordless_mode:
            return
        if self.is_production_like or self.app_env != "development":
            raise ValueError(
                "CANDIDATE_PASSWORDLESS_MODE is a local candidate trial entry and must be "
                f"off when APP_ENV={self.app_env}. It cannot serve a public deployment."
            )
        if (
            self.jwt_secret_key in _INSECURE_JWT_DEFAULTS
            or len(self.jwt_secret_key) < _MIN_JWT_SECRET_LENGTH
        ):
            raise ValueError(
                "CANDIDATE_PASSWORDLESS_MODE requires a non-default JWT_SECRET_KEY of at "
                f"least {_MIN_JWT_SECRET_LENGTH} characters; it issues real sessions."
            )
        if not is_local_database_url(self.database_url):
            raise ValueError(
                "CANDIDATE_PASSWORDLESS_MODE requires a sqlite or loopback DATABASE_URL; "
                "refusing to issue sessions against a remote database."
            )
        try:
            parse_account_allowlist(self.candidate_passwordless_accounts)
        except ValueError as exc:
            raise ValueError(f"CANDIDATE_PASSWORDLESS_ACCOUNTS is invalid: {exc}") from exc
        if not 1 <= self.candidate_passwordless_session_minutes <= 120:
            raise ValueError(
                "CANDIDATE_PASSWORDLESS_SESSION_MINUTES must be between 1 and 120."
            )

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
