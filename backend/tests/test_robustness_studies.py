"""Robustness studies: bounded plans, honest refusals, no duplicate runs.

These tests exercise the real engine on the built-in synthetic demo series.
They check behaviour the product promises: the plan is priced before it runs,
a study never edits the strategy, every attempt is a real research run, and
resuming fills only the cells that are missing.
"""
import uuid

import pytest

from tests.conftest import TestSession


BUY_HOLD_SPEC = {
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

SMA_SPEC = {
    **BUY_HOLD_SPEC,
    "template_type": "sma_crossover",
    "signal_parameters": {"short_window": 5, "long_window": 20},
}


def _create_experiment(client, headers, spec=None, title="Robustness Experiment") -> dict:
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "hypothesis": "Costs and periods change the conclusion",
            "research_type": "quantitative",
            "strategy_spec": dict(spec or BUY_HOLD_SPEC),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _uid(value) -> uuid.UUID:
    return uuid.UUID(str(value))


def _user_id(email: str = "test@alethra.io") -> uuid.UUID:
    from app.models.user import User

    db = TestSession()
    try:
        return db.query(User).filter(User.email == email).first().id
    finally:
        db.close()


def _grant_research_runs(limit: int = 100, email: str = "test@alethra.io") -> None:
    from app.models.pilot import PilotEntitlement

    db = TestSession()
    try:
        existing = (
            db.query(PilotEntitlement)
            .filter(PilotEntitlement.user_id == _user_id(email))
            .first()
        )
        if existing:
            existing.research_run_limit = limit
            existing.pilot_access = True
        else:
            db.add(
                PilotEntitlement(
                    user_id=_user_id(email),
                    pilot_access=True,
                    research_run_limit=limit,
                )
            )
        db.commit()
    finally:
        db.close()


def _plan(client, headers, experiment_id, version_id, kind, config=None) -> dict:
    resp = client.post(
        f"/api/v1/experiments/{experiment_id}/robustness/plan",
        headers=headers,
        json={"version_id": version_id, "kind": kind, "config": config},
    )
    return resp


def _create_study(client, headers, experiment_id, version_id, kind, config=None, request_id=None):
    body = {"version_id": version_id, "kind": kind, "config": config}
    if request_id:
        body["request_id"] = request_id
    return client.post(
        f"/api/v1/experiments/{experiment_id}/robustness",
        headers=headers,
        json=body,
    )


def _run(client, headers, study_id):
    return client.post(f"/api/v1/robustness/{study_id}/run", headers=headers)


def _quota_used() -> int:
    from app.models.quota import QuotaCounter

    db = TestSession()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == _user_id(),
                QuotaCounter.quota_type == "research_run",
            )
            .first()
        )
        return counter.used if counter else 0
    finally:
        db.close()


def _research_run_ids() -> set:
    from app.models.research_run import ResearchRun

    db = TestSession()
    try:
        return {str(r.id) for r in db.query(ResearchRun).all()}
    finally:
        db.close()


def _register_second_user(client) -> dict:
    email = f"other-{uuid.uuid4().hex[:8]}@alethra.io"
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "otherpass123",
            "display_name": "Other User",
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "otherpass123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


