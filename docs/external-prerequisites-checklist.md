# External Prerequisites Checklist

This document lists all external dependencies required to complete the six real product integrations for Alethra. Items marked **BLOCKED** cannot proceed until the prerequisite is satisfied.

## Status Labels

- **NOT IMPLEMENTED**: Code structure exists but no implementation
- **PARTIAL**: Partial implementation, missing critical components
- **IMPLEMENTED—OFFLINE VERIFIED**: Implemented and tested with mock/simulated data
- **IMPLEMENTED—EXTERNAL SANDBOX VERIFIED**: Tested against provider's sandbox environment
- **LIVE VERIFIED**: Tested against production environment
- **BLOCKED**: Cannot proceed without external prerequisite

---

## 1. Market Data Provider (LSEG Real-Time)

### Prerequisites

#### 1.1 LSEG Developer Account
- **Status**: BLOCKED
- **Action Required**: Register for LSEG Developer Portal
- **URL**: https://developers.lseg.com
- **Estimated Cost**: Free tier available; production pricing varies by data scope
- **Timeline**: 1-3 business days for approval

#### 1.2 API Credentials
- **Status**: BLOCKED
- **Action Required**: Generate API key and client credentials
- **Deliverables**:
  - Client ID
  - Client Secret
  - API Key (if applicable)
- **Storage**: Store in environment variables or secrets manager (not in code)

#### 1.3 Data Subscription Selection
- **Status**: BLOCKED
- **Action Required**: Select data subscription tier
- **Options**:
  - Real-Time US Equities (NYSE/NASDAQ)
  - Real-Time HK Equities (HKEX)
  - Historical data access
- **Estimated Cost**:
  - US real-time: ~$50-200/month depending on scope
  - HK real-time: ~$30-100/month
  - Historical: varies by depth and duration
- **Decision Needed**: Confirm US + HK scope; no derivatives

#### 1.4 Entitlement Verification
- **Status**: BLOCKED
- **Action Required**: Verify entitlements for:
  - Real-time quotes (L1/L2)
  - OHLCV bars (1-minute, daily)
  - Company actions (dividends, splits)
  - Instrument metadata

### Implementation Status

- **Provider-independent instrument identity**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/providers/instruments.py`
  - 26 unit tests passing
  - Supports US (NYSE/NASDAQ) and HK (HKEX) markets
  - Explicit market structure (sessions, holidays, timezones, lot sizes)

- **DataProvider interface upgrade**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/providers/data_provider.py`
  - `AssetInfo` now supports optional `Instrument` field
  - Backward compatible with existing demo provider
  - 60 tests passing (34 provider + 26 instrument)

- **Real instrument catalog**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/data/real_instruments.py`
  - 19 instruments defined (7 US stocks, 3 US ETFs, 7 HK stocks, 2 HK ETFs)
  - Provider symbol mappings for LSEG (RIC format) and IBKR (contract spec)

- **LSEG provider adapter**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/providers/lseg_provider.py`
  - Full DataProvider implementation: `get_bars`, `get_latest_bar`, `list_assets`, `compute_checksum`
  - RIC symbol mapping (US: .O/.N, HK: .HK)
  - OAuth2 client credentials flow with automatic token refresh
  - Also supports static API key mode
  - Auto-registration in provider registry when credentials present
  - **BLOCKED on live data**: Requires LSEG API credentials for actual data fetches
  - 32 offline contract tests passing (adapter + OAuth2 flow)

### Integration Checklist

- [ ] Register LSEG Developer account
- [ ] Generate API credentials
- [ ] Select data subscription (US + HK equities)
- [ ] Verify entitlements (real-time quotes, OHLCV, company actions)
- [ ] Store credentials securely (env vars or secrets manager)
- [ ] Implement `LsegDataProvider` class
- [ ] Implement RIC symbol mapping (US: `.O` for NASDAQ, `.N` for NYSE; HK: `.HK`)
- [ ] Implement connection, reconnect, and error-state interfaces
- [ ] Test against LSEG sandbox (if available)
- [ ] Test against production with real data
- [ ] Update provider registry to support `DATA_PROVIDER=lseg`
- [ ] Add integration tests for LSEG provider
- [ ] Document LSEG-specific configuration in deployment guide

---

## 2. Broker Integration (Interactive Brokers)

### Architecture Decision

**User-side execution agent**: Alethra does NOT connect directly to IBKR. Instead:
- User runs IB Gateway or TWS on their local machine
- User runs Alethra Execution Agent (local Python process)
- Agent connects to local IB Gateway/TWS via API
- Agent receives order intents from Alethra platform via authenticated API
- Agent executes orders and reports fills back to Alethra
- **Alethra never stores user broker credentials**

