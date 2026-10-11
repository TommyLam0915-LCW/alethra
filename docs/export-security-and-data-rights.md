# Export Security and Data Rights

**Date**: 2026-10-11

---

## Principles

1. **User ownership**: Export packages contain only the exporting user's own data. No cross-user data leakage.
2. **Minimal exposure**: Credentials, trade instructions, and deployment authorizations are never included.
3. **Consent-based inclusion**: Private notes are excluded by default and only included with explicit opt-in.
4. **Integrity verification**: SHA-256 checksums allow recipients to verify package contents have not been altered.

---

## What Is Excluded

The export package will **never** contain:

| Category | Examples |
|----------|---------|
| Broker credentials | API keys, secret keys, login passwords, OAuth tokens |
| Exchange credentials | Exchange API keys, trading passwords, withdrawal addresses |
| Execution instructions | Automated trading scripts, order placement logic |
| Trading commands | Buy/sell/hold signals, entry/exit commands |
| Allocation advice | Target weights, position sizing, portfolio allocation recommendations |
| Profitability claims | Return guarantees, profit forecasts, performance promises |
| Deployment authorization | Live trading approval, production deployment configs |

These exclusions are encoded as `MANDATORY_EXCLUDED_CAPABILITIES` in the schema and listed in every `manifest.json`.

---

## What Is Included

| Data | Condition |
|------|-----------|
| Model specification | Always |
| Evaluation results (historical + forward) | Always |
| Decision ledger | Always (if linked simulations exist) |
| Model card | Always (if one exists for the version) |
| Version history | Always |
| Risk flags | Always (from model card) |
| Dataset lineage | Always |
| Approved research memories | Always (confirmed memories only) |
| Private research notes | Only when `include_private_notes=true` |

---

## Access Control

- Exports are owned by the user who creates them.
- The `owner_id` field on `ModelResearchExport` enforces ownership.
- API endpoints check `export.owner_id == current_user.id` before returning data.
- A user cannot export another user's model research record (403 Forbidden).
- A user cannot download another user's export package (403 Forbidden).

---

## Data Portability

The export package supports data portability by providing a complete, machine-readable snapshot of a user's model research. Users can:

- Download their research at any time
- Share the package with collaborators for peer review
- Archive their work independently of the platform
- Verify package integrity using the included checksums

---

## Integrity Verification

To verify a package has not been tampered with:

1. Read `integrity.json` from the package
2. For each file listed in `file_checksums`, compute SHA-256 and compare
3. Compute SHA-256 of the entire package JSON and compare with `manifest.json` → `package_checksum`

**Note**: Checksums confirm consistency, not investment correctness. A valid checksum means the package has not been altered — it does not validate the research methodology or results.

---

## Revoked Models

When a model version has been revoked, the export still succeeds but prominently displays the revoked status:

- `model-specification.json` includes `revoked_at` and `revocation_reason`
- `research-summary.md` shows a "REVOKED" marker with the reason
- The manifest's `model_status` reflects the current record status

---

## Audit Trail

Every successful export emits an `export_generated` audit event with:
- `entity_type`: `model_research_export`
- `actor_id`: The user who created the export
- `payload`: Record ID, version ID, and whether private notes were included

---

**Document Version**: 1.0
**Last Updated**: 2026-10-11
