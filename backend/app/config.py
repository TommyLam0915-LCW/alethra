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

# The only accepted value for AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION. It is
# a development fixture, not owner consent: it exists so the ready-snapshot
# write path can be exercised against synthetic data, and the validator below
# refuses it outside a local development process.
AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION = "test-only-fixture-v1"

# Same shape for the training/inference chain: a development fixture that lets
# the chain be exercised on synthetic data. It is not owner consent and the
# validator refuses it outside a local development process.
AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION = "test-only-training-fixture-v1"

_KNOWN_DATA_PROVIDERS = {"demo", "lseg", "twelvedata"}
_KNOWN_AI_PROVIDERS = {"mock", "openai_compatible"}
_INTRADAY_INTERVAL_LABELS = {"1min", "5min", "15min", "30min"}

# The ticks one learning process knows how to schedule, in clock order. Named
# here rather than in the worker so a host that asks for a lane this build does
# not have fails while the settings are read — before a job is registered,
# before a thread exists, and before anything could fire.
LEARNING_SCHEDULER_LANES = (
    "daily_research",
    "daily_simulation",
    "weekly_analysis",
    "daily_model_estimate",
)

# What a host registers when it turns the shared scheduler on but names no lane.
# The three ticks this switch has always meant — deliberately not
# ``LEARNING_SCHEDULER_LANES``, which is the registry and grows on upgrades. A
# deployment that switched the shared scheduler on before a lane existed must
# not acquire that lane from an upgrade alone, because the two acts are not the
# same approval: one says "run the learning service", the other says "run this
# one too, and bill owners for it". ``daily_model_estimate`` is the current case
# and sits at 05:00 UTC deducting quota, so it is named here as off-by-default
# for anyone who did not ask. The rule is general: a lane added to the registry
# later stays unregistered until an operator names it.
DEFAULT_LEARNING_SCHEDULER_LANES = (
    "daily_research",
    "daily_simulation",
    "weekly_analysis",
)