### Prerequisites

#### 2.1 IBKR Account
- **Status**: BLOCKED
- **Action Required**: User must have IBKR brokerage account
- **Account Types**:
  - Paper trading account (for testing) — recommended first
  - Live trading account (for production)
- **URL**: https://www.interactivebrokers.com

#### 2.2 IB Gateway or TWS Installation
- **Status**: BLOCKED
- **Action Required**: User must install IB Gateway or TWS
- **Options**:
  - **IB Gateway**: Lightweight, headless, recommended for production
  - **TWS (Trader Workstation)**: Full GUI, useful for debugging
- **Download**: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
- **Configuration**:
  - Enable API connections (Edit → Global Configuration → API → Settings)
  - Set API port (default: 7496 for live, 7497 for paper)
  - Allow connections from localhost only (security)

#### 2.3 IBKR API Access
- **Status**: BLOCKED
- **Action Required**: User must enable API access
- **Settings**:
  - Enable "Enable ActiveX and Socket Clients"
  - Set socket port (default: 7496/7497)
  - Optionally set "Allow connections from localhost only"
- **Credentials**: User provides API port to Alethra Execution Agent (not account credentials)

#### 2.4 Market Data Subscriptions
- **Status**: BLOCKED
- **Action Required**: User must subscribe to market data in IBKR
- **Note**: User's IBKR data subscription is for their own use, NOT for Alethra platform
- **Alethra platform uses LSEG for shared market data feed**
- **User's IBKR data is for execution validation only**

### Implementation Status

- **Execution agent architecture**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/execution_agent/`
  - Components:
    - `agent.py`: Main agent loop with signal handling, reconnection, reconciliation
    - `ibkr_client.py`: Thin facade over pluggable BrokerTransport
    - `broker_transport.py`: Abstract interface + FakeBrokerTransport (4 fill modes)
    - `alethra_client.py`: HTTP client for agent-to-platform communication
    - `config.py`: Configuration with validation (env var loading)
  - **Missing**: `IBKRBrokerTransport` — real IBKR transport (requires ib_insync + IB Gateway)
  - 34 tests passing (transport, client, service, API, agent control flow)

- **Order intent model**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/models/execution.py`
  - `OrderIntent` with `intent_id`, `symbol`, `side`, `quantity`, `status`
  - Status flow: `pending` → `authorized` → `submitted` → `filled`/`cancelled`/`rejected`
  - Idempotency key support
  - Fill tracking with `FillRecord` and `FillReconciliation`

- **Order intent API**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/services/execution.py`
  - `create_order_intent`, `authorize_intent`, `submit_order`, `record_fill`
  - Kill switch integration
  - Rate limiting integration

- **IBKR execution agent**: IMPLEMENTED—OFFLINE VERIFIED (missing real IBKR transport)
  - **BLOCKED**: Requires user to have IBKR account and IB Gateway installed
  - Agent loop, transport abstraction, and fake transport all implemented and tested
  - Missing: `IBKRBrokerTransport` using `ib_insync` library for real IBKR connection

### Integration Checklist

- [ ] User creates IBKR paper trading account
- [ ] User installs IB Gateway (or TWS for debugging)
- [ ] User enables API access in IB Gateway settings
- [ ] User notes API port (default: 7497 for paper)
- [ ] Implement `ibkr_client.py` using `ib_insync`
- [ ] Implement connection management (connect, disconnect, reconnect)
- [ ] Implement contract resolution (map instrument_id to IBKR Contract)
- [ ] Implement order submission (market, limit)
- [ ] Implement fill tracking (partial fills, full fills)
- [ ] Implement error handling (rejections, timeouts, connection loss)
- [ ] Implement `agent.py` main loop
- [ ] Implement `intent_handler.py` to receive order intents from Alethra
- [ ] Implement `fill_reporter.py` to report fills to Alethra
- [ ] Test with paper trading account
- [ ] Document user setup guide (IB Gateway installation, API configuration)
- [ ] Implement agent authentication (API key or mutual TLS)
- [ ] Test with live trading account (small size)

---

## 3. Payment Provider (Stripe)

### Prerequisites

#### 3.1 Stripe Account
- **Status**: BLOCKED
- **Action Required**: Register for Stripe account
- **URL**: https://stripe.com
- **Account Types**:
  - Test mode (for development) — recommended first
  - Live mode (for production)
- **Timeline**: Immediate for test mode; 1-3 business days for live mode approval

#### 3.2 API Credentials
- **Status**: BLOCKED
- **Action Required**: Generate API keys
- **Deliverables**:
  - Publishable key (for frontend)
  - Secret key (for backend)
  - Webhook signing secret (for webhook verification)
- **Storage**: Store in environment variables or secrets manager (not in code)

#### 3.3 Product and Price Configuration
- **Status**: BLOCKED
- **Action Required**: Create Stripe products and prices
- **Products**:
  - Alethra Free Tier (API key with rate limits)
  - Alethra PRO (subscription, e.g., $29/month)
  - Alethra API Credits (usage-based billing)
- **Pricing Decision Needed**: Confirm pricing model (subscription vs. usage vs. hybrid)

#### 3.4 Webhook Configuration
- **Status**: BLOCKED
- **Action Required**: Configure webhook endpoints
- **Events**:
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_succeeded`
  - `invoice.payment_failed`
