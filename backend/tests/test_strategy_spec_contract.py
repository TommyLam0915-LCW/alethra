"""The published strategy contract must match what the engine actually reads.

The catalogue and preflight are discovery surfaces: a customer reads them to
decide what a spec field does. A field that is stored but never read must be
labelled as such, and a field the engine does read must not be omitted just
because its name differs from the obvious one (the engine executes
``risk_parameters.stop_price`` / ``stop_pct`` / ``target_price`` /
``target_pct``, not a field called ``stop_loss``).

Each test changes one thing and asserts the published answer flips with it.
"""
import pytest

from app.engine.backtest import SIGNAL_PARAMETER_RULES
from app.engine.exit_rules import PLAN_KEYS

BASE_SPEC = {
    "template_type": "sma_crossover",
    "asset_ids": ["US:AAPL"],
    "frequency": "1d",
    "start_date": "2024-01-02",
    "end_date": "2024-03-15",
    "initial_cash": 100000.0,
    "signal_parameters": {"short_window": 10, "long_window": 30},
}


def _catalogue(client, headers):
    resp = client.get("/api/v1/strategy-templates", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preflight(client, headers, spec):
    resp = client.post("/api/v1/strategy-specs/preflight", json=spec, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_every_spec_field_states_whether_the_engine_reads_it(client, auth_headers):
    body = _catalogue(client, auth_headers)
    fields = {row["name"]: row for row in body["spec_fields"]}

    for row in fields.values():
        assert isinstance(row["executed_by_engine"], bool)
        assert row["semantics"], f"{row['name']} has no semantics"

    # Stored-and-diffed only: no engine module consumes these blocks.
    for stored_only in ("entry_rule", "exit_rule", "sizing_rule", "execution_timing"):
        assert fields[stored_only]["executed_by_engine"] is False

    # Read by the engine on the run path.
    for executed in (
        "template_type",
        "asset_ids",
        "start_date",
        "end_date",
        "initial_cash",
        "signal_parameters",
        "risk_parameters",
        "commission_rate",
        "slippage_rate",
        "benchmark_id",
    ):
        assert fields[executed]["executed_by_engine"] is True, executed

    assert fields["schema_version"]["executed_by_engine"] is False
    assert fields["currency"]["executed_by_engine"] is False


def test_risk_section_lists_exactly_the_keys_the_engine_resolves(client, auth_headers):
    body = _catalogue(client, auth_headers)
    sections = {row["section"]: row for row in body["sections"]}

    assert sections["signal_parameters"]["executed_by_engine"] is True
    risk = sections["risk_parameters"]
    assert risk["executed_by_engine"] is True
    assert [key["key"] for key in risk["keys"]] == list(PLAN_KEYS)

    # The named stop/target keys must be discoverable without guessing a
    # "stop_loss" contract name the engine never used.
    published = {key["key"]: key for key in risk["keys"]}
    for name in ("stop_price", "stop_pct", "target_price", "target_pct"):
        assert published[name]["unit"], name
        assert published[name]["note"], name
    assert "ignored when stop_price is set" in published["stop_pct"]["note"]

    for stored_only in ("entry_rule", "exit_rule", "sizing_rule", "execution_timing"):
        assert sections[stored_only]["executed_by_engine"] is False
        assert sections[stored_only]["note"]


def test_template_parameters_carry_units_and_conflict_rules(client, auth_headers):
    body = _catalogue(client, auth_headers)
    templates = {row["name"]: row for row in body["templates"]}

    assert templates["buy_and_hold"]["signal_parameters"] == []

    for template_name, rules in SIGNAL_PARAMETER_RULES.items():
        published = {row["name"]: row for row in templates[template_name]["signal_parameters"]}
        assert set(published) == set(rules), template_name
        for name, row in published.items():
            assert row["unit"], f"{template_name}.{name} has no unit"
            assert row["min"] == rules[name]["min"]
            assert row["max"] == rules[name]["max"]
            assert row["required"] is True

    sma = {row["name"]: row for row in templates["sma_crossover"]["signal_parameters"]}
    assert "below long_window" in sma["short_window"]["conflict"]
    assert "above short_window" in sma["long_window"]["conflict"]


def test_preflight_accepts_a_legal_spec(client, auth_headers):
    body = _preflight(client, auth_headers, BASE_SPEC)
    assert body["status"] == "valid"
    assert body["template_type"] == "sma_crossover"
    assert body["errors"] == []


def test_preflight_reports_the_engine_cross_field_rule(client, auth_headers):
    inverted = dict(BASE_SPEC, signal_parameters={"short_window": 30, "long_window": 10})
    body = _preflight(client, auth_headers, inverted)
    assert body["status"] == "invalid"
    assert [issue["field"] for issue in body["errors"]] == ["signal_parameters"]
    assert "short_window must be less than long_window" in body["errors"][0]["message"]


def test_preflight_reports_a_missing_required_field_without_422(client, auth_headers):
    incomplete = {key: value for key, value in BASE_SPEC.items() if key != "initial_cash"}
    body = _preflight(client, auth_headers, incomplete)
    assert body["status"] == "invalid"
    assert any(issue["field"] == "initial_cash" for issue in body["errors"])


def test_preflight_separates_unsupported_from_invalid(client, auth_headers):
    unimplemented = dict(BASE_SPEC, template_type="mean_reversion_zscore")
    body = _preflight(client, auth_headers, unimplemented)
    assert body["status"] == "unsupported"
    assert "not implemented" in body["reason"]

    intraday = dict(BASE_SPEC, timeframe="5m")
    body = _preflight(client, auth_headers, intraday)
    assert body["status"] == "unsupported"
    assert body["errors"][0]["field"] == "timeframe"


def test_preflight_accepts_a_venue_qualified_crypto_pair(client, auth_headers):
    body = _preflight(
        client, auth_headers, dict(BASE_SPEC, asset_ids=["CRYPTO:KRAKEN:BTC-USD"])
    )
    assert body["status"] == "valid"
    assert body["errors"] == []


@pytest.mark.parametrize(
    "asset_id",
    [
        "CRYPTO:KRAKEN",           # missing pair
        "CRYPTO:KRAKEN:BTCUSD",    # missing separator
        "CRYPTO:NOWHERE:BTC-USD",  # unknown venue
        "crypto:KRAKEN:BTC-USD",   # lowercase market segment
    ],
)
def test_preflight_rejects_malformed_crypto_ids(client, auth_headers, asset_id):
    body = _preflight(client, auth_headers, dict(BASE_SPEC, asset_ids=[asset_id]))
    assert body["status"] == "invalid"
    assert any(issue["field"] == "asset_ids" for issue in body["errors"])


@pytest.mark.parametrize("path", ["/api/v1/strategy-templates", "/api/v1/strategy-specs/preflight"])
def test_contract_routes_publish_a_typed_schema_and_description(client, path):
    spec = client.get("/openapi.json").json()
    operation = spec["paths"][path]["get" if path.endswith("templates") else "post"]
    assert operation["summary"]
    assert len(operation["description"] or "") > 40
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in schema, f"{path} still advertises an untyped response"
