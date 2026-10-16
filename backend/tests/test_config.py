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


def _prod_settings(**env_overrides):
    base = {
        "APP_ENV": "production",
        "JWT_SECRET_KEY": "x" * 48,
        "DATABASE_URL": "postgresql://user:pass@db.example.com:5432/alethra",
        "CORS_ORIGINS": "https://app.alethra.com",
    }
    base.update(env_overrides)
    return _fresh_settings(**base)


class TestDevelopmentDefaults:
    def test_development_allows_default_jwt_secret(self):
        s = _fresh_settings(APP_ENV="development")
        assert s.app_env == "development"
        assert s.jwt_secret_key == "change-me-in-production-use-a-long-random-string"

    def test_development_allows_short_jwt_secret(self):
        s = _fresh_settings(APP_ENV="development", JWT_SECRET_KEY="short")
        assert s.jwt_secret_key == "short"

    def test_development_allows_cors_wildcard(self):
        s = _fresh_settings(CORS_ORIGINS="*")
        assert s.cors_origins == "*"

    def test_development_allows_unknown_data_provider(self):
        s = _fresh_settings(DATA_PROVIDER="nonexistent")
        assert s.data_provider == "nonexistent"

    def test_development_allows_localhost_database(self):
        s = _fresh_settings(DATABASE_URL="postgresql://localhost/test")
        assert "localhost" in s.database_url

    def test_development_allows_live_payments_without_stripe(self):
        s = _fresh_settings(ENABLE_LIVE_PAYMENTS="true")
        assert s.enable_live_payments is True


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
        s = _prod_settings(JWT_SECRET_KEY=secret)
        assert s.jwt_secret_key == secret
        assert s.app_env == "production"

    def test_staging_rejects_default_jwt_secret(self):
        with pytest.raises(Exception) as exc_info:
            _fresh_settings(APP_ENV="staging")
        assert "JWT_SECRET_KEY" in str(exc_info.value)

    def test_staging_accepts_long_random_secret(self):
        secret = "b" * 48
        s = _fresh_settings(
            APP_ENV="staging",
            JWT_SECRET_KEY=secret,
            DATABASE_URL="postgresql://db.example.com/test",
        )
        assert s.app_env == "staging"


class TestCorsValidation:
    def test_production_rejects_wildcard_cors(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(CORS_ORIGINS="*")
        assert "CORS_ORIGINS" in str(exc_info.value)

    def test_production_accepts_explicit_origins(self):
        s = _prod_settings(CORS_ORIGINS="https://app.alethra.com,https://admin.alethra.com")
        assert len(s.cors_origins_list) == 2

    def test_staging_rejects_wildcard_cors(self):
        with pytest.raises(Exception) as exc_info:
            _fresh_settings(
                APP_ENV="staging",
                JWT_SECRET_KEY="x" * 48,
                DATABASE_URL="postgresql://db.example.com/test",
                CORS_ORIGINS="*",
            )
        assert "CORS_ORIGINS" in str(exc_info.value)


class TestDataProviderValidation:
    def test_production_rejects_unknown_data_provider(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(DATA_PROVIDER="nonexistent")
        assert "DATA_PROVIDER" in str(exc_info.value)
        assert "nonexistent" in str(exc_info.value)

    def test_production_accepts_demo_provider(self):
        s = _prod_settings(DATA_PROVIDER="demo")
        assert s.data_provider == "demo"

    def test_production_accepts_lseg_provider(self):
        s = _prod_settings(DATA_PROVIDER="lseg")
        assert s.data_provider == "lseg"


class TestAiProviderValidation:
    def test_production_accepts_mock_without_api_key(self):
        s = _prod_settings(AI_PROVIDER="mock", AI_API_KEY="not-used-in-phase-1")
        assert s.ai_provider == "mock"

    def test_production_rejects_non_mock_without_api_key(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(AI_PROVIDER="openai", AI_API_KEY="not-used-in-phase-1")
        assert "AI_API_KEY" in str(exc_info.value)

    def test_production_rejects_non_mock_with_empty_api_key(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(AI_PROVIDER="openai", AI_API_KEY="")
        assert "AI_API_KEY" in str(exc_info.value)

    def test_production_accepts_non_mock_with_real_api_key(self):
        s = _prod_settings(AI_PROVIDER="openai", AI_API_KEY="sk-real-key-12345")
        assert s.ai_provider == "openai"
        assert s.ai_api_key == "sk-real-key-12345"


class TestLivePaymentsValidation:
    def test_production_rejects_live_payments_without_stripe_key(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(ENABLE_LIVE_PAYMENTS="true")
        assert "STRIPE_SECRET_KEY" in str(exc_info.value)

    def test_production_rejects_live_payments_without_webhook_secret(self):
        env = {"STRIPE_SECRET_KEY": "sk_test_fake"}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(Exception) as exc_info:
                _prod_settings(ENABLE_LIVE_PAYMENTS="true")
            assert "STRIPE_WEBHOOK_SECRET" in str(exc_info.value)

    def test_production_accepts_live_payments_with_both_keys(self):
        env = {
            "STRIPE_SECRET_KEY": "sk_test_fake",
            "STRIPE_WEBHOOK_SECRET": "whsec_test_fake",
        }
        with patch.dict(os.environ, env, clear=False):
            s = _prod_settings(ENABLE_LIVE_PAYMENTS="true")
            assert s.enable_live_payments is True

    def test_production_accepts_disabled_live_payments_without_keys(self):
        s = _prod_settings(ENABLE_LIVE_PAYMENTS="false")
        assert s.enable_live_payments is False


class TestDatabaseUrlValidation:
    def test_production_rejects_localhost_database(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(DATABASE_URL="postgresql://localhost:5432/alethra")
        assert "DATABASE_URL" in str(exc_info.value)
        assert "localhost" in str(exc_info.value)

    def test_production_rejects_127_0_0_1_database(self):
        with pytest.raises(Exception) as exc_info:
            _prod_settings(DATABASE_URL="postgresql://127.0.0.1:5432/alethra")
        assert "DATABASE_URL" in str(exc_info.value)

    def test_production_accepts_remote_database(self):
        s = _prod_settings(DATABASE_URL="postgresql://user:pass@db.example.com:5432/alethra")
        assert "db.example.com" in s.database_url


class TestCorsOriginsList:
    def test_single_origin(self):
        s = _fresh_settings(CORS_ORIGINS="http://localhost:3000")
        assert s.cors_origins_list == ["http://localhost:3000"]

    def test_multiple_origins(self):
        s = _fresh_settings(CORS_ORIGINS="http://a.com, http://b.com, http://c.com")
        assert s.cors_origins_list == ["http://a.com", "http://b.com", "http://c.com"]


class TestIsProductionLike:
    def test_development_is_not_production_like(self):
        s = _fresh_settings(APP_ENV="development")
        assert s.is_production_like is False

    def test_production_is_production_like(self):
        s = _prod_settings()
        assert s.is_production_like is True

    def test_staging_is_production_like(self):
        s = _fresh_settings(
            APP_ENV="staging",
            JWT_SECRET_KEY="x" * 48,
            DATABASE_URL="postgresql://db.example.com/test",
        )
        assert s.is_production_like is True