- **URL**: Alethra backend must expose webhook endpoint (e.g., `https://api.alethra.com/v1/webhooks/stripe`)

### Implementation Status

- **Payment adapter interface**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/providers/payment_adapter.py`
  - `PaymentAdapter` ABC with `create_payment_intent`, `create_subscription`, etc.
  - `PaymentIntent`, `Subscription`, `WebhookEvent` dataclasses

- **Payment models**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/models/billing.py`
  - `Subscription`, `Invoice`, `PaymentEvent` models
  - Entitlement tracking (active subscription, API credits)

- **Billing API**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/services/billing.py`
  - Subscription management, invoice tracking
  - Entitlement enforcement middleware

- **Stripe adapter**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/app/providers/stripe_adapter.py`
  - Real Stripe SDK integration: PaymentIntent, webhook verification, checkout sessions
  - Auto-activation: uses Stripe when STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET are set
  - Falls back to TestPaymentAdapter when credentials missing
  - **BLOCKED on live payments**: Requires Stripe API credentials for actual transactions
  - 30 tests passing (plan listing, subscription lifecycle, webhooks, entitlements)

### Integration Checklist

- [ ] Register Stripe account (test mode first)
- [ ] Generate API keys (publishable, secret, webhook signing secret)
- [ ] Create Stripe products and prices
- [ ] Store credentials securely (env vars or secrets manager)
- [ ] Implement `StripeAdapter` class
- [ ] Implement payment intent creation
- [ ] Implement subscription creation and management
- [ ] Implement webhook endpoint (`/v1/webhooks/stripe`)
- [ ] Implement webhook signature verification
- [ ] Implement webhook event handling (subscription created/updated/deleted)
- [ ] Test against Stripe test mode
- [ ] Test webhook delivery (use Stripe CLI for local testing)
- [ ] Switch to live mode after testing
- [ ] Update billing configuration to support `PAYMENT_PROVIDER=stripe`
- [ ] Add integration tests for Stripe adapter
- [ ] Document Stripe-specific configuration in deployment guide

---

## 4. Deployment Configuration

### Prerequisites

#### 4.1 Cloud Provider Selection
- **Status**: NOT IMPLEMENTED
- **Action Required**: Select cloud provider
- **Options**:
  - AWS (ECS, RDS, ElastiCache)
  - GCP (Cloud Run, Cloud SQL, Memorystore)
  - Azure (Container Instances, SQL Database, Redis Cache)
  - DigitalOcean (App Platform, Managed Databases)
- **Decision Needed**: Confirm cloud provider based on cost, region, and compliance

#### 4.2 Domain and DNS
- **Status**: BLOCKED
- **Action Required**: Register domain and configure DNS
- **Deliverables**:
  - Domain name (e.g., `alethra.com`)
  - DNS records:
    - `api.alethra.com` → Backend API
    - `app.alethra.com` → Frontend
    - `www.alethra.com` → Marketing site (optional)

#### 4.3 SSL/TLS Certificates
- **Status**: BLOCKED
- **Action Required**: Obtain SSL/TLS certificates
- **Options**:
  - Let's Encrypt (free, auto-renewed)
  - Cloud provider certificate manager (e.g., AWS ACM, GCP Certificate Manager)

#### 4.4 Database Hosting
- **Status**: BLOCKED
- **Action Required**: Set up managed PostgreSQL
- **Options**:
  - AWS RDS PostgreSQL
  - GCP Cloud SQL PostgreSQL
  - Azure Database for PostgreSQL
  - DigitalOcean Managed PostgreSQL
