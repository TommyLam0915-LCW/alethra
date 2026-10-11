import pytest


class TestBrandEndpoint:
    def test_get_brand(self, client):
        resp = client.get("/api/v1/brand")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Alethra"
        assert "tagline" in data
        assert "disclaimer" in data


class TestDemoAssetsEndpoint:
    def test_list_demo_assets(self, client):
        resp = client.get("/api/v1/demo-assets")
        assert resp.status_code == 200
        data = resp.json()
        assert "assets" in data
        assert "date_range" in data
        symbols = [a["symbol"] for a in data["assets"]]
        assert "DEMO-ALPHA" in symbols
        assert "DEMO-BETA" in symbols
        assert "DEMO-BENCHMARK" in symbols


class TestAuthEndpoints:
    def test_register_and_login(self, client):
        resp = client.post("/api/auth/register", json={
            "email": "newuser@alethra.io",
            "password": "securepass123",
            "display_name": "New User",
            "user_type": "individual",
        })
        assert resp.status_code == 201
        user = resp.json()
        assert user["email"] == "newuser@alethra.io"

        login = client.post("/api/auth/login", json={
            "email": "newuser@alethra.io",
            "password": "securepass123",
        })
        assert login.status_code == 200
        assert "access_token" in login.json()

    def test_me_endpoint(self, client, auth_headers):
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["email"] == "test@alethra.io"


class TestExperimentsEndpoints:
    def _create_experiment(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Test Experiment",
            "hypothesis": "Testing buy and hold",
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
        return resp.json()

    def test_create_experiment(self, client, auth_headers):
        exp = self._create_experiment(client, auth_headers)
        assert exp["title"] == "Test Experiment"
        assert exp["status"] == "draft"
        assert exp["current_version_id"] is not None

    def test_list_experiments(self, client, auth_headers):
        self._create_experiment(client, auth_headers)
        resp = client.get("/api/v1/experiments/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_get_experiment(self, client, auth_headers):
        exp = self._create_experiment(client, auth_headers)
        resp = client.get(f"/api/v1/experiments/{exp['id']}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == exp["id"]


class TestRunsEndpoints:
    def _create_experiment_and_version(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Run Test",
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
        return resp.json()

    def test_create_run(self, client, auth_headers):
        exp = self._create_experiment_and_version(client, auth_headers)
        version_id = exp["current_version_id"]

        resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=auth_headers)
        assert resp.status_code == 201
        run = resp.json()
        assert run["status"] == "completed"
        assert run["config_hash"] is not None
        assert run["dataset_checksum"] is not None
        assert run["metrics"] is not None

    def test_get_run(self, client, auth_headers):
        exp = self._create_experiment_and_version(client, auth_headers)
        version_id = exp["current_version_id"]

        create_resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=auth_headers)
        run_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == run_id


SPEC = {
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
}


def _make_experiment(client, headers, **overrides):
    body = {
        "title": "Event Test",
        "research_type": "quantitative",
        "strategy_spec": SPEC,
        **overrides,
    }
    resp = client.post("/api/v1/experiments/", headers=headers, json=body)
    assert resp.status_code == 201
    return resp.json()


class TestExperimentCreationEvents:
    def test_create_emits_three_events(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        resp = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers)
        assert resp.status_code == 200
        events = resp.json()
        event_types = [e["event_type"] for e in events]

        assert "experiment_created" in event_types
        assert "dataset_selected" in event_types
        assert "strategy_template_selected" in event_types

    def test_creation_events_have_correct_quality_metadata(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        resp = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers)
        events = resp.json()

        for e in events:
            assert e["recorded_before_outcome"] is True
            assert e["actor_type"] == "human"
            assert e["data_cutoff_at"] == "2024-02-14"


class TestHypothesisUpdateEvents:
    def test_updating_hypothesis_emits_event(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers, hypothesis="Original")
        eid = exp["id"]

        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "hypothesis": "Revised hypothesis",
        })
        assert resp.status_code == 200

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        event_types = [e["event_type"] for e in timeline]
        assert "hypothesis_updated" in event_types

        evt = next(e for e in timeline if e["event_type"] == "hypothesis_updated")
        assert evt["payload_json"]["new_hypothesis"] == "Revised hypothesis"
        assert evt["recorded_before_outcome"] is True

    def test_updating_falsification_conditions_emits_event(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "falsification_conditions": "Drawdown > 20%",
        })
        assert resp.status_code == 200

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        event_types = [e["event_type"] for e in timeline]
        assert "falsification_condition_added" in event_types


