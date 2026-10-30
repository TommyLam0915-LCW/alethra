"""Offline tests for the live-probe environment contract.

These do not touch a network or a database: they pin the refusal rules so a
probe run can never silently inherit a shared dev stack, the accepted candidate,
or a fixed account again.
"""
import pytest

from tests.live_stack_env import (
    PROBE_BAR_DATES,
    ProbeConfigRefusal,
    db_name_from_url,
    parse_target,
    skip_reason,
)

BASE = {
    "ALETHRA_LIVE_PROBES": "1",
    "ALETHRA_PROBE_API_ORIGIN": "http://127.0.0.1:8210",
    "ALETHRA_PROBE_DB_URL": (
        "postgresql+psycopg2://postgres:pw@127.0.0.1:54321/alethra_r532_probe_20261030"
    ),
    "ALETHRA_PROBE_DB_NAME": "alethra_r532_probe_20261030",
}


def env(**overrides):
    values = dict(BASE)
    for key, value in overrides.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    return values


def test_accepts_a_declared_disposable_stack():
    target = parse_target(env())
    assert target.origin == "http://127.0.0.1:8210"
    assert target.db_name == "alethra_r532_probe_20261030"
    assert skip_reason(env()) is None


def test_refuses_without_opt_in():
    with pytest.raises(ProbeConfigRefusal, match="ALETHRA_LIVE_PROBES"):
        parse_target(env(ALETHRA_LIVE_PROBES="0"))


def test_refuses_when_opt_in_has_any_other_value():
    with pytest.raises(ProbeConfigRefusal, match="ALETHRA_LIVE_PROBES"):
        parse_target(env(ALETHRA_LIVE_PROBES="yes"))


def test_refuses_missing_origin_instead_of_defaulting_to_8000():
    with pytest.raises(ProbeConfigRefusal, match="no default"):
        parse_target(env(ALETHRA_PROBE_API_ORIGIN=None))


@pytest.mark.parametrize("origin", [
    "http://10.0.0.5:8210",
    "https://alethra.example.com",
    "http://192.168.1.20:8210",
])
def test_refuses_non_loopback_origin(origin):
    with pytest.raises(ProbeConfigRefusal, match="not loopback"):
        parse_target(env(ALETHRA_PROBE_API_ORIGIN=origin))


def test_refuses_missing_db_url():
    with pytest.raises(ProbeConfigRefusal, match="ALETHRA_PROBE_DB_URL"):
        parse_target(env(ALETHRA_PROBE_DB_URL=None))


def test_refuses_sqlite_db_url():
    with pytest.raises(ProbeConfigRefusal, match="postgresql\\+psycopg2"):
        parse_target(env(ALETHRA_PROBE_DB_URL="sqlite:///./test.db"))


def test_refuses_missing_db_name():
    with pytest.raises(ProbeConfigRefusal, match="ALETHRA_PROBE_DB_NAME"):
        parse_target(env(ALETHRA_PROBE_DB_NAME=None))


def test_refuses_name_that_disagrees_with_url():
    with pytest.raises(ProbeConfigRefusal, match="does not match"):
        parse_target(env(ALETHRA_PROBE_DB_NAME="alethra_r532_other_20261030"))


@pytest.mark.parametrize("name", [
    "alethra_dev",
    "alethra_candidate_20261029",
    "alethra_r532_candidate_20261030",
])
def test_refuses_shared_dev_or_candidate_database(name):
    with pytest.raises(ProbeConfigRefusal, match="shared dev|disposable"):
        parse_target(env(
            ALETHRA_PROBE_DB_NAME=name,
            ALETHRA_PROBE_DB_URL=(
                f"postgresql+psycopg2://postgres:pw@127.0.0.1:54321/{name}"
            ),
        ))


@pytest.mark.parametrize("name", [
    "alethra_probe",
    "alethra_r532_probe",
    "alethra_r532_probe_2026",
    "postgres",
])
def test_refuses_name_not_in_round_shape(name):
    with pytest.raises(ProbeConfigRefusal, match="per-round disposable"):
        parse_target(env(
            ALETHRA_PROBE_DB_NAME=name,
            ALETHRA_PROBE_DB_URL=(
                f"postgresql+psycopg2://postgres:pw@127.0.0.1:54321/{name}"
            ),
        ))


def test_skip_reason_is_the_refusal_text():
    reason = skip_reason(env(ALETHRA_PROBE_DB_NAME=None))
    assert "ALETHRA_PROBE_DB_NAME" in reason


def test_db_name_from_url():
    assert db_name_from_url(
        "postgresql+psycopg2://u:p@127.0.0.1:54321/alethra_r532_x_20261030"
    ) == "alethra_r532_x_20261030"
    assert db_name_from_url("sqlite:///./test.db") is None
    assert db_name_from_url("") is None


def test_bar_fixture_shape_matches_the_contract():
    assert len(PROBE_BAR_DATES) == 20
    assert PROBE_BAR_DATES[0].isoformat() == "2024-01-02"
    assert PROBE_BAR_DATES[-1].isoformat() == "2024-01-30"
    assert all(day.weekday() < 5 for day in PROBE_BAR_DATES)
    assert all(day.isoformat() != "2024-01-15" for day in PROBE_BAR_DATES)