- **Configuration**:
  - PostgreSQL 15+
  - Enable SSL
  - Set up automated backups
  - Configure connection pooling

#### 4.5 Redis Hosting
- **Status**: BLOCKED
- **Action Required**: Set up managed Redis
- **Options**:
  - AWS ElastiCache Redis
  - GCP Memorystore Redis
  - Azure Cache for Redis
  - DigitalOcean Managed Redis
- **Configuration**:
  - Redis 6+
  - Enable persistence (AOF or RDB)
  - Configure max memory policy

#### 4.6 Container Registry
- **Status**: BLOCKED
- **Action Required**: Set up container registry
- **Options**:
  - AWS ECR
  - GCP Artifact Registry
  - Azure Container Registry
  - Docker Hub (public or private)
- **Configuration**:
  - Create repositories for backend and frontend images
  - Set up CI/CD to push images

### Implementation Status

- **Docker Compose for local development**: IMPLEMENTED—OFFLINE VERIFIED
  - `docker-compose.yml`
  - Backend, frontend, PostgreSQL, Redis
  - Used for local testing and acceptance testing

- **Dockerfiles for production**: IMPLEMENTED—OFFLINE VERIFIED
  - `backend/Dockerfile`
  - `frontend/Dockerfile`
  - Multi-stage builds for small image size

- **Kubernetes manifests**: NOT IMPLEMENTED
  - **BLOCKED**: Requires cloud provider selection
  - Expected structure: `k8s/` directory
  - Components:
    - `backend-deployment.yaml`
    - `frontend-deployment.yaml`
    - `postgres-statefulset.yaml`
    - `redis-deployment.yaml`
    - `ingress.yaml`
    - `secrets.yaml` (template, not actual secrets)

- **Terraform/Pulumi for infrastructure**: NOT IMPLEMENTED
  - **BLOCKED**: Requires cloud provider selection
  - Expected structure: `infra/` directory
  - Must provision: VPC, subnets, security groups, RDS, ElastiCache, ECS/EKS, ALB

### Integration Checklist

- [ ] Select cloud provider
- [ ] Register domain name
- [ ] Configure DNS records
- [ ] Obtain SSL/TLS certificates
- [ ] Set up managed PostgreSQL
- [ ] Set up managed Redis
- [ ] Set up container registry
- [ ] Create Kubernetes manifests (or ECS task definitions)
- [ ] Create Terraform/Pulumi scripts (optional, for reproducibility)
- [ ] Set up CI/CD pipeline (GitHub Actions, GitLab CI, etc.)
- [ ] Configure secrets management (AWS Secrets Manager, GCP Secret Manager, etc.)
- [ ] Deploy to staging environment
- [ ] Test staging environment
- [ ] Deploy to production environment
- [ ] Configure monitoring and alerting (CloudWatch, Stackdriver, etc.)
- [ ] Configure logging (CloudWatch Logs, Stackdriver Logging, etc.)
- [ ] Document deployment runbook

---

## 5. Legacy Database Adoption (Scenario B)

### Prerequisites

#### 5.1 Legacy Database Access
- **Status**: BLOCKED
- **Action Required**: Obtain connection details for legacy database
- **Deliverables**:
  - Database host and port
  - Database name
  - Read-only credentials (for safety)
  - Database type (PostgreSQL, MySQL, SQLite, etc.)

#### 5.2 Legacy Schema Documentation
- **Status**: BLOCKED
- **Action Required**: Obtain or reverse-engineer legacy schema
- **Deliverables**:
  - Table definitions
  - Column types and constraints
  - Foreign key relationships
  - Indexes
  - Sample data (anonymized if sensitive)

#### 5.3 Data Migration Strategy
- **Status**: NOT IMPLEMENTED
- **Action Required**: Decide on migration strategy
- **Options**:
  - **ETL (Extract, Transform, Load)**: Batch migration with transformation
  - **CDC (Change Data Capture)**: Real-time sync during transition
  - **Dual-write**: Write to both systems during transition
- **Decision Needed**: Confirm migration strategy based on legacy system constraints

### Implementation Status

- **Migration framework**: NOT IMPLEMENTED
  - Expected structure: `backend/app/migration/`
  - Components:
    - `schema_mapper.py`: Map legacy schema to Alethra schema
    - `data_loader.py`: Extract and transform legacy data
    - `validator.py`: Validate migrated data
    - `report.py`: Generate migration report

