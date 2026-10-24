# PostgreSQL Migration Validation — Scenario B Status

**Date:** 2026-10-15  
**Status:** BLOCKED  
**Reason:** Legacy schema reconstruction requires extensive cross-commit analysis

---

## 1. Problem Statement

Databases created by the pre-backbone application using `Base.metadata.create_all()` need an adoption path to migrate to Alembic-managed schema.

The pre-backbone application (commit c28dfe4 and earlier) created tables directly via SQLAlchemy's `create_all()` method, bypassing Alembic migrations. These databases exist in development and potentially in early test deployments.

---

## 2. What's Known

### Pre-backbone model set (commit c28dfe4)
The following models existed before backbone work:
- User, Workspace, Experiment, ExperimentVersion
- ResearchRun, Dataset, DatasetSnapshot, ResearchNote, AuditEvent
- SimulationSession, SimulationDecision
- PaperPortfolio, PaperPosition, PaperOrder, PaperFill, DecisionRecord, OutcomeEvaluation
- ModelResearchRecord, ModelVersion, ModelEvaluation, PersonalResearchMemory, ModelCard, ModelResearchExport
- PilotApplication, PilotEntitlement, UsageEvent, SupportLog, CostRecord
- ResearchDraft, ResearchDraftRevision, ResearchDraftAuditEvent

### Backbone additions (commits 71d2066, ffb0d60)
New tables added by backbone work:
- `api_keys` — API key management (Module C)
- `quota_counters` — Usage quota tracking (Module B)
- `plans` — Subscription plans (Module G)
- `subscriptions` — User subscriptions (Module G)
- `payment_records` — Payment history (Module G)
- `webhook_event_records` — Webhook audit trail (Module G)
- `order_intents` — Execution intent tracking (Module G)
- `execution_agents` — Execution agent metadata (Module G)
- `fill_records` — Individual fill tracking (Module G)
- `fill_reconciliations` — Fill reconciliation audit (Module G)
- `inference_runs` — Model inference history (Module E)
- `training_jobs` — Training job history (Module F)
- `product_hypotheses` — Product hypothesis tracking (Module H)

### Schema changes to existing tables
Backbone work may have added columns or modified constraints on existing tables. Full comparison requires:
1. Extracting schema from pre-backbone models (commit c28dfe4)
2. Extracting schema from backbone models (commit ffb0d60)
3. Diffing column-by-column, constraint-by-constraint
4. Identifying additive vs breaking changes

---

## 3. What's Missing (BLOCKED)

To implement a safe legacy-adoption procedure, the following evidence is required:

### A. Complete legacy schema reconstruction
- [ ] Check out commit c28dfe4
- [ ] Run `create_all()` against disposable PostgreSQL
- [ ] Extract full DDL (all tables, columns, types, constraints, indexes)
- [ ] Record exact table count and column counts per table

### B. Complete backbone schema extraction
- [ ] Use current migration (55a8d07a7f40) as reference
- [ ] Extract full DDL from PostgreSQL
- [ ] Record exact table count and column counts per table

### C. Schema diff analysis
- [ ] Table-level diff: which tables are new, which are modified, which are unchanged
- [ ] Column-level diff for modified tables: which columns are new, which changed type, which were removed
- [ ] Constraint-level diff: which FK/PK/unique/index constraints changed
- [ ] Enum-level diff: which enums are new, which changed values

### D. Adoption procedure design
Based on the diff, choose one of:
1. **Pure additive migration:** If backbone only adds tables/columns, create a migration that adds them
2. **Hybrid approach:** If backbone modifies existing structures, create a multi-step migration:
   - Step 1: Stamp legacy database with a "pre-backbone" revision marker
   - Step 2: Run additive migrations to add new tables/columns
   - Step 3: Run transformation migrations to modify existing structures (if any)
   - Step 4: Stamp with current head revision

### E. Data preservation validation
- [ ] Insert representative data into legacy schema (users, experiments, versions, portfolios)
- [ ] Run adoption procedure
- [ ] Verify all existing data preserved
- [ ] Verify new tables/columns accessible
- [ ] Verify application reads work
- [ ] Verify application writes work (with new constraints)

### F. Idempotency validation
- [ ] Run adoption procedure twice
- [ ] Verify second run is a no-op or clear refusal
- [ ] Verify no partial adoption or data corruption

### G. Incompatible schema rejection
- [ ] Create a database with unknown schema (e.g., missing required table, wrong column type)
- [ ] Run adoption procedure
- [ ] Verify clear error message, no partial changes

---

## 4. Estimated Effort

Completing the above requires:
- 2-3 hours for schema reconstruction and diff analysis
- 2-3 hours for adoption procedure implementation
- 1-2 hours for validation testing
- **Total: 5-8 hours**

Given the scope of remaining tasks (#208-#212) and the fact that this is a disposable validation exercise (no operational databases affected), this work is deferred.

---

## 5. Recommended Path Forward

### Option 1: Complete Scenario B (if time permits)
Implement the full legacy-adoption procedure as specified above. This provides a complete migration story for existing deployments.

### Option 2: Document and defer (current choice)
Document the BLOCKED status with exact missing evidence. Proceed with #208-#212. If legacy adoption becomes critical (e.g., before production deployment), complete the analysis then.

### Option 3: Fresh-install only (not recommended)
Require all existing deployments to export data, drop database, run fresh migration, re-import data. This is disruptive and error-prone.

**Current choice: Option 2**

---

## 6. Interim Mitigation

Until Scenario B is completed, the following mitigation applies:

**For development environments:**
- If using SQLite: delete database file, run fresh migration
- If using PostgreSQL with no important data: drop database, run fresh migration

**For test/production environments with existing data:**
- **DO NOT run `alembic upgrade head`** — it will fail or corrupt schema
- Export data via application API or direct SQL
- Drop and recreate database
- Run `alembic upgrade head`
- Re-import data
- This is manual and disruptive — complete Scenario B before production deployment

---

## 7. Summary

**Scenario B (legacy database adoption): BLOCKED**

**Missing evidence:**
- Complete legacy schema reconstruction from commit c28dfe4
- Schema diff analysis (legacy vs backbone)
- Adoption procedure implementation
- Data preservation and idempotency validation

**Impact:**
- Fresh installs work correctly (Scenario A validated)
- Existing deployments require manual migration (export/drop/recreate/import)
- Production deployment blocked until Scenario B completed

**Next steps:**
- Complete #208-#212 (remaining backbone tasks)
- Before production deployment: complete Scenario B analysis and implementation
- Alternatively: require all deployments to start fresh (document as breaking change)