class TestStrategyChangeEvents:
    def test_strategy_change_without_reason_returns_400(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        modified_spec = {**SPEC, "initial_cash": 20000}
        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "strategy_spec": modified_spec,
        })
        assert resp.status_code == 400
        assert "change_reason" in resp.json()["detail"]

    def test_strategy_change_with_reason_emits_events(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        modified_spec = {**SPEC, "initial_cash": 20000}
        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "strategy_spec": modified_spec,
            "change_reason": "Testing with more capital",
        })
        assert resp.status_code == 200

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        event_types = [e["event_type"] for e in timeline]
        assert "parameter_changed" in event_types
        assert "strategy_version_created" in event_types

        param_evt = next(e for e in timeline if e["event_type"] == "parameter_changed")
        assert param_evt["payload_json"]["change_reason"] == "Testing with more capital"
        assert param_evt["data_cutoff_at"] == "2024-02-14"

    def test_title_change_does_not_require_change_reason(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "title": "Renamed Experiment",
        })
        assert resp.status_code == 200
        assert resp.json()["title"] == "Renamed Experiment"


class TestRunEvents:
    def test_run_emits_started_and_completed(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        version_id = exp["current_version_id"]

        resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=auth_headers)
        assert resp.status_code == 201
        run_id = resp.json()["id"]

        timeline = client.get(f"/api/v1/experiments/{exp['id']}/timeline", headers=auth_headers).json()
        run_events = [e for e in timeline if e["entity_type"] == "run" and e["entity_id"] == run_id]
        event_types = [e["event_type"] for e in run_events]

        assert "research_run_started" in event_types
        assert "research_run_completed" in event_types

        started = next(e for e in run_events if e["event_type"] == "research_run_started")
        assert started["recorded_before_outcome"] is True

        completed = next(e for e in run_events if e["event_type"] == "research_run_completed")
        assert completed["recorded_before_outcome"] is False

    def test_warnings_emit_system_events(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        version_id = exp["current_version_id"]

        resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=auth_headers)
        assert resp.status_code == 201
        run = resp.json()

        if run.get("warnings") and len(run["warnings"]) > 0:
            run_id = run["id"]
            timeline = client.get(f"/api/v1/experiments/{exp['id']}/timeline", headers=auth_headers).json()
            warning_events = [e for e in timeline if e["event_type"] == "warning_generated" and e["entity_id"] == run_id]

            assert len(warning_events) == len(run["warnings"])
            for we in warning_events:
                assert we["actor_type"] == "system"
                assert we["recorded_before_outcome"] is False


