"""Read-only strategy catalogue and preflight contract.

These tests pin the new endpoints to the existing contract: the StrategySpec
schema, the engine's signal-parameter rules, and the create path. They also
prove the preflight writes nothing and dispatches nothing.
"""
import pytest
from pydantic import ValidationError

from app.api.schemas import ALLOWED_TEMPLATES, ExperimentCreate, StrategySpec
from app.engine.backtest import SIGNAL_PARAMETER_RULES


def make_spec(template: str = "buy_and_hold", **overrides):
    spec = {
        "schema_version": "1.0",
        "template_type": template,
        "asset_ids": ["DEMO-ALPHA"],
        "frequency": "1d",
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "initial_cash": 10000,
        "commission_rate": 0.001,
        "slippage_rate": 0.0005,
        "signal_parameters": {},
        "risk_parameters": {},
    }
    if template == "sma_crossover":
        spec["signal_parameters"] = {"short_window": 10, "long_window": 30}
    elif template == "price_change_fixed_hold":
        spec["signal_parameters"] = {
            "lookback_days": 5,
            "threshold_pct": 0.05,
            "holding_days": 10,
        }
    spec.update(overrides)
    return spec


def test_catalog_lists_exactly_the_supported_templates(client, auth_headers):
    response = client.get("/api/v1/strategy-templates", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert [entry["name"] for entry in body["templates"]] == list(ALLOWED_TEMPLATES)
    assert all(entry["supported"] is True for entry in body["templates"])
    listed = {entry["name"] for entry in body["templates"]}
    assert not listed.intersection(entry["name"] for entry in body["unsupported"])
    for entry in body["templates"]:
        assert entry["purpose"]
        assert entry["known_limits"]
        assert "does not establish" in body["scope_note"]


def test_catalog_matches_the_schema_contract(client, auth_headers):
    body = client.get("/api/v1/strategy-templates", headers=auth_headers).json()
    required = {name for name, field in StrategySpec.model_fields.items() if field.is_required()}
    for entry in body["templates"]:
        assert set(entry["required_fields"]) == required
    fields = {field["name"] for field in body["spec_fields"]}
    assert fields == set(StrategySpec.model_fields)


def test_catalog_signal_parameters_match_engine_rules(client, auth_headers):
    body = client.get("/api/v1/strategy-templates", headers=auth_headers).json()
    for entry in body["templates"]:
        rules = SIGNAL_PARAMETER_RULES[entry["name"]]
        assert [param["name"] for param in entry["signal_parameters"]] == list(rules)
        for param in entry["signal_parameters"]:
            rule = rules[param["name"]]
            assert param["type"] == rule["type"]
            assert param["min"] == rule["min"]
            assert param["max"] == rule["max"]


@pytest.mark.parametrize("template", ALLOWED_TEMPLATES)
def test_valid_spec_is_accepted_by_preflight_and_the_create_contract(
    client, auth_headers, template
):
    spec = make_spec(template)
    ExperimentCreate.model_validate({"title": "contract parity", "strategy_spec": spec})
    response = client.post(
        "/api/v1/strategy-specs/preflight", headers=auth_headers, json=spec
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert body["template_type"] == template
    assert body["errors"] == []


@pytest.mark.parametrize(
    "mutate, expected_field",
    [
        (lambda s: s.pop("asset_ids"), "asset_ids"),
        (lambda s: s.update(frequency="5m"), "frequency"),
        (lambda s: s.update(end_date="2024-01-01"), "end_date"),
        (lambda s: s.update(initial_cash=0), "initial_cash"),
        (lambda s: s.update(asset_ids=["NOT A SYMBOL"]), "asset_ids"),
        (lambda s: s.update(commission_rate=0.5), "commission_rate"),
    ],
)
def test_schema_errors_match_the_create_contract(
    client, auth_headers, mutate, expected_field
):
    spec = make_spec("buy_and_hold")
    mutate(spec)
    with pytest.raises(ValidationError):
        ExperimentCreate.model_validate({"title": "rejected", "strategy_spec": spec})
    body = client.post(
        "/api/v1/strategy-specs/preflight", headers=auth_headers, json=spec
    ).json()
    assert body["status"] == "invalid"
    assert expected_field in {error["field"] for error in body["errors"]}


@pytest.mark.parametrize(
    "template, signal_parameters, message",
    [
        ("sma_crossover", {"short_window": 30, "long_window": 10}, "short_window"),
        ("sma_crossover", {"short_window": 1, "long_window": 30}, "between 2 and 60"),
        ("sma_crossover", {"long_window": 30}, "short_window"),
        ("price_change_fixed_hold", {"lookback_days": 5, "threshold_pct": 5, "holding_days": 10}, "0.01-0.50"),
    ],
)
def test_engine_signal_rule_errors_are_reported(
    client, auth_headers, template, signal_parameters, message
):
    spec = make_spec(template, signal_parameters=signal_parameters)
    body = client.post(
        "/api/v1/strategy-specs/preflight", headers=auth_headers, json=spec
    ).json()
    assert body["status"] == "invalid"
    assert body["errors"]
    assert any(message in error["message"] for error in body["errors"])
    assert all(error["field"] == "signal_parameters" for error in body["errors"])


def test_unsupported_template_is_distinct_from_field_errors(client, auth_headers):
    spec = make_spec("mean_reversion_zscore")
    body = client.post(
        "/api/v1/strategy-specs/preflight", headers=auth_headers, json=spec
    ).json()
    assert body["status"] == "unsupported"
    assert "mean_reversion_zscore" in body["reason"]
    assert body["errors"] == []


def test_intraday_spec_is_unsupported_on_the_batch_path(client, auth_headers):
    spec = make_spec("buy_and_hold", timeframe="5m")
    body = client.post(
        "/api/v1/strategy-specs/preflight", headers=auth_headers, json=spec
    ).json()
    assert body["status"] == "unsupported"
    assert body["errors"][0]["field"] == "timeframe"


def test_declared_daily_timeframe_is_valid_and_an_unknown_grain_is_invalid(
    client, auth_headers
):
    """Unsupported is reserved for the grains the intraday path really runs.

    "4h" is not a member of INTRADAY_TIMEFRAMES, so it stays a schema error
    rather than being advertised as a capability that exists elsewhere.
    """
    declared = client.post(
        "/api/v1/strategy-specs/preflight",
        headers=auth_headers,
        json=make_spec("buy_and_hold", timeframe="1d"),
    ).json()
    assert declared["status"] == "valid"
    assert declared["errors"] == []

    unknown = client.post(
        "/api/v1/strategy-specs/preflight",
        headers=auth_headers,
        json=make_spec("buy_and_hold", timeframe="4h"),
    ).json()
    assert unknown["status"] == "invalid"
    assert "timeframe" in {error["field"] for error in unknown["errors"]}


def test_preflight_requires_authentication(client):
    response = client.post(
        "/api/v1/strategy-specs/preflight", json=make_spec("buy_and_hold")
    )
    assert response.status_code == 401
    assert client.get("/api/v1/strategy-templates").status_code == 401


def test_preflight_writes_nothing_and_dispatches_nothing(
    client, auth_headers, monkeypatch
):
    from app.engine import backtest as backtest_module
    from app.models.audit_event import AuditEvent
    from app.models.experiment import Experiment
    from app.models.experiment_version import ExperimentVersion
    from tests.conftest import TestSession

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not execute or fetch data")

    monkeypatch.setattr(backtest_module, "run_backtest", forbidden)
    monkeypatch.setattr(backtest_module, "get_demo_ohlcv", forbidden)

    db = TestSession()
    try:
        before = (
            db.query(Experiment).count(),
            db.query(ExperimentVersion).count(),
            db.query(AuditEvent).count(),
        )
    finally:
        db.close()

    for template in ALLOWED_TEMPLATES:
        response = client.post(
            "/api/v1/strategy-specs/preflight",
            headers=auth_headers,
            json=make_spec(template),
        )
        assert response.json()["status"] == "valid"

    db = TestSession()
    try:
        after = (
            db.query(Experiment).count(),
            db.query(ExperimentVersion).count(),
            db.query(AuditEvent).count(),
        )
    finally:
        db.close()
    assert after == before


def test_error_responses_do_not_leak_internal_detail(client, auth_headers):
    spec = make_spec("buy_and_hold")
    spec["start_date"] = "not-a-date"
    spec["end_date"] = "still-not-a-date"
    response = client.post(
        "/api/v1/strategy-specs/preflight", headers=auth_headers, json=spec
    )
    text = response.text.lower()
    assert "traceback" not in text
    assert "sqlalchemy" not in text
    assert "postgresql://" not in text


def test_routes_are_published_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/strategy-templates" in paths
    assert "/api/v1/strategy-specs/preflight" in paths
