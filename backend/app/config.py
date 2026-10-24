import os

from pydantic_settings import BaseSettings
from pydantic import model_validator, ConfigDict
from functools import lru_cache

_INSECURE_JWT_DEFAULTS = {
    "change-me-in-production-use-a-long-random-string",
    "secret",
    "changeme",
    "test",
    "",
}

_MIN_JWT_SECRET_LENGTH = 32

_KNOWN_DATA_PROVIDERS = {"demo", "lseg", "twelve_data"}
_KNOWN_AI_PROVIDERS = {"mock", "openai_compatible"}


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

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("production", "staging")

    @model_validator(mode="after")
    def _validate_production_safety(self) -> "Settings":
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

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
