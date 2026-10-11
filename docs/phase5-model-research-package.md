# Phase 5: Alethra Model Research Package

**Date**: 2026-10-11
**Schema Version**: 1.0.0

---

## Overview

The Alethra Model Research Package is an exportable, self-contained research artifact that bundles all evidence, specifications, and metadata for a model version. It allows researchers to archive, share, and review a complete snapshot of their model research.

**The export package is a research artifact. It is not an execution package, trading signal, investment recommendation, or deployment approval.**

---

## Package Contents

The package contains 15 files organized into machine-readable JSON and human-readable Markdown:

### Machine-Readable Files (JSON)

| File | Label | Description |
|------|-------|-------------|
| `manifest.json` | — | Package metadata, schema version, checksum, disclaimer, excluded capabilities |
| `model-specification.json` | `research_metadata` | Model type, specification, prompt/schema versions, linked resources |
| `strategy-spec.json` | `historical_simulation` | Linked experiment strategy specifications |
| `approved-research-memory.json` | `research_metadata` | Confirmed personal research memories and optional notes |
| `dataset-lineage.json` | `research_metadata` | Dataset snapshot IDs and source information |
| `historical-evaluations.json` | `historical_simulation` | Backtest evaluation results |
| `forward-simulation-evaluations.json` | `paper_simulation` | Forward simulation evaluation results |
| `decision-ledger.json` | `paper_simulation` | Paper trading decision records |
| `risk-flags.json` | `research_metadata` | Risk flags from the model card |
| `version-history.json` | `research_metadata` | All versions with lock/revoke status |
| `integrity.json` | — | SHA-256 checksums for every file in the package |

### Human-Readable Files (Markdown)

| File | Description |
|------|-------------|
| `model-card.md` | Formatted model card (only when a card exists for the version) |
| `research-summary.md` | Research overview with status, evaluations, and version info |
| `methodology.md` | Evaluation methods and engine versions |
| `limitations.md` | Known limitations from model card plus general limitations |

---

## Mandatory Excluded Capabilities

The package explicitly excludes all of the following. These are listed in `manifest.json` under `excluded_capabilities`:

1. `no_broker_credentials` — No broker login or API credentials
2. `no_exchange_credentials` — No exchange API keys or secrets
3. `no_automatic_execution_instruction` — No instructions for automated execution
4. `no_buy_sell_hold_command` — No buy/sell/hold trading commands
5. `no_target_weight` — No target portfolio weight recommendations
6. `no_real_money_allocation_recommendation` — No real-money allocation advice
7. `no_claim_of_profitability` — No claims of profitability or future returns
8. `no_live_deployment_authorization` — No authorization for live deployment

---

## Integrity

Every file (except `integrity.json` itself) has a SHA-256 checksum recorded in `integrity.json`. The `manifest.json` contains an overall `package_checksum` computed from all package contents.

**Checksum confirms package consistency, not investment correctness or independent audit.**

---

## API Endpoints

### Create Export

```
POST /api/v1/model-research-records/{record_id}/exports
```

Request body:
```json
{
  "model_version_id": "uuid",
  "include_private_notes": false
}
```

Response: `ModelResearchExportResponse` with status `completed` or `failed`.

### Get Export Status

```
GET /api/v1/exports/{export_id}
```

Returns export metadata including status, checksum, and timestamps.

### Download Package

```
GET /api/v1/exports/{export_id}/download
```

Returns the complete package as JSON. All 15 files are top-level keys in the response object.

### Authorization

All endpoints require authentication. Only the export owner can access their exports. Attempting to access another user's export returns 403.

---

## Private Notes

Private research notes are **excluded by default**. When `include_private_notes=true`, notes linked to experiments associated with the model version are included in `approved-research-memory.json`.

---

## Database Model

`ModelResearchExport` tracks each export job:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `owner_id` | UUID | User who created the export |
| `model_research_record_id` | UUID | Parent research record |
| `model_version_id` | UUID | Exported model version |
| `status` | Enum | pending / generating / completed / failed |
| `include_private_notes` | Boolean | Whether notes were included |
| `package_json` | JSONB | Complete package data |
| `package_checksum` | String(64) | SHA-256 of the package |
| `error_message` | Text | Error details if failed |
| `created_at` | DateTime | Creation timestamp |
| `completed_at` | DateTime | Completion timestamp |

---

## Frontend

The model detail page includes an "Export" tab where users can:
- Select a version to export
- Toggle private notes inclusion
- Create the export package
- Download the package as a JSON file
- View the checksum and export status

A disclaimer banner is displayed prominently on the export tab.

---

## Tests

16 tests across 9 categories:

1. **Required files** — All 14 mandatory files present
2. **Model card and limitations** — Card and limitations content correct
3. **Credential exclusion** — No API keys, secrets, passwords in package
4. **Live trade exclusion** — No trade commands, no signal/execute endpoints
5. **Owner authorization** — User B cannot access User A's exports
6. **Checksum integrity** — Checksum present, unique per export, all files checksummed
7. **Revoked model export** — Revoked status shown in summary and specification
8. **Private notes** — Excluded by default, included when requested
9. **Schema validation** — Manifest has all required fields, all JSON files labeled

---

**Document Version**: 1.0
**Last Updated**: 2026-10-11
