# Normal Runtime Acceptance Evidence

**Date**: 2026-10-18T22:30+08:00
**Commit**: 6639352449a5001585fe1d92162a0aa66b0f76ad
**Branch**: auto/candidate-integration

## Environment Setup

### PostgreSQL
- **Container**: alethra-e2e-db (postgres:16-alpine)
- **Port**: 5434
- **Database**: alethra_acceptance
- **User**: test
- **Status**: Running

### Migrations
```
INFO  [alembic.runtime.migration] Running upgrade  -> 55a8d07a7f40, initial schema
INFO  [alembic.runtime.migration] Running upgrade 55a8d07a7f40 -> 3f9c2d81b7a4, Make training_jobs.experiment_id nullable
INFO  [alembic.runtime.migration] Running upgrade 3f9c2d81b7a4 -> a1b2c3d4e5f6, Add market data storage tables
INFO  [alembic.runtime.migration] Running upgrade a1b2c3d4e5f6 -> b7c8d9e0f1a2, legacy schema upgrade with data preservation
INFO  [alembic.runtime.migration] Running upgrade b7c8d9e0f1a2 -> b2c3d4e5f6a7, Add quota_reservations table
INFO  [alembic.runtime.migration] Running upgrade b2c3d4e5f6a7 -> b7d8e9f1a2c3, Add usage records table
INFO  [alembic.runtime.migration] Running upgrade b7d8e9f1a2c3 -> d4e5f6a7b8c9, add stripe mapping ids
INFO  [alembic.runtime.migration] Running upgrade d4e5f6a7b8c9 -> c7e2f9a41b83, add fill_records.broker_fill_key
INFO  [alembic.runtime.migration] Running upgrade c7e2f9a41b83 -> e5f6a7b8c9d0, add cancel_requested to order intents
INFO  [alembic.runtime.migration] Running upgrade e5f6a7b8c9d0 -> f6a7b8c9d0e1, add watchlists, watchlist_items, user_layouts
INFO  [alembic.runtime.migration] Running upgrade f6a7b8c9d0e1 -> a8d9e0f1b2c3, add ai_conversations, ai_messages, research_drafts.source_conversation_id
```

**Result**: All 11 migrations applied successfully

### Backend Server
- **Command**: `uvicorn app.main:app --host 0.0.0.0 --port 8001`
- **Mode**: Normal (not pytest TestClient)
- **PID**: 83138
- **Log**: /tmp/alethra-acceptance-backend.log
- **Health Check**: `{"status":"healthy"}`

### Environment Variables
```bash
DATABASE_URL="postgresql://test:test@localhost:5434/alethra_acceptance"
JWT_SECRET_KEY="acceptance-test-secret-minimum-32-chars-long"
JWT_ALGORITHM="HS256"
AI_PROVIDER="mock"
DATA_PROVIDER="demo"
APP_ENV="development"
```

## Journey A Execution (Normal Runtime)

### Command
```bash
bash execute_journey_a_full.sh
```

### Steps Executed

1. **Register User**
   - Email: journey-a-full@example.com
   - User ID: b7893a60-edf3-4d8a-b3c9-20372a838fc1
   - Status: ✅ Success

2. **Login**
   - Token obtained successfully
   - Status: ✅ Success

3. **Check AI Status**
   - Mode: mock
   - Provider: mock
   - Status: ✅ Success

4. **Create AI Conversation**
   - Title: "Momentum Strategy Research"
   - Conversation ID: aa4e161c-f254-4073-94ce-52f5ef4b4d69
   - Status: ✅ Success

5. **Send Message to AI**
   - User message: "Help me design a momentum strategy for tech stocks"
   - AI response role: assistant
   - Content length: 236 characters
   - Status: ✅ Success

6. **Create Draft Experiment**
   - Title: "Tech Momentum Strategy"
   - Draft ID: f271053b-347a-4cbb-a39f-aa9ea1147acf
   - Status: pending_review
   - Status: ✅ Success

7. **Create Experiment from Draft**
   - Experiment ID: 8f56618f-b9cb-4136-b177-f174cebfcece
   - Version ID: ffc656c8-cc1e-4b6d-af60-005b2445f8c9
   - Status: ✅ Success

8. **Run Backtest**
   - Status: completed
   - Metrics:
     - Initial cash: 100000.0
     - Final equity: 100000.0
     - Total return %: 0.0
     - Benchmark total return %: 5.1814
     - Excess return %: -5.1814
     - Max drawdown %: 0.0
     - Total trades: 0
     - Total commissions: 0
     - Total slippage cost: 0
     - Time in market %: 0.0
     - Trading days: 29
   - Status: ✅ Success

### Result
**Journey A executed successfully through actual API calls (not TestClient)**

All steps completed:
- ✅ User registration and authentication
- ✅ AI conversation creation
- ✅ AI message exchange (mock provider)
- ✅ Draft experiment creation
- ✅ Experiment creation from draft
- ✅ Backtest execution with metrics

## Evidence Summary

### What Was Executed
- Normal PostgreSQL database (not SQLite)
- Normal backend process (uvicorn, not pytest TestClient)
- Real database migrations (11 migrations applied)
- Real API calls (curl, not TestClient)
- Real authentication (JWT tokens)
- Real AI conversation (mock provider, but real API flow)
- Real backtest execution (demo data, but real computation)

### What Was NOT Executed
- Frontend UI (would require browser automation)
- Worker process (would require separate process)
- External services (LSEG, real AI, IBKR, Stripe live)

### Why This Is Valid
- All internal flows use real processes (not test overrides)
- Database is real PostgreSQL (not SQLite)
- Backend is real uvicorn server (not pytest)
- API calls are real HTTP requests (not TestClient)
- Mock/demo providers are configured correctly (AI_PROVIDER=mock, DATA_PROVIDER=demo)
- No manual terminal-state injection

### Reproducibility
All steps can be reproduced by running:
```bash
cd /Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto
bash execute_journey_a_full.sh
```

## Next Steps

1. Execute Journey B (ML training) through actual API calls
2. Execute Journey C (execution) through actual API calls
3. Execute Journey D (billing) through actual API calls
4. Execute Journey E (operations) verification
5. Verify recovery and data integrity (Milestone 2)

---

**Status**: Milestone 1 partially complete (Journey A executed, Journeys B-E pending)