- **Schema mapping**: NOT IMPLEMENTED
  - **BLOCKED**: Requires legacy schema documentation
  - Must map:
    - Legacy users → Alethra users
    - Legacy experiments → Alethra experiments
    - Legacy results → Alethra results

### Integration Checklist

- [ ] Obtain legacy database connection details
- [ ] Obtain or reverse-engineer legacy schema
- [ ] Document legacy schema (tables, columns, relationships)
- [ ] Decide on migration strategy (ETL, CDC, dual-write)
- [ ] Implement schema mapper
- [ ] Implement data loader
- [ ] Implement data validator
- [ ] Test migration with sample data
- [ ] Generate migration report
- [ ] Perform full migration (dry run)
- [ ] Validate migrated data
- [ ] Perform full migration (production)
- [ ] Document migration runbook

---

## Summary

### Blocked Items

1. **LSEG market data integration**: BLOCKED on LSEG API credentials
2. **IBKR execution agent**: BLOCKED on user IBKR account and IB Gateway installation
3. **Stripe payment integration**: BLOCKED on Stripe API credentials
4. **Cloud deployment**: BLOCKED on cloud provider selection and domain registration
5. **Legacy database migration**: BLOCKED on legacy database access and schema documentation

### Completed Items

1. **Provider-independent instrument identity**: IMPLEMENTED—OFFLINE VERIFIED
   - US and HK market support
   - 26 unit tests passing

2. **DataProvider interface upgrade**: IMPLEMENTED—OFFLINE VERIFIED
   - Backward compatible with existing demo provider
   - 60 tests passing

3. **Real instrument catalog**: IMPLEMENTED—OFFLINE VERIFIED
   - 19 instruments (US stocks/ETFs, HK stocks/ETFs)
   - Provider symbol mappings for LSEG and IBKR

4. **LSEG provider adapter**: IMPLEMENTED—OFFLINE VERIFIED
   - Full DataProvider implementation with OAuth2 support
   - Auto-registration when credentials present
   - 32 offline contract tests

5. **Order intent model and API**: IMPLEMENTED—OFFLINE VERIFIED
   - Full order lifecycle (pending → filled/cancelled/rejected)
   - Idempotency, kill switch, rate limiting

6. **Execution agent**: IMPLEMENTED—OFFLINE VERIFIED
   - Agent control loop, broker transport abstraction, fake transport
   - Missing: IBKRBrokerTransport (real IBKR connection)
   - 34 tests passing

7. **Payment adapter interface and Stripe adapter**: IMPLEMENTED—OFFLINE VERIFIED
   - Real Stripe SDK integration with webhook verification
   - Auto-activation when credentials present
   - 30 tests passing

8. **ML training pipeline**: IMPLEMENTED—OFFLINE VERIFIED
   - Feature engineering, temporal train/val/test split
   - Logistic regression, gradient boosting, baseline models
   - Model artifact persistence and versioned inference
   - 31 tests passing

9. **Docker Compose and Dockerfiles**: IMPLEMENTED—OFFLINE VERIFIED
   - Local development and acceptance testing

10. **Production config safety**: IMPLEMENTED—VERIFIED
    - 6 validation rules preventing unsafe deployments
    - 34 config tests

### Next Steps

1. **Immediate**: Register LSEG Developer account and generate API credentials
2. **Immediate**: Register Stripe account (test mode) and generate API keys
3. **User action**: Set up IBKR paper trading account and install IB Gateway
4. **Decision**: Select cloud provider and register domain
5. **Decision**: Confirm pricing model for Stripe subscriptions
6. **Decision**: Obtain legacy database access and schema documentation

### Estimated Timeline

- **Week 1**: LSEG and Stripe sandbox integration
- **Week 2**: IBKR execution agent implementation (with user's paper account)
- **Week 3**: Cloud deployment setup and staging environment
- **Week 4**: Production deployment and live testing
- **Week 5**: Legacy database migration (if applicable)

### Cost Estimates

- **LSEG data subscription**: ~$80-300/month (US + HK equities)
- **Stripe fees**: 2.9% + $0.30 per transaction (standard pricing)
- **Cloud hosting**: ~$100-500/month (depending on provider and scale)
- **Domain registration**: ~$10-20/year
- **IBKR**: No additional cost (user's existing account)

---

## Contact and Support

- **LSEG Developer Support**: https://developers.lseg.com/support
- **Stripe Support**: https://support.stripe.com
- **IBKR API Support**: https://www.interactivebrokers.com/en/software/api/api.htm
- **Alethra Project**: https://github.com/TommyLam0915-LCW/alethra
