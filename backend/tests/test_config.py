import os
import pytest
from unittest.mock import patch


def _fresh_settings(**env_overrides):
    env = {
        "APP_ENV": "development",
        "JWT_SECRET_KEY": "change-me-in-production-use-a-long-random-string",
    }
    env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        from app.config import Settings
        return Settings(_env_file=None)


class TestDevelopmentDefaults:
    def test_development_allows_default_jwt_secret(self):
        s = _fresh_settings(APP_ENV="development")
        assert s.app_env == "development"
        assert s.jwt_secret_key == "change-me-in-production-use-a-long-random-string"

    def test_development_allows_short_jwt_secret(self):
        s = _fresh_settings(APP_ENV="development", JWT_SECRET_KEY="short")
        assert s.jwt_secret_key == "short"


class TestProductionSafety:
    def test_production_rejects_default_jwt_secret(self):
        with pytest.raises(Exception) as exc_info:
            _fresh_settings(APP_ENV="production")
        assert "JWT_SECRET_KEY" in str(exc_info.value)

    def test_production_rejects_well_known_defaults(self):
        for secret in ("secret", "changeme", "test", ""):
            with pytest.raises(Exception) as exc_info:
                _fresh_settings(APP_ENV="production", JWT_SECRET_KEY=secret)
            assert "JWT_SECRET_KEY" in str(exc_info.value)

    def test_production_rejects_short_secret(self):
        with pytest.raises(Exception) as exc_info:
            _fresh_settings(APP_ENV="production", JWT_SECRET_KEY="a" * 31)
        assert "at least 32" in str(exc_info.value)

    def test_production_accepts_long_random_secret(self):
        secret = "a" * 32
        s = _fresh_settings(APP_ENV="production", JWT_SECRET_KEY=secret)
        assert s.jwt_secret_key == secret
        assert s.app_env == "production"

    def test_staging_rejects_default_jwt_secret(self):
        with pytest.raises(Exception) as exc_info:
            _fresh_settings(APP_ENV="staging")
        assert "JWT_SECRET_KEY" in str(exc_info.value)

    def test_staging_accepts_long_random_secret(self):
        secret = "b" * 48
        s = _fresh_settings(APP_ENV="staging", JWT_SECRET_KEY=secret)
        assert s.app_env == "staging"


class TestCorsOriginsList:
    def test_single_origin(self):
        s = _fresh_settings(CORS_ORIGINS="http://localhost:3000")
        assert s.cors_origins_list == ["http://localhost:3000"]

    def test_multiple_origins(self):
        s = _fresh_settings(CORS_ORIGINS="http://a.com, http://b.com, http://c.com")
        assert s.cors_origins_list == ["http://a.com", "http://b.com", "http://c.com"]
