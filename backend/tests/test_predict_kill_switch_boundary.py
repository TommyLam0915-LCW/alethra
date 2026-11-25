"""The two general predict entries meet the inference kill switch, before spending.

What this file proves
---------------------
``POST /api/v1/ml/personal/models/{version_id}/predict`` and
``POST /api/v1/ml/personal/records/{record_id}/predict`` are the owner's own
"ask my model" buttons. They had no stop control at all: only the daily-estimate
run route carried ``Depends(check_kill_switch(inference))``. The check now lives
inside ``_run_personal_prediction`` — the function both routes and the estimate
worker call — so a decorator is not the thing standing between a switched-off
system and a spent unit, and a caller that arrives with no ``Request`` passes the
same line.

Four properties get their own node rather than riding on a green suite:

* an active ``inference`` switch, or the ``global`` one it inherits, refuses with
  503 and writes nothing: no model load, no ``usage_records`` row, no
  ``quota_reservations`` row in any status, no movement in the ``ai_usage``
  counter;
* a switch that cannot be read refuses too, by a code that says so — an
  unreadable brake is not a licence to infer;
* the worker-shaped call (db plus ids, no HTTP) is refused at the same line;
* with no switch armed, both entries answer exactly as they did before, naming
  the version each one resolved and why, and the execution switch — a different
  book — still does not stop a pure estimate.

What this file does NOT prove
-----------------------------
The trained version comes from ``personal_env``, which writes the artifact and a
"completed" training job directly; the bars are synthetic. So these nodes are
evidence about the door and the ledger, not about model quality, and nothing here
runs a real worker process or PostgreSQL.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api import personal_models as pm
from app.models.billing import UsageRecord
from app.models.kill_switch import KillSwitchScope
from app.models.quota import QuotaCounter, QuotaReservation
from app.services.kill_switch import activate_kill_switch, deactivate_kill_switch
from tests.conftest import TestSession
from tests.test_successful_inference import (  # noqa: F401  (fixture import)
    AS_OF_DATE,
    TEST_INSTRUMENT,
    _service_predict,
    personal_env,
)

MODELS_ROUTE = "/api/v1/ml/personal/models/{version_id}/predict"
RECORDS_ROUTE = "/api/v1/ml/personal/records/{record_id}/predict"

RESERVATION_STATUSES = ("reserved", "settled", "released")


def _body(env):
    return {
        "instrument_id": TEST_INSTRUMENT,
        "as_of_date": AS_OF_DATE,
        "skill_unit_id": str(env["portfolio"].id),
    }


def _books(owner_id):
    """What the two books say, read on an independent session.

    A reservation row survives its own outcome — settlement and release both
    change ``status``, neither deletes — so the honest claim about a refusal is
    the count in each of the three states, not a row total. A refusal must leave
    nothing pending, nothing settled and nothing released, because it never
    opened a reservation in the first place.
    """
    db = TestSession()
    try:
        counts = dict.fromkeys(RESERVATION_STATUSES, 0)
        for row in (
            db.query(QuotaReservation)
            .filter(QuotaReservation.user_id == owner_id)
            .all()
        ):
            counts[row.status] = counts.get(row.status, 0) + 1
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == owner_id,
                QuotaCounter.quota_type == "ai_usage",
                QuotaCounter.period == "lifetime",
            )
            .first()
        )
        return {
            "usage_rows": db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .count(),
            "reservations": counts,
            "ai_usage_used": counter.used if counter else 0,
        }
    finally:
        db.close()


UNTOUCHED = {
    "usage_rows": 0,
    "reservations": dict.fromkeys(RESERVATION_STATUSES, 0),
    "ai_usage_used": 0,
}
ONE_SERVED = {
    "usage_rows": 1,
    "reservations": {"reserved": 0, "settled": 1, "released": 0},
    "ai_usage_used": 1,
}


@pytest.fixture
def never_loads_the_model(monkeypatch):
    """Record every attempt to load an artifact instead of refusing loudly.

    ``_run_personal_prediction`` wraps ``predict_personal`` in a broad
    ``except Exception``, so a spy that raises would be turned into a 422 and the
    test would pass for the wrong reason. Counting calls keeps the claim
    "no inference happened" checkable.
    """
    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return {"predicted_action": "hold", "action_probabilities": {}, "classes": []}

    monkeypatch.setattr(pm, "predict_personal", _spy)
    return calls


def _arm(scope, env):
    activate_kill_switch(env["db"], scope, "test", "owner pulled it")


def test_the_inference_switch_refuses_both_entries_and_spends_nothing(
    client, personal_env, never_loads_the_model
):
    """503 on both doors, an untouched ledger, and the switch is releasable."""
    env = personal_env
    owner_id = env["user"].id
    assert _books(owner_id) == UNTOUCHED, "the fixture must start with an untouched ledger"

    _arm(KillSwitchScope.inference, env)
    try:
        for url in (
            MODELS_ROUTE.format(version_id=env["version"].id),
            RECORDS_ROUTE.format(record_id=env["record"].id),
        ):
            refused = client.post(url, headers=env["headers"], json=_body(env))
            assert refused.status_code == 503, refused.text
            assert (
                refused.json()["detail"]["code"] == "kill_switch_active"
            ), refused.text
        assert never_loads_the_model == [], "a switched-off system must not load an artifact"
        assert _books(owner_id) == UNTOUCHED, "a refusal must not bill, reserve or settle"
    finally:
        deactivate_kill_switch(env["db"], KillSwitchScope.inference)

    # The brake was the reason, not the build: put it back and the button answers.
    answered = client.post(
        MODELS_ROUTE.format(version_id=env["version"].id),
        headers=env["headers"],
        json=_body(env),
    )
    assert answered.status_code == 200, answered.text
    resolved = answered.json()["resolved_version"]
    assert resolved["model_version_id"] == str(env["version"].id)
    assert resolved["record_id"] == str(env["record"].id)
    assert resolved["resolved_from"] == "explicit_version_id"
    assert _books(owner_id) == ONE_SERVED, "one served prediction is one settled unit"


def test_the_global_switch_covers_both_entries_by_inheritance(
    client, personal_env, never_loads_the_model
):
    """``global`` stops inference without anyone writing a second switch row.

    ``is_kill_switch_active`` answers True for the global row first, so the
    promise that one operator lever empties the system is measured at each door
    rather than assumed from the shared read.
    """
    env = personal_env
    owner_id = env["user"].id
    _arm(KillSwitchScope.global_, env)
    try:
        for url in (
            MODELS_ROUTE.format(version_id=env["version"].id),
            RECORDS_ROUTE.format(record_id=env["record"].id),
        ):
            refused = client.post(url, headers=env["headers"], json=_body(env))
            assert refused.status_code == 503, refused.text
            assert (
                refused.json()["detail"]["code"] == "kill_switch_active"
            ), refused.text
    finally:
        deactivate_kill_switch(env["db"], KillSwitchScope.global_)

    assert never_loads_the_model == []
    assert _books(owner_id) == UNTOUCHED


def test_a_switch_that_cannot_be_read_refuses_rather_than_infers(
    client, personal_env, never_loads_the_model
):
    """Unreadable state is answered as "on", and the code says which one it was.

    The two refusal codes stay distinguishable because an owner deciding whether
    to pull a lever again needs to know if the system was stopped or blind.
    """
    env = personal_env
    with patch.object(
        pm, "is_kill_switch_active", side_effect=RuntimeError("database unreachable")
    ):
        for url in (
            MODELS_ROUTE.format(version_id=env["version"].id),
            RECORDS_ROUTE.format(record_id=env["record"].id),
        ):
            refused = client.post(url, headers=env["headers"], json=_body(env))
            assert refused.status_code == 503, refused.text
            assert (
                refused.json()["detail"]["code"] == "kill_switch_unreadable"
            ), refused.text

    assert never_loads_the_model == []
    assert _books(env["user"].id) == UNTOUCHED


def test_the_worker_door_passes_the_same_line_as_the_button(
    client, personal_env, never_loads_the_model
):
    """A call with no ``Request`` cannot reach a prediction by missing the decorator.

    ``_service_predict`` is the estimate worker's exact shape: db plus ids,
    ``via="service_tick"``. The owner's button goes through HTTP and the route's
    dependencies; both now stop at the same statement, and the worker's refusal
    carries the same code the person is told.
    """
    env = personal_env
    _arm(KillSwitchScope.inference, env)
    try:
        with pytest.raises(HTTPException) as caught:
            _service_predict(env)
    finally:
        deactivate_kill_switch(env["db"], KillSwitchScope.inference)

    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "kill_switch_active"
    assert never_loads_the_model == []
    assert _books(env["user"].id) == UNTOUCHED


def test_the_execution_switch_still_leaves_a_pure_estimate_alone(client, personal_env):
    """A stop order about placing orders is not a stop order about estimating.

    Pinned so the new shared check cannot be widened into the ``execution``
    scope: the record route resolves through the skill account's own selection,
    which is the behaviour the panel depends on.
    """
    env = personal_env
    _arm(KillSwitchScope.execution, env)
    try:
        answered = client.post(
            RECORDS_ROUTE.format(record_id=env["record"].id),
            headers=env["headers"],
            json=_body(env),
        )
    finally:
        deactivate_kill_switch(env["db"], KillSwitchScope.execution)

    assert answered.status_code == 200, answered.text
    resolved = answered.json()["resolved_version"]
    assert resolved["model_version_id"] == str(env["version"].id)
    assert resolved["resolved_from"] == "skill_unit_selection"
    assert _books(env["user"].id) == ONE_SERVED
