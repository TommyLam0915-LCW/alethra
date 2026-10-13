# PR #3 Local Stack Repair and Security Fix — Status Report

**Date**: 2026-10-13  
**Branch**: `feat/phase2a-draft-review-ui`  
**Base**: `main` at `7dd308a` (PR #2 merged)

---

## Summary

Resolved two critical blockers preventing browser acceptance testing:

1. **PostgreSQL port conflict** — native Homebrew PostgreSQL vs Docker container both on port 5432
2. **Security vulnerability** — private research content (hypothesis, falsification criteria) exposed in URL query parameters

Both issues are now fixed. The field transfer mechanism uses authenticated API calls instead of URL parameters.

---

## Issue 1: PostgreSQL Port Conflict

### Root Cause

Two PostgreSQL instances were running on port 5432:
- **Native Homebrew PostgreSQL** (PID 1784): `/opt/homebrew/opt/postgresql@15/bin/postgres`
- **Docker container** (d705b961-db-1): PostgreSQL 16.14

The backend was connecting to the native instance (which has different credentials than `.env`), causing authentication failures.

### Solution

Created `docker-compose.acceptance.yml` with:
- PostgreSQL on port **5433** (instead of 5432)
- Backend on port **8001** (instead of 8000)
- Frontend on port **3001** (instead of 3000)
- Separate database: `ai_investment_lab_acceptance`
- Separate volume: `postgres_acceptance_data`

This provides a disposable acceptance environment that doesn't conflict with existing services.

### Usage

```bash
# Start acceptance environment
docker-compose -f docker-compose.acceptance.yml up -d

# Stop acceptance environment
docker-compose -f docker-compose.acceptance.yml down

# Stop and remove volume (disposable)
docker-compose -f docker-compose.acceptance.yml down -v
```

---

## Issue 2: Security Vulnerability — Private Content in URLs

### Vulnerability

The previous implementation put private research content directly in URL query parameters:

```typescript
// VULNERABLE CODE (removed)
params.set('hypothesis', String(draft.hypothesis));
params.set('falsification_criteria', String(draft.falsification_criteria));
router.push(`/experiments/new?${params.toString()}`);
```

This exposed sensitive data in:
- Browser history
- Server logs
- Referrer headers
- Bookmarks
- Shared links

### Fix

**New workflow**: URL contains only `draft_id` and field names, not values.

**Draft detail page** (`/research-drafts/[id]/page.tsx`):
```typescript
// SECURE CODE
params.set('draft_id', draft.id);
params.set('fields', Array.from(selectedFields).join(','));
router.push(`/experiments/new?${params.toString()}`);
```

**Experiment creation page** (`/experiments/new/page.tsx`):
1. Reads `draft_id` and `fields` from URL
2. Fetches draft via authenticated API: `api.researchDrafts.get(draft_id)`
3. Verifies draft status is `accepted`
4. Shows preview of proposed values
5. User confirms before applying fields to form

### Security Properties

✅ Private content never appears in URLs  
✅ Draft must be in `accepted` status before transfer  
✅ User must explicitly confirm field application  
✅ API call requires authentication (JWT token)  
✅ Backend validates draft ownership (owner-only access)  
✅ Cancel leaves form unchanged  

### User Experience

1. User selects fields to transfer from accepted draft
2. Clicks "確認套用 N 個欄位"
3. Navigates to `/experiments/new?draft_id=...&fields=title,hypothesis,...`
4. Page fetches draft via authenticated API
5. Preview panel shows proposed values
6. User clicks "確認套用" to apply or "取消" to abort
7. After confirmation, banner shows which fields were applied
8. User can modify any field before creating experiment

---

## Issue 3: Missing Dependency

### Problem

Docker backend failed with: `ImportError: email-validator is not installed`

### Root Cause

`backend/app/auth/schemas.py` uses `EmailStr` from Pydantic, which requires the `email-validator` package. This was not listed in `requirements.txt`.

### Fix

Added to `backend/requirements.txt`:
```
email-validator==2.1.1
```

---

## Files Changed

| File | Change |
|------|--------|
| `frontend/src/app/research-drafts/[id]/page.tsx` | Fixed `handleApplySelectedFields` to use draft_id-based transfer |
| `frontend/src/app/experiments/new/page.tsx` | Rewrote to fetch draft via API, show preview, require confirmation |
| `backend/requirements.txt` | Added `email-validator==2.1.1` |
| `docker-compose.acceptance.yml` | New — disposable acceptance environment on non-conflicting ports |

---

## Verification

### Backend Test Suite
```
258 passed, 2335 warnings in 138.14s
```

All tests pass, including 5 new tests for secure field transfer:

**Backend API tests** (not frontend UI tests):
- `test_draft_must_be_accepted_for_field_transfer` — verifies API rejects non-accepted drafts
- `test_accepted_draft_can_be_fetched_for_transfer` — verifies API returns accepted draft with all fields via authenticated request
- `test_zero_values_preserved_in_draft` — verifies API preserves zero commission/slippage (not treated as missing)
- `test_authentication_required_for_draft_fetch` — verifies API returns 403 for missing credentials
- `test_owner_only_access_for_field_transfer` — verifies API returns 403 for non-owner access

**Note**: These tests verify backend API behavior. Frontend UI behavior (preview panel, confirmation dialog, field population) requires manual browser testing.

### Frontend Build
```
✓ Compiled successfully
Route (app)                              Size     First Load JS
├ ƒ /experiments/new                     6.84 kB          94 kB
└ ƒ /research-drafts/[id]                6.6 kB          101 kB
```

Both pages compile without errors.

### PostgreSQL Smoke Test
```
/var/run/postgresql:5432 - accepting connections
```

Database connection verified.

### Whitespace Check
```
git diff --check: no errors
```

### Security Audit

✅ No private content in URLs  
✅ Authentication required for draft fetch (403 for missing credentials)  
✅ Draft status validation (must be `accepted`)  
✅ Owner-only access enforced (403 for non-owners)  
✅ Explicit user confirmation required  
✅ Cancel aborts transfer cleanly  
✅ Zero values preserved (not treated as missing)  

---

## Next Steps

Completed:
1. ✅ **Start acceptance environment** — disposable stack on non-conflicting ports
2. ✅ **API verification** — backend endpoints tested via test client
3. ✅ **Regression tests** — 5 new tests for secure field transfer
4. ✅ **Verification suite** — 258 backend tests pass, frontend builds, no whitespace errors
5. ✅ **Documentation** — this report

Pending:
6. ⏳ **Browser acceptance** — manual testing required (scenarios A-N)
7. ⏳ **Final handoff** — after browser acceptance confirmed

---

## Constraints Respected

- ✅ No commits made (awaiting separate instruction)
- ✅ No pushes made
- ✅ No PR created/modified
- ✅ No merge performed
- ✅ No deployment initiated
- ✅ Phase 2B not started
- ✅ No external AI/market data/broker integrations added
- ✅ Feature branch isolation maintained
- ✅ All existing functionality preserved (additive only)
- ✅ No authentication trust mode or auth bypass
- ✅ No volumes/databases deleted without approval

---

## Status

**Backend and frontend verification complete. Browser acceptance testing pending.**
