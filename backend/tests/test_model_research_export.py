import json
import pytest

from app.api.schemas import (
    PACKAGE_SCHEMA_VERSION,
    MANDATORY_EXCLUDED_CAPABILITIES,
    PACKAGE_DISCLAIMER,
)


def _create_user2(client):
    client.post("/api/auth/register", json={
        "email": "user2@alethra.io",
        "password": "testpass123",
        "display_name": "User 2",
        "user_type": "individual",
    })
    resp = client.post("/api/auth/login", json={
        "email": "user2@alethra.io",
        "password": "testpass123",
    })
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_record(client, headers, title="Test Model"):
    resp = client.post("/api/v1/model-research", headers=headers, json={
        "title": title,
        "purpose": "Testing export workflow",
    })
    assert resp.status_code == 201
    return resp.json()


def _create_version(client, headers, record_id):
    resp = client.post(f"/api/v1/model-research/{record_id}/versions", headers=headers, json={
        "model_type": "rule_based",
        "model_specification_json": {"rules": [{"indicator": "SMA", "period": 20}]},
    })
    assert resp.status_code == 201
    return resp.json()


def _create_card(client, headers, version_id):
    resp = client.post("/api/v1/model-cards", headers=headers, json={
        "model_version_id": version_id,
        "purpose": "Test card",
        "intended_research_use": "Research only",
        "excluded_use": "No live trading",
        "data_sources": ["DEMO-ALPHA"],
        "data_cutoff": "2024-02-14",
        "known_limitations": ["Demo data only", "No real market data"],
        "revocation_process": "Revoke immediately if errors found",
        "disclaimer": "Research only, not investment advice.",
        "risk_flags": ["demo_data: Uses synthetic data only"],
    })
    assert resp.status_code == 201
    return resp.json()