class TestPlan:
    def test_cost_plan_is_bounded_priced_and_disclosed(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert resp.status_code == 200, resp.text
        plan = resp.json()

        assert plan["kind"] == "cost_scenarios"
        assert plan["estimated_attempts"] == 9 == len(plan["cells"])
        assert plan["max_attempts"] == 12
        assert plan["calendar"]["source"] == "builtin_demo"
        assert plan["calendar"]["natures"] == {"DEMO-ALPHA": "synthetic"}
        assert plan["calendar"]["bars"] == 29

        base_cells = [c for c in plan["cells"] if c["role"] == "baseline"]
        assert len(base_cells) == 1
        assert base_cells[0]["spec"]["commission_rate"] == 0.001
        assert base_cells[0]["spec"]["slippage_rate"] == 0.001

        # Every cell carries the full settings that will actually be run.
        for cell in plan["cells"]:
            assert cell["spec"]["template_type"] == "buy_and_hold"
            assert cell["spec"]["asset_ids"] == ["DEMO-ALPHA"]
            assert cell["period_start"] and cell["period_end"]
            assert cell["overrides"]["commission_rate"] == cell["spec"]["commission_rate"]

        joined = " ".join(plan["disclosures"])
        assert "the best single result is not used to stand in for" in joined
        assert "Built-in synthetic demo data (not real market prices)" in joined
        assert "the whole study is one research-run unit" in joined

        # Nine backtests, but one authorization unit: the free plan's five
        # units can start this study, and the plan says so before any run.
        assert plan["authorization_required"] == 1
        assert plan["estimated_backtests"] == 9
        assert plan["quota"]["required"] == 1
        assert plan["quota"]["estimated_attempts"] == 9
        assert plan["quota"]["sufficient"] is True
        assert plan["ready"] is True
        assert plan["required_bars_rule"]

        # The free plan grants five authorizations, and one unit starts this
        # study, so the plan must not label it permanently unreachable.
        assert plan["reachability"]["required"] == 1
        assert plan["reachability"]["limit"] == 5
        assert plan["reachability"]["permanently_unreachable"] is False
        assert plan["reachability"]["reason"] is None
        assert plan["quota"]["permanently_unreachable"] is False
        assert plan["quota"]["limit_block_reason"] is None

    def test_required_bars_follows_long_window(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers, spec=SMA_SPEC)
        resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["required_bars"] == 21  # long_window 20 + 1
        assert "long_window (20) + 1" in plan["required_bars_rule"]

    def test_required_bars_clamps_holding_days_at_five(self, client, auth_headers):
        spec = {
            **BUY_HOLD_SPEC,
            "template_type": "price_change_fixed_hold",
            "signal_parameters": {
                "lookback_days": 3,
                "threshold_pct": 0.05,
                "holding_days": 8,
            },
        }
        exp = _create_experiment(client, auth_headers, spec=spec)
        resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["required_bars"] == 9  # 3 + min(8, 5) + 1
        assert "min(holding_days, 5) (5)" in plan["required_bars_rule"]

    def test_parameter_sensitivity_changes_one_parameter_at_a_time(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers, spec=SMA_SPEC)
        resp = _plan(
            client,
            auth_headers,
            exp["id"],
            exp["current_version_id"],
            "parameter_sensitivity",
        )
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["estimated_attempts"] == 5

        base_params = SMA_SPEC["signal_parameters"]
        for cell in plan["cells"]:
            spec = cell["spec"]
            changed = {
                key: value
                for key, value in spec["signal_parameters"].items()
                if base_params.get(key) != value
            }
            if cell["role"] == "baseline":
                assert changed == {}
            else:
                assert len(changed) == 1
                assert set(changed) == set(cell["overrides"])
            # Everything outside the tuned parameter stays identical.
            for key in ("template_type", "asset_ids", "start_date", "end_date", "initial_cash"):
                assert spec[key] == SMA_SPEC[key]

    def test_period_slices_refused_when_each_slice_too_short(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers, spec=SMA_SPEC)
        resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "period_slices"
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "robustness_refused"
        assert "21" in detail["message"]  # long_window 20 + 1
        assert "longer period" in detail["message"]

    def test_walk_forward_refused_when_windows_too_short(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers, spec=SMA_SPEC)
        resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "walk_forward"
        )
        assert resp.status_code == 400, resp.text
        assert "for training" in resp.json()["detail"]["message"]

    def test_intraday_version_is_refused_not_downgraded(self, client, auth_headers):
        from app.models.experiment_version import ExperimentVersion

        exp = _create_experiment(client, auth_headers)
        db = TestSession()
        try:
            version = db.query(ExperimentVersion).filter(
                ExperimentVersion.id == _uid(exp["current_version_id"])
            ).first()
            spec = dict(version.strategy_spec)
            spec["frequency"] = "5m"
            version.strategy_spec = spec
            db.commit()
        finally:
            db.close()

        resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert resp.status_code == 400, resp.text
        assert "not silently downgraded to daily" in resp.json()["detail"]["message"]


