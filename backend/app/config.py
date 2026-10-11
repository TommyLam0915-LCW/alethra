from pydantic_settings import BaseSettings
from functools import lru_cache


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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
