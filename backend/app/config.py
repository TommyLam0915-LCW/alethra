from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache

_INSECURE_JWT_DEFAULTS = {
    "change-me-in-production-use-a-long-random-string",
    "secret",
    "changeme",
    "test",
    "",
}

_MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/ai_investment_lab"
    jwt_secret_key: str = "change-me-in-production-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440
    ai_provider: str = "mock"
    ai_model: str = "mock-model"
    ai_api_key: str = "not-used-in-phase-1"
    data_provider: str = "demo"
    app_env: str = "development"
    enable_live_payments: bool = False
    enable_public_sharing: bool = False
    enable_external_notifications: bool = False
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @model_validator(mode="after")
    def _validate_production_safety(self) -> "Settings":
        if self.app_env in ("production", "staging"):
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
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