class TestExecution:
    def test_cost_study_runs_every_cell_as_a_real_run(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        used_before = _quota_used()
        runs_before = _research_run_ids()

        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert created.status_code == 201, created.text
        study = created.json()
        assert study["status"] == "planned"
        assert study["attempts"] == []

        resp = _run(client, auth_headers, study["id"])
        assert resp.status_code == 200, resp.text
        detail = resp.json()

        assert detail["status"] == "completed"
        assert len(detail["attempts"]) == 9
        assert all(a["status"] == "completed" for a in detail["attempts"])
        assert all(a["run_id"] for a in detail["attempts"])
        assert set(a["run_id"] for a in detail["attempts"]) == (
            _research_run_ids() - runs_before
        )
        # Nine real research runs, one authorization unit.
        assert _quota_used() == used_before + 1
        assert detail["authorization"]["unit"] == "study"
        assert detail["authorization"]["required"] == 1
        assert detail["authorization"]["estimated_attempts"] == 9
        assert detail["authorization"]["state"] == "charged"
        assert detail["authorization"]["charge_on_next_run"] is False
        assert detail["authorization"]["reservation_id"]
        assert detail["replayable_cells"] == []

        # Same dataset, different costs: checksum must be identical everywhere.
        assert len({a["dataset_checksum"] for a in detail["attempts"]}) == 1
        for attempt in detail["attempts"]:
            identity = attempt["data_identity"]
            assert identity["data_source_used"] in ("provider", "mixed", "stored")
            assert identity["data_nature"] == {"DEMO-ALPHA": "synthetic"}
            assert identity["start_date"] == "2024-01-02"

        agg = detail["aggregate"]
        assert agg["scope"] == "all_attempts"
        assert agg["attempts_total"] == 9
        assert agg["counts"]["completed"] == 9
        stats = agg["return_stats"]
        assert stats["count"] == 9
        assert stats["min"] <= stats["median"] <= stats["max"]
        assert stats["spread"] == pytest.approx(stats["max"] - stats["min"], abs=1e-4)
        assert "The best single result is not robustness evidence" in agg["note"]
        assert agg["selection_rule"]

        by_key = {a["cell_key"]: a for a in detail["attempts"]}
        free = by_key["cost:c=0.0:s=0.0"]["metrics"]["total_return_pct"]
        costly = by_key["cost:c=0.002:s=0.002"]["metrics"]["total_return_pct"]
        assert free > costly

        listed = client.get(
            f"/api/v1/experiments/{exp['id']}/robustness", headers=auth_headers
        )
        assert listed.status_code == 200
        assert [s["id"] for s in listed.json()] == [study["id"]]

    def test_period_slices_are_contiguous_and_do_not_overlap(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client,
            auth_headers,
            exp["id"],
            exp["current_version_id"],
            "period_slices",
            config={"slices": 3},
        )
        assert created.status_code == 201, created.text
        resp = _run(client, auth_headers, created.json()["id"])
        assert resp.status_code == 200, resp.text
        detail = resp.json()

        assert detail["status"] == "completed"
        attempts = sorted(detail["attempts"], key=lambda a: a["period_start"])
        assert len(attempts) == 3
        for previous, current in zip(attempts, attempts[1:]):
            assert previous["period_end"] < current["period_start"]
            assert current["period_start"] > previous["period_end"]
        assert attempts[0]["period_start"] == "2024-01-02"
        # The demo series has no bar for 2024-02-14, so the last slice must stop
        # at the last date that actually has a quote inside the requested period.
        assert attempts[-1]["period_end"] == "2024-02-13"
        disclosures = [c for c in detail["plan"]["disclosures"] if "requested period" in c]
        assert any("2024-02-13" in c for c in disclosures)

        for attempt in attempts:
            identity = attempt["data_identity"]
            assert identity["start_date"] == attempt["period_start"]
            assert identity["end_date"] == attempt["period_end"]

        assert detail["aggregate"]["scope"] == "all_attempts"
        assert detail["aggregate"]["return_stats"]["count"] == 3

    def test_walk_forward_selects_on_train_and_measures_on_test(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client,
            auth_headers,
            exp["id"],
            exp["current_version_id"],
            "walk_forward",
            config={"folds": 2},
        )
        assert created.status_code == 201, created.text
        resp = _run(client, auth_headers, created.json()["id"])
        assert resp.status_code == 200, resp.text
        detail = resp.json()

        selection = [a for a in detail["attempts"] if a["role"] == "selection"]
        measurement = [a for a in detail["attempts"] if a["role"] == "measurement"]
        assert len(selection) == 2
        assert len(measurement) == 2
        assert all(a["status"] == "completed" for a in measurement)

        for measured in measurement:
            fold = measured["overrides"]["fold"]
            train_cells = [
                a for a in selection if a["cell_key"].startswith(f"{fold}:")
            ]
            assert train_cells, "each fold must have train-side attempts"
            assert measured["overrides"]["selected_from"].startswith(f"{fold}:cand:")
            # Test period begins after training ends, with one untouched bar between.
            train_end = max(
                a["period_end"] for a in detail["attempts"]
                if a["cell_key"].startswith(f"{fold}:cand:")
            )
            assert measured["period_start"] > train_end
            assert measured["data_identity"]["start_date"] == measured["period_start"]
            assert measured["data_identity"]["end_date"] == measured["period_end"]

        agg = detail["aggregate"]
        assert agg["scope"] == "measurement_only"
        assert agg["return_stats"]["count"] == 2
        assert agg["attempts_total"] == 4
        assert [f["fold"] for f in agg["folds"]] == ["1", "2"]

    def test_rerun_does_not_duplicate_attempts_or_runs(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client,
            auth_headers,
            exp["id"],
            exp["current_version_id"],
            "period_slices",
            config={"slices": 3},
        )
        study_id = created.json()["id"]
        assert _run(client, auth_headers, study_id).status_code == 200
        attempts_after_first = len(
            client.get(f"/api/v1/robustness/{study_id}", headers=auth_headers).json()["attempts"]
        )
        runs_after_first = _research_run_ids()
        used_after_first = _quota_used()

        again = _run(client, auth_headers, study_id)
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "completed"
        assert len(again.json()["attempts"]) == attempts_after_first
        assert _research_run_ids() == runs_after_first
        assert _quota_used() == used_after_first

    def test_resume_fills_only_the_missing_cell(self, client, auth_headers):
        from app.models.robustness import RobustnessAttempt, RobustnessStudy

        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client,
            auth_headers,
            exp["id"],
            exp["current_version_id"],
            "period_slices",
            config={"slices": 3},
        )
        study_id = created.json()["id"]
        assert _run(client, auth_headers, study_id).status_code == 200

        db = TestSession()
        try:
            attempt = (
                db.query(RobustnessAttempt)
                .filter(RobustnessAttempt.study_id == _uid(study_id))
                .first()
            )
            broken_cell = attempt.cell_key
            attempt.status = "failed"
            attempt.metrics = None
            attempt.error_details = "simulated interruption"
            study = (
                db.query(RobustnessStudy)
                .filter(RobustnessStudy.id == _uid(study_id))
                .first()
            )
            study.status = "partial"
            db.commit()
        finally:
            db.close()

        used_before = _quota_used()
        resumed = _run(client, auth_headers, study_id)
        assert resumed.status_code == 200, resumed.text
        detail = resumed.json()
        assert detail["status"] == "completed"
        assert len(detail["attempts"]) == 3
        refilled = next(a for a in detail["attempts"] if a["cell_key"] == broken_cell)
        assert refilled["status"] == "completed"
        # Finishing a study that already holds its authorization is free.
        assert _quota_used() == used_before

    def test_cancel_before_run_blocks_execution(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "period_slices"
        )
        study_id = created.json()["id"]
        cancelled = client.post(
            f"/api/v1/robustness/{study_id}/cancel", headers=auth_headers
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        blocked = _run(client, auth_headers, study_id)
        assert blocked.status_code == 400
        assert "This study is cancelled" in blocked.json()["detail"]["message"]
        assert blocked.json()["detail"]["error"] == "robustness_refused"

        assert _quota_used() == 0

    def test_study_does_not_mutate_the_strategy_version(self, client, auth_headers):
        from app.models.experiment import Experiment
        from app.models.experiment_version import ExperimentVersion

        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        version_id = exp["current_version_id"]

        db = TestSession()
        try:
            version = db.query(ExperimentVersion).filter(
                ExperimentVersion.id == _uid(version_id)
            ).first()
            snapshot = {
                "spec": dict(version.strategy_spec),
                "assumptions": version.assumptions,
                "locked_at": version.locked_at,
                "hash": version.strategy_config_hash,
            }
            versions_before = (
                db.query(ExperimentVersion)
                .filter(ExperimentVersion.experiment_id == _uid(exp["id"]))
                .count()
            )
        finally:
            db.close()

        created = _create_study(
            client,
            auth_headers,
            exp["id"],
            version_id,
            "period_slices",
            config={"slices": 3},
        )
        assert _run(client, auth_headers, created.json()["id"]).status_code == 200

        db = TestSession()
        try:
            version = db.query(ExperimentVersion).filter(
                ExperimentVersion.id == _uid(version_id)
            ).first()
            assert dict(version.strategy_spec) == snapshot["spec"]
            assert version.assumptions == snapshot["assumptions"]
            assert version.locked_at == snapshot["locked_at"]
            assert version.strategy_config_hash == snapshot["hash"]
            assert (
                db.query(ExperimentVersion)
                .filter(ExperimentVersion.experiment_id == _uid(exp["id"]))
                .count()
                == versions_before
            )
            experiment = db.query(Experiment).filter(Experiment.id == _uid(exp["id"])).first()
            assert str(experiment.current_version_id) == version_id
        finally:
            db.close()

    def test_request_id_replays_the_same_study(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        request_id = str(uuid.uuid4())
        first = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"],
            "period_slices", request_id=request_id,
        )
        second = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"],
            "period_slices", request_id=request_id,
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        listed = client.get(
            f"/api/v1/experiments/{exp['id']}/robustness", headers=auth_headers
        ).json()
        assert len(listed) == 1


class TestQuotaAndOwnership:
    def test_one_remaining_unit_starts_a_nine_cell_study(self, client, auth_headers):
        """The headline complaint: 5 units, a 9-cell study, nothing could start."""
        _grant_research_runs(1)
        exp = _create_experiment(client, auth_headers)
        used_before = _quota_used()

        plan = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["quota"]["remaining"] == 1
        assert plan.json()["ready"] is True

        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert created.status_code == 201, created.text
        study = created.json()
        assert study["authorization"]["charge_on_next_run"] is True
        assert len(study["replayable_cells"]) == 9

        ran = _run(client, auth_headers, study["id"])
        assert ran.status_code == 200, ran.text
        assert ran.json()["status"] == "completed"
        assert len(ran.json()["attempts"]) == 9
        assert _quota_used() == used_before + 1

    def test_insufficient_quota_refuses_before_any_run(self, client, auth_headers):
        _grant_research_runs(0)
        exp = _create_experiment(client, auth_headers)
        plan = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["authorization_required"] == 1
        assert plan.json()["quota"]["required"] == 1
        assert plan.json()["quota"]["estimated_attempts"] == 9
        assert plan.json()["quota"]["sufficient"] is False
        assert plan.json()["ready"] is False
        # A zero monthly limit is a permanent block, not a transient shortage:
        # waiting for usage to reset cannot make this study startable.
        assert plan.json()["reachability"]["permanently_unreachable"] is True
        assert plan.json()["quota"]["permanently_unreachable"] is True
        assert "monthly limit is 0" in plan.json()["reachability"]["reason"]

        resp = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "cost_scenarios"
        )
        assert resp.status_code == 429, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "insufficient_quota"
        assert detail["unit"] == "study"
        assert detail["required"] == 1
        assert detail["estimated_attempts"] == 9
        assert detail["remaining"] == 0
        assert detail["permanently_unreachable"] is True
        assert "monthly limit is 0" in detail["limit_block_reason"]
        assert detail["check_url"] == (
            "/api/v1/usage/quota?quota_type=research_run_limit&period=monthly"
        )
        # Both numbers stay in the sentence; the executable GET is appended,
        # not substituted for either number.
        assert "This plan needs 1 research-run authorization" in detail["message"]
        assert "but only 0 remain" in detail["message"]
        assert "the study did not start" in detail["message"]
        assert "GET /api/v1/usage/quota" in detail["message"]
        assert "monthly limit is 0" in detail["message"]

        listed = client.get(
            f"/api/v1/experiments/{exp['id']}/robustness", headers=auth_headers
        ).json()
        assert listed == []

    def test_refused_execution_releases_the_authorization(
        self, client, auth_headers, monkeypatch
    ):
        """A charge is released, not kept, when no cell produced evidence."""
        from app.api import robustness as robustness_api
        from app.services.robustness import RobustnessRefused

        _grant_research_runs(5)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "period_slices"
        )
        assert created.status_code == 201, created.text
        study_id = created.json()["id"]
        used_before = _quota_used()

        def _refuse(*args, **kwargs):
            raise RobustnessRefused("simulated refusal before any cell ran")

        monkeypatch.setattr(robustness_api, "execute_study", _refuse)
        refused = _run(client, auth_headers, study_id)
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"]["error"] == "robustness_refused"
        assert _quota_used() == used_before

        from app.models.robustness import RobustnessStudy

        db = TestSession()
        try:
            study = (
                db.query(RobustnessStudy)
                .filter(RobustnessStudy.id == _uid(study_id))
                .first()
            )
            assert study.authorization_reservation_id is None
        finally:
            db.close()

        monkeypatch.undo()
        retried = _run(client, auth_headers, study_id)
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "completed"
        assert _quota_used() == used_before + 1

    def test_concurrent_run_of_a_held_study_is_refused(self, client, auth_headers):
        """An in-flight claim means another request is executing those cells."""
        import uuid as _uuid

        from app.models.quota import QuotaReservation
        from app.models.robustness import RobustnessStudy
        from app.lib.timeutil import utcnow

        _grant_research_runs(5)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "period_slices"
        )
        assert created.status_code == 201, created.text
        study_id = created.json()["id"]
        used_before = _quota_used()

        db = TestSession()
        try:
            reservation = QuotaReservation(
                id=_uuid.uuid4(),
                user_id=_user_id(),
                quota_type="research_run",
                amount=1,
                status="reserved",
                created_at=utcnow(),
            )
            db.add(reservation)
            study = (
                db.query(RobustnessStudy)
                .filter(RobustnessStudy.id == _uid(study_id))
                .first()
            )
            study.authorization_reservation_id = reservation.id
            db.commit()
        finally:
            db.close()

        blocked = _run(client, auth_headers, study_id)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["error"] == "study_running"
        assert _quota_used() == used_before

    def test_foreign_users_cannot_plan_read_or_run(self, client, auth_headers):
        _grant_research_runs(100)
        exp = _create_experiment(client, auth_headers)
        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"], "period_slices"
        )
        study_id = created.json()["id"]
        other = _register_second_user(client)

        assert _plan(
            client, other, exp["id"], exp["current_version_id"], "period_slices"
        ).status_code == 404
        assert client.get(
            f"/api/v1/robustness/{study_id}", headers=other
        ).status_code == 404
        assert client.post(
            f"/api/v1/robustness/{study_id}/run", headers=other
        ).status_code == 404
        assert client.get(
            f"/api/v1/experiments/{exp['id']}/robustness", headers=other
        ).status_code == 404

    def test_expired_entitlement_is_not_reported_as_startable(
        self, client, auth_headers
    ):
        """Plan and create must agree: remaining units are not enough alone.

        An expired entitlement still has 100 unused research runs. The dry run
        used to say ``ready`` and the create endpoint then refused, so a user
        was told a study could start when it could not.
        """
        from datetime import timedelta

        from app.lib.timeutil import utcnow
        from app.models.pilot import PilotEntitlement

        _grant_research_runs(100)
        db = TestSession()
        try:
            entitlement = (
                db.query(PilotEntitlement)
                .filter(PilotEntitlement.user_id == _user_id())
                .one()
            )
            entitlement.expiry_date = utcnow() - timedelta(days=1)
            db.commit()
        finally:
            db.close()

        exp = _create_experiment(client, auth_headers)
        plan_resp = _plan(
            client, auth_headers, exp["id"], exp["current_version_id"],
            "period_slices",
        )
        assert plan_resp.status_code == 200, plan_resp.text
        plan = plan_resp.json()
        assert plan["quota"]["remaining"] == 100
        assert plan["quota"]["entitlement_active"] is False
        assert plan["ready"] is False

        created = _create_study(
            client, auth_headers, exp["id"], exp["current_version_id"],
            "period_slices",
        )
        assert created.status_code == 403, created.text