class TestTimelineEndpoint:
    def test_timeline_chronological_order(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers, hypothesis="v1")
        eid = exp["id"]

        client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "hypothesis": "v2",
        })

        resp = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers)
        assert resp.status_code == 200
        events = resp.json()
        assert len(events) >= 4

        for i in range(1, len(events)):
            assert events[i]["created_at"] >= events[i - 1]["created_at"]

    def test_timeline_owner_only(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        other = client.post("/api/auth/register", json={
            "email": "other@alethra.io",
            "password": "testpass123",
            "display_name": "Other",
            "user_type": "individual",
        })
        other_login = client.post("/api/auth/login", json={
            "email": "other@alethra.io",
            "password": "testpass123",
        })
        other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

        resp = client.get(f"/api/v1/experiments/{eid}/timeline", headers=other_headers)
        assert resp.status_code == 404


class TestNotesEndpoints:
    def test_create_and_list_notes(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        resp = client.post(f"/api/v1/experiments/{eid}/notes", headers=auth_headers, json={
            "content": "First observation",
        })
        assert resp.status_code == 201
        note = resp.json()
        assert note["content"] == "First observation"
        assert note["experiment_id"] == eid

        resp = client.get(f"/api/v1/experiments/{eid}/notes", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_note_emits_event(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        client.post(f"/api/v1/experiments/{eid}/notes", headers=auth_headers, json={
            "content": "Noting something",
        })

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        event_types = [e["event_type"] for e in timeline]
        assert "research_note_added" in event_types


class TestVersionComparison:
    def test_compare_identical_versions(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]
        v1 = exp["current_version_id"]

        versions = client.get(f"/api/v1/experiments/{eid}/versions", headers=auth_headers).json()
        assert len(versions) == 1

        resp = client.get(f"/api/v1/versions/compare?v1={v1}&v2={v1}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["diffs"] == []
        assert data["version_a_number"] == 1
        assert data["version_b_number"] == 1

    def test_compare_different_versions(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]
        v1 = exp["current_version_id"]

        modified_spec = {**SPEC, "initial_cash": 50000, "commission_rate": 0.002}
        client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "strategy_spec": modified_spec,
            "change_reason": "More capital, higher fees",
        })

        versions = client.get(f"/api/v1/experiments/{eid}/versions", headers=auth_headers).json()
        v2_id = None
        for v in versions:
            if v["version_number"] == 2:
                v2_id = v["id"]
                break
        assert v2_id is not None

        resp = client.get(f"/api/v1/versions/compare?v1={v1}&v2={v2_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["version_a_number"] == 1
        assert data["version_b_number"] == 2
        assert len(data["diffs"]) >= 2

        diff_fields = {d["field"] for d in data["diffs"]}
        assert "initial_cash" in diff_fields
        assert "commission_rate" in diff_fields

        cash_diff = next(d for d in data["diffs"] if d["field"] == "initial_cash")
        assert cash_diff["old_value"] == 10000
        assert cash_diff["new_value"] == 50000


class TestExportEndpoint:
    def test_export_contains_all_sections(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers, hypothesis="Test hypothesis")
        eid = exp["id"]

        client.post(f"/api/v1/experiments/{eid}/notes", headers=auth_headers, json={
            "content": "Export test note",
        })

        version_id = exp["current_version_id"]
        client.post(f"/api/v1/versions/{version_id}/runs", headers=auth_headers)

        resp = client.get(f"/api/v1/experiments/{eid}/export", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert "experiment" in data
        assert data["experiment"]["hypothesis"] == "Test hypothesis"
        assert data["experiment"]["falsification_conditions"] is not None or data["experiment"]["falsification_conditions"] is None

        assert "version_history" in data
        assert len(data["version_history"]) >= 1

        assert "current_version" in data

        assert "runs" in data
        assert len(data["runs"]) >= 1

        assert "events" in data
        assert len(data["events"]) >= 1
        event_types = {e["event_type"] for e in data["events"]}
        assert "experiment_created" in event_types

        assert "notes" in data
        assert len(data["notes"]) == 1
        assert data["notes"][0]["content"] == "Export test note"

        assert "disclaimer" in data
        assert len(data["disclaimer"]) > 0
        assert "disclaimer_zh" in data
        assert "disclaimer_short" in data
        assert "disclaimer_short_zh" in data

    def test_export_emits_export_event(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        client.get(f"/api/v1/experiments/{eid}/export", headers=auth_headers)

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        event_types = [e["event_type"] for e in timeline]
        assert "export_generated" in event_types


class TestStrategyLocking:
    def test_locking_emits_strategy_locked(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "scope": {"status": "locked"},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "locked"

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        lock_events = [e for e in timeline if e["event_type"] == "strategy_locked"]
        assert len(lock_events) == 1
        assert lock_events[0]["rules_locked"] is True
        assert lock_events[0]["recorded_before_outcome"] is True

    def test_locked_experiment_rejects_modification(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]

        client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "scope": {"status": "locked"},
        })

        resp = client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "hypothesis": "Should fail",
        })
        assert resp.status_code == 400

    def test_run_on_locked_experiment_records_rules_locked(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers)
        eid = exp["id"]
        version_id = exp["current_version_id"]

        client.put(f"/api/v1/experiments/{eid}", headers=auth_headers, json={
            "scope": {"status": "locked"},
        })

        resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=auth_headers)
        assert resp.status_code == 201
        run_id = resp.json()["id"]

        timeline = client.get(f"/api/v1/experiments/{eid}/timeline", headers=auth_headers).json()
        run_events = [e for e in timeline if e["entity_type"] == "run" and e["entity_id"] == run_id]
        for e in run_events:
            assert e["rules_locked"] is True