def _create_export(client, headers, record_id, version_id, include_private_notes=False):
    resp = client.post(
        f"/api/v1/model-research-records/{record_id}/exports",
        headers=headers,
        json={
            "model_version_id": version_id,
            "include_private_notes": include_private_notes,
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _download_package(client, headers, export_id):
    resp = client.get(f"/api/v1/exports/{export_id}/download", headers=headers)
    assert resp.status_code == 200
    return resp.json()


REQUIRED_FILES = [
    "manifest.json",
    "model-specification.json",
    "research-summary.md",
    "strategy-spec.json",
    "approved-research-memory.json",
    "dataset-lineage.json",
    "historical-evaluations.json",
    "forward-simulation-evaluations.json",
    "decision-ledger.json",
    "risk-flags.json",
    "methodology.md",
    "limitations.md",
    "version-history.json",
    "integrity.json",
]


class TestExportContainsRequiredFiles:
    """Test 1: Export contains all required files."""

    def test_export_has_all_required_files(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])

        assert export["status"] == "completed"
        pkg = _download_package(client, auth_headers, export["id"])

        for fname in REQUIRED_FILES:
            assert fname in pkg, f"Missing required file: {fname}"


class TestExportIncludesModelCardAndLimitations:
    """Test 2: Export includes model card and limitations."""

    def test_export_includes_model_card(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, version["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        assert "model-card.md" in pkg
        card_md = pkg["model-card.md"]
        assert "Test Model" in card_md
        assert "Excluded Use" in card_md
        assert "No live trading" in card_md

    def test_export_includes_limitations(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, version["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        assert "limitations.md" in pkg
        lim_md = pkg["limitations.md"]
        assert "Demo data only" in lim_md
        assert "No real market data" in lim_md


class TestExportExcludesCredentials:
    """Test 3: Export excludes all credentials."""

    def test_no_credentials_in_package(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, version["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        content_without_manifest = {k: v for k, v in pkg.items() if k != "manifest.json"}
        full_text = json.dumps(content_without_manifest).lower()
        credential_terms = [
            "api_key", "api_secret", "secret_key", "password",
            "private_key", "access_token",
            "exchange_credential",
        ]
        for term in credential_terms:
            assert term not in full_text, f"Found credential term '{term}' in package"


class TestExportExcludesLiveTradeInstructions:
    """Test 4: Export excludes live trade instructions."""

    def test_no_trade_commands_in_package(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, version["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        manifest = pkg["manifest.json"]
        assert manifest["excluded_capabilities"] == MANDATORY_EXCLUDED_CAPABILITIES

        content_without_manifest = {k: v for k, v in pkg.items() if k != "manifest.json"}
        full_text = json.dumps(content_without_manifest).lower()
        trade_terms = [
            "execute_trade", "live_deployment", "real_money_allocation",
        ]
        for term in trade_terms:
            assert term not in full_text, f"Found trade term '{term}' in package"

    def test_no_signal_or_execute_endpoints(self, client):
        for path in ["/api/v1/signals", "/api/v1/trade", "/api/v1/execute",
                      "/api/v1/broker", "/api/v1/exchange-key", "/api/v1/order",
                      "/api/v1/target-weight"]:
            resp = client.get(path)
            assert resp.status_code in (404, 405), f"Endpoint {path} should not exist"


class TestExportOwnerAuthorization:
    """Test 5: Export uses only owner-authorized records."""

    def test_user_cannot_export_others_record(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        user2_headers = _create_user2(client)

        resp = client.post(
            f"/api/v1/model-research-records/{record['id']}/exports",
            headers=user2_headers,
            json={"model_version_id": version["id"]},
        )
        assert resp.status_code in (403, 404)

    def test_user_cannot_download_others_export(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])

        user2_headers = _create_user2(client)

        resp = client.get(f"/api/v1/exports/{export['id']}", headers=user2_headers)
        assert resp.status_code in (403, 404)

        resp = client.get(f"/api/v1/exports/{export['id']}/download", headers=user2_headers)
        assert resp.status_code in (403, 404)


class TestExportChecksumIntegrity:
    """Test 6: Export checksum changes if contents change."""

    def test_checksum_present_and_valid(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, version["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])

        assert export["package_checksum"] is not None
        assert len(export["package_checksum"]) == 64

        pkg = _download_package(client, auth_headers, export["id"])
        manifest = pkg["manifest.json"]
        assert manifest["package_checksum"] == export["package_checksum"]

    def test_different_exports_different_checksums(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        v1 = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, v1["id"])

        export1 = _create_export(client, auth_headers, record["id"], v1["id"])

        v2 = _create_version(client, auth_headers, record["id"])
        export2 = _create_export(client, auth_headers, record["id"], v2["id"])

        assert export1["package_checksum"] != export2["package_checksum"]

    def test_integrity_file_has_file_checksums(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        integrity = pkg["integrity.json"]
        assert integrity["algorithm"] == "sha256"
        assert "file_checksums" in integrity
        assert len(integrity["file_checksums"]) > 0
        for fname in REQUIRED_FILES:
            if fname == "integrity.json":
                continue
            assert fname in integrity["file_checksums"], f"Missing checksum for {fname}"


class TestRevokedModelExport:
    """Test 7: Revoked model export prominently shows revoked status."""

    def test_revoked_version_export_shows_status(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        lock_resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/lock",
            headers=auth_headers,
        )
        assert lock_resp.status_code == 200

        revoke_resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/revoke?reason=Test+revocation",
            headers=auth_headers,
        )
        assert revoke_resp.status_code == 200

        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        model_spec = pkg["model-specification.json"]
        assert model_spec["revoked_at"] is not None
        assert model_spec["revocation_reason"] == "Test revocation"

        summary = pkg["research-summary.md"]
        assert "REVOKED" in summary
        assert "Test revocation" in summary


class TestPrivateNotesExcludedByDefault:
    """Test 8: Private notes are excluded by default."""

    def test_notes_excluded_by_default(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        client.post(f"/api/v1/experiments/{record['id']}/notes", headers=auth_headers, json={
            "content": "SECRET private note that should not be exported",
        })

        export = _create_export(client, auth_headers, record["id"], version["id"], include_private_notes=False)
        pkg = _download_package(client, auth_headers, export["id"])

        memory_data = pkg["approved-research-memory.json"]
        assert memory_data["include_private_notes"] is False
        assert memory_data["notes"] == []

    def test_notes_included_when_requested(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        export = _create_export(client, auth_headers, record["id"], version["id"], include_private_notes=True)
        pkg = _download_package(client, auth_headers, export["id"])

        memory_data = pkg["approved-research-memory.json"]
        assert memory_data["include_private_notes"] is True


class TestPackageSchemaValidation:
    """Test 9: Package schema validates."""

    def test_manifest_has_all_required_fields(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        _create_card(client, auth_headers, version["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        manifest = pkg["manifest.json"]

        required_fields = [
            "package_schema_version",
            "model_research_record_id",
            "model_version_id",
            "generated_at",
            "model_status",
            "evidence_status",
            "engine_version",
            "dataset_snapshot_ids",
            "data_cutoff_at",
            "package_checksum",
            "disclaimer",
            "excluded_capabilities",
            "included_files",
        ]
        for field in required_fields:
            assert field in manifest, f"Missing manifest field: {field}"

        assert manifest["package_schema_version"] == PACKAGE_SCHEMA_VERSION
        assert manifest["disclaimer"] == PACKAGE_DISCLAIMER
        assert manifest["excluded_capabilities"] == MANDATORY_EXCLUDED_CAPABILITIES
        assert len(manifest["included_files"]) > 0

    def test_all_results_labeled(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        export = _create_export(client, auth_headers, record["id"], version["id"])
        pkg = _download_package(client, auth_headers, export["id"])

        valid_labels = {"historical_simulation", "paper_simulation", "research_metadata"}
        for fname, content in pkg.items():
            if fname.endswith(".json") and isinstance(content, dict) and "label" in content:
                assert content["label"] in valid_labels, (
                    f"File {fname} has invalid label: {content['label']}"
                )


class TestUUIDJSONBoundaryRegression:
    """Regression: JSON string IDs must convert to UUID objects for ORM queries."""

    def _create_experiment_version(self, client, headers):
        resp = client.post("/api/v1/experiments/", headers=headers, json={
            "title": "UUID Export Boundary Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "schema_version": "1.0",
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "frequency": "1d",
                "start_date": "2024-01-02",
                "end_date": "2024-02-14",
                "initial_cash": 10000,
                "signal_parameters": {},
                "commission_rate": 0.001,
                "slippage_rate": 0.001,
                "benchmark_id": "DEMO-BENCHMARK",
                "currency": "USD",
            },
        })
        assert resp.status_code == 201
        return resp.json()["current_version_id"]

    def test_model_research_export_converts_json_string_ids_to_uuid_objects(self, client, auth_headers):
        exp_version_id = self._create_experiment_version(client, auth_headers)

        record = _create_record(client, auth_headers)
        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
                "linked_experiment_version_ids": [exp_version_id],
            },
        )
        assert resp.status_code == 201
        version = resp.json()

        export = _create_export(client, auth_headers, record["id"], version["id"])
        assert export["status"] == "completed"

        pkg = _download_package(client, auth_headers, export["id"])
        assert "manifest.json" in pkg

    def test_model_research_export_rejects_invalid_uuid_strings_safely(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
                "linked_experiment_version_ids": ["not-a-valid-uuid"],
            },
        )
        assert resp.status_code == 422

        resp2 = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
                "linked_experiment_version_ids": [""],
            },
        )
        assert resp2.status_code == 422

    def test_model_research_export_handles_duplicate_linked_ids_deterministically(self, client, auth_headers):
        exp_version_id = self._create_experiment_version(client, auth_headers)

        record = _create_record(client, auth_headers)
        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
                "linked_experiment_version_ids": [exp_version_id, exp_version_id],
            },
        )
        assert resp.status_code == 201
        version = resp.json()

        export1 = _create_export(client, auth_headers, record["id"], version["id"])
        assert export1["status"] == "completed"

        export2 = _create_export(client, auth_headers, record["id"], version["id"])
        assert export2["status"] == "completed"
        assert export1["package_checksum"] == export2["package_checksum"]