class Settings(BaseSettings):
    database_url: str = "postgresql://user:pass@localhost:5432/dbname"  # Must be overridden via DATABASE_URL env var
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
    # Local owner-trial entry: a single one-time link, exchanged for an ordinary
    # session of the one account named here. It never replaces password sign-in
    # for customers and is refused outside local development.
    trial_passwordless_enabled: bool = False
    trial_passwordless_email: str = ""
    trial_passwordless_token_file: str = ""
    trial_passwordless_session_minutes: int = 720
    # Agent candidate screening is a development slice. Off by default so the
    # routes existing in a customer environment cannot collect candidates,
    # build outcomes or preview a dataset without a new explicit scope.
    agent_strategy_screening_enabled: bool = False
    # A development-only authorization fixture for the ready-snapshot write
    # path. It is not owner consent and cannot authorize a customer process:
    # it is accepted only with screening enabled, APP_ENV=development and a
    # loopback/sqlite database.
    agent_strategy_test_snapshot_authorization: str = ""
    # The same kind of fixture for training and inference. It never becomes an
    # owner-consent value and never enables a customer process.
    agent_strategy_test_training_authorization: str = ""
    # Purpose-level switch for the local owner trial. It is an operator
    # capability, not consent: the owner's own recorded row is still required,
    # so enabling this cannot manufacture a permission the owner never gave.
    agent_strategy_owner_consent_mode: bool = False
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
    # Kraken crypto lane upstream budget. Every public REST call (OHLC, Ticker,
    # Time, AssetPairs, retries, UI-triggered reads) reserves against these
    # counters before the socket opens. The verification-run cap is a named hard
    # budget for one bounded evidence run; it does not reset on restart — the run
    # id must be changed deliberately to start a new allowance.
    crypto_max_requests_per_hour: int = 60
    crypto_max_requests_per_day: int = 400
    crypto_verify_run_id: str = ""
    crypto_verify_max_requests: int = 0
    # Local-only research auth bypass for MB-14 batch execution. Off by default;
    # refused outside local development. When enabled, requests from loopback
    # skip JWT validation and use the first active user in the database.
    research_auth_bypass_enabled: bool = False
    # Auto-learning scheduler: runs daily research and weekly pattern analysis.
    # Off by default; requires explicit opt-in to consume provider quota.
    enable_learning_scheduler: bool = False
    # Which lanes the one switch above is allowed to register. Empty (the
    # default) means ``DEFAULT_LEARNING_SCHEDULER_LANES`` — the lanes this
    # switch has always meant, not every lane this build happens to know — so
    # an upgrade that adds a lane cannot add it to a running deployment.
    # Setting it is a narrowing or an explicit grant: naming a lane here cannot
    # start a lane whose own opt-in or kill switch declines it, and leaving the
    # shared switch off still registers nothing at all.
    learning_scheduler_jobs: str = ""
    # How many accounts one scheduler tick may serve, newest-bound by owner id.
    # Each served account runs the whole instrument x interval grid against the
    # venue, so an unbounded fan-out turns one opt-in flag into a quota incident
    # for every account on the host.
    learning_scheduler_max_owners: int = 25
    # Auto-collection scheduler: periodically fetches new market data and runs backtests.
    # Off by default; requires explicit opt-in to consume provider quota.
    auto_collection_enabled: bool = False
    auto_collection_interval_hours: int = 4
    # Age threshold for the research-run reclaim. The reclaim reads it to decide
    # what to list as a candidate and then refuses to act: an aged row is not
    # proof its process died, so an active run blocks its version and holds its
    # quota unit until an outcome is written. Lowering this number changes only
    # what the refusal names, not what it does.
    research_run_stale_after_seconds: int = 900
    # Skill-unit ceiling: how many paper portfolios one main account may hold
    # open at once. Enforced by the backend at every entry point that writes a
    # portfolio row (app/services/skill_units.py), not by the UI. Retiring an
    # account releases its slot, so reaching the ceiling always leaves an
    # actionable way out.
    paper_skill_unit_max_per_user: int = 10
    # Live trading daemon: real-time paper trading via Kraken WebSocket.
    # Off by default; requires explicit opt-in.
    live_trading_enabled: bool = False
    live_trading_symbols: str = "XBT/USD,ETH/USD,SOL/USD"
    live_trading_interval: int = 60
    live_trading_capital: float = 100000.0
    kraken_ws_url: str = "wss://ws.kraken.com"

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
    def learning_scheduler_job_list(self) -> list[str]:
        """The lanes this process registers; empty means the default set.

        A pure parse, so a value of only separators stays empty here and is
        refused by the validator rather than quietly becoming the default —
        someone who wrote ``","`` meant to name something.
        """
        return [lane.strip() for lane in self.learning_scheduler_jobs.split(",") if lane.strip()]

    @property
    def registered_learning_lanes(self) -> list[str]:
        """``learning_scheduler_job_list``, or the default set when nothing was named."""
        return self.learning_scheduler_job_list or list(DEFAULT_LEARNING_SCHEDULER_LANES)

    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("production", "staging")

    @model_validator(mode="after")
    def _validate_production_safety(self) -> "Settings":
        self._validate_candidate_passwordless()
        self._validate_trial_passwordless()
        self._validate_agent_strategy_screening()
        self._validate_agent_strategy_test_snapshot_authorization()
        self._validate_agent_strategy_test_training_authorization()
        self._validate_agent_strategy_owner_consent_mode()
        self._validate_research_auth_bypass()
        self._validate_intraday_bounds()
        self._validate_crypto_budget_bounds()
        self._validate_paper_skill_unit_limit()
        self._validate_learning_scheduler_fanout()
        self._validate_learning_scheduler_jobs()
        self._validate_auto_collection_interval()

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

    def _validate_crypto_budget_bounds(self) -> None:
        if self.crypto_max_requests_per_hour < 1:
            raise ValueError("CRYPTO_MAX_REQUESTS_PER_HOUR must be positive.")
        if self.crypto_max_requests_per_day < 1:
            raise ValueError("CRYPTO_MAX_REQUESTS_PER_DAY must be positive.")
        if self.crypto_max_requests_per_day < self.crypto_max_requests_per_hour:
            raise ValueError(
                "CRYPTO_MAX_REQUESTS_PER_DAY must be >= CRYPTO_MAX_REQUESTS_PER_HOUR."
            )
        if self.crypto_verify_max_requests < 0:
            raise ValueError("CRYPTO_VERIFY_MAX_REQUESTS must be zero or positive.")
        if self.crypto_verify_max_requests > 0 and not self.crypto_verify_run_id.strip():
            raise ValueError(
                "CRYPTO_VERIFY_RUN_ID is required when CRYPTO_VERIFY_MAX_REQUESTS > 0."
            )

    def _validate_paper_skill_unit_limit(self) -> None:
        if self.paper_skill_unit_max_per_user < 1:
            raise ValueError(
                "PAPER_SKILL_UNIT_MAX_PER_USER must be at least 1 (got "
                f"{self.paper_skill_unit_max_per_user}). A ceiling of 0 would "
                "refuse every account's first skill unit, and no product entry "
                "point exists to open one without it."
            )

    def _validate_learning_scheduler_fanout(self) -> None:
        if self.learning_scheduler_max_owners < 1:
            raise ValueError(
                "LEARNING_SCHEDULER_MAX_OWNERS must be at least 1 (got "
                f"{self.learning_scheduler_max_owners}). A tick that serves "
                "nobody is indistinguishable in the ledger from a host with no "
                "accounts, so the misconfiguration would read as idle instead "
                "of broken."
            )

    def _validate_learning_scheduler_jobs(self) -> None:
        unrunnable = [
            lane
            for lane in DEFAULT_LEARNING_SCHEDULER_LANES
            if lane not in LEARNING_SCHEDULER_LANES
        ]
        if unrunnable:
            raise ValueError(
                "DEFAULT_LEARNING_SCHEDULER_LANES names lanes this build cannot "
                f"schedule: {sorted(set(unrunnable))}. Every host that leaves "
                "LEARNING_SCHEDULER_JOBS unset would then fail while reading its "
                "settings, so the pair is checked rather than trusted."
            )
        if not self.learning_scheduler_jobs.strip():
            return
        if not self.learning_scheduler_job_list:
            raise ValueError(
                "LEARNING_SCHEDULER_JOBS is set but names no lane "
                f"({self.learning_scheduler_jobs!r}). Separators alone would "
                "register an empty schedule, and a host with an empty schedule "
                "reads in the ledger like a host that was never asked to run."
            )
        unknown = [
            lane
            for lane in self.learning_scheduler_job_list
            if lane not in LEARNING_SCHEDULER_LANES
        ]
        if unknown:
            raise ValueError(
                f"LEARNING_SCHEDULER_JOBS names unknown lanes: {sorted(set(unknown))}. "
                f"Known lanes: {list(LEARNING_SCHEDULER_LANES)}. A name this "
                "build cannot map is refused rather than skipped, so that "
                "silently losing the lane it was meant to isolate cannot read "
                "as a narrowed schedule."
            )

    def _validate_auto_collection_interval(self) -> None:
        if self.auto_collection_interval_hours < 1:
            raise ValueError(
                "AUTO_COLLECTION_INTERVAL_HOURS must be at least 1 (got "
                f"{self.auto_collection_interval_hours}). It is both the "
                "scheduler's wait and the size of the dedup window a tick "
                "files under; 0 would ask APScheduler for a zero-length "
                "interval and make the window key change on every call, so "
                "the service would re-study without ever recording that it "
                "was configured to deduplicate."
            )

    def _validate_agent_strategy_screening(self) -> None:
        if not self.agent_strategy_screening_enabled:
            return
        if self.is_production_like or self.app_env != "development":
            raise ValueError(
                "AGENT_STRATEGY_SCREENING_ENABLED is a local isolated research "
                f"entry and must be off when APP_ENV={self.app_env}. It is not "
                "a released customer capability."
            )
        if not is_local_database_url(self.database_url):
            raise ValueError(
                "AGENT_STRATEGY_SCREENING_ENABLED requires a sqlite or loopback "
                "DATABASE_URL; refusing to write candidates against a remote DB."
            )

    def _validate_agent_strategy_test_snapshot_authorization(self) -> None:
        self._validate_agent_strategy_test_authorization(
            "AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION",
            self.agent_strategy_test_snapshot_authorization,
            AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION,
            "refusing to freeze a dataset against a remote database.",
        )

    def _validate_agent_strategy_test_training_authorization(self) -> None:
        self._validate_agent_strategy_test_authorization(
            "AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION",
            self.agent_strategy_test_training_authorization,
            AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION,
            "refusing to train an artifact against a remote database.",
        )

    def _validate_agent_strategy_test_authorization(
        self, env_name: str, raw_value: str, fixture: str, remote_reason: str
    ) -> None:
        value = (raw_value or "").strip()
        if not value:
            return
        if value != fixture:
            raise ValueError(
                f"{env_name} accepts only the development fixture {fixture!r}; "
                "there is no owner-consent value it can impersonate."
            )
        if not self.agent_strategy_screening_enabled:
            raise ValueError(
                f"{env_name} requires AGENT_STRATEGY_SCREENING_ENABLED=true."
            )
        if self.is_production_like or self.app_env != "development":
            raise ValueError(
                f"{env_name} is a test fixture and must be off when "
                f"APP_ENV={self.app_env}; it cannot authorize a customer "
                "environment."
            )
        if not is_local_database_url(self.database_url):
            raise ValueError(
                f"{env_name} requires a sqlite or loopback DATABASE_URL; "
                f"{remote_reason}"
            )

    def _validate_agent_strategy_owner_consent_mode(self) -> None:
        if not self.agent_strategy_owner_consent_mode:
            return
        if not self.agent_strategy_screening_enabled:
            raise ValueError(
                "AGENT_STRATEGY_OWNER_CONSENT_MODE requires "
                "AGENT_STRATEGY_SCREENING_ENABLED=true."
            )
        if self.is_production_like or self.app_env != "development":
            raise ValueError(
                "AGENT_STRATEGY_OWNER_CONSENT_MODE is a local trial profile and "
                f"must be off when APP_ENV={self.app_env}; a customer deployment "
                "needs its own reviewed release."
            )
        if not is_local_database_url(self.database_url):
            raise ValueError(
                "AGENT_STRATEGY_OWNER_CONSENT_MODE requires a sqlite or loopback "
                "DATABASE_URL; refusing to run an owner trial against a remote DB."
            )

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

    def _validate_research_auth_bypass(self) -> None:
        if not self.research_auth_bypass_enabled:
            return
        if self.is_production_like or self.app_env != "development":
            raise ValueError(
                "RESEARCH_AUTH_BYPASS_ENABLED is a local testing convenience and must be off "
                f"when APP_ENV={self.app_env}. It cannot serve a public deployment."
            )
        if not is_local_database_url(self.database_url):
            raise ValueError(
                "RESEARCH_AUTH_BYPASS_ENABLED requires a sqlite or loopback DATABASE_URL; "
                "refusing to bypass auth against a remote database."
            )

    def _validate_trial_passwordless(self) -> None:
        if not self.trial_passwordless_enabled:
            return
        if self.is_production_like or self.app_env != "development":
            raise ValueError(
                "TRIAL_PASSWORDLESS_ENABLED is a local owner-trial entry and must be off "
                f"when APP_ENV={self.app_env}. It cannot serve a public deployment."
            )
        if (
            self.jwt_secret_key in _INSECURE_JWT_DEFAULTS
            or len(self.jwt_secret_key) < _MIN_JWT_SECRET_LENGTH
        ):
            raise ValueError(
                "TRIAL_PASSWORDLESS_ENABLED requires a non-default JWT_SECRET_KEY of at "
                f"least {_MIN_JWT_SECRET_LENGTH} characters; it issues real sessions."
            )
        if not is_local_database_url(self.database_url):
            raise ValueError(
                "TRIAL_PASSWORDLESS_ENABLED requires a sqlite or loopback DATABASE_URL; "
                "refusing to issue sessions against a remote database."
            )
        email = (self.trial_passwordless_email or "").strip().lower()
        if "@" not in email:
            raise ValueError(
                "TRIAL_PASSWORDLESS_EMAIL must name the one trial account that the link "
                "may open."
            )
        token_file = (self.trial_passwordless_token_file or "").strip()
        if not token_file:
            raise ValueError(
                "TRIAL_PASSWORDLESS_TOKEN_FILE is required when the link entry is enabled."
            )
        if not os.path.isfile(token_file):
            raise ValueError(
                "TRIAL_PASSWORDLESS_TOKEN_FILE must already exist before the process starts; "
                "generate it with the trial link script."
            )
        if os.stat(token_file).st_mode & 0o077:
            raise ValueError(
                "TRIAL_PASSWORDLESS_TOKEN_FILE must not be readable by group or other; "
                "chmod it to 0600."
            )
        if not 1 <= self.trial_passwordless_session_minutes <= 1440:
            raise ValueError(
                "TRIAL_PASSWORDLESS_SESSION_MINUTES must be between 1 and 1440."
            )

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
