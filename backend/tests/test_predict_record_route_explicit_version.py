"""The record door with a version named in the query — the one combination left.

``POST /records/{record_id}/predict`` takes an optional ``version_id``
(``app/api/personal_models.py:800-851``). Every existing selection test presses
either the *version* door (``/models/{version_id}/predict``, which resolves
nothing) or the record door *unnamed* (which resolves through the skill
account's own selection). Nothing pressed the record door while naming a
version, so the two rules the brief pins there were unmeasured on the only route
where both pointers are in play: an explicit version is a candidate, never an
authorization, and the record-wide alias must not stand in for a skill account
that was refused.

What is asserted, with the money read back from ``quota_counters``／
``quota_reservations``／``usage_records`` rather than from the response body:

* a named version the skill is on serves, says ``explicit_version_id``, and hands
  back the id of the usage row that was actually settled;
* a named version the skill moved off is refused, and the answer does not quietly
  swap in the version the skill *is* on. In those nodes the inference engine is a
  tripwire that fails by name if it is reached;
* a named version that lives in another record is a 404 there and a normal 200 on
  its own record, which is what separates the cross-record guard from a broken
  parameter.

Fixture data only: decisions, bars, snapshots, artifacts and models are the
synthetic ones the existing training fixtures generate, and the consent each
strategy uses is granted through the formal consent endpoint by the same test
owner. No real owner consents to anything here.
"""

import uuid

import pytest

import app.api.personal_models as personal_models
from app.models.billing import UsageRecord
from tests.conftest import TestSession
from tests.test_formal_skill_unit_authorization_endpoint import (
    AS_OF,
    _billing,
    _reason,
    _select,
    _synthetic_market,
    trained,
)
from tests.test_personal_model_comparison import _model_for_job, _start_training
from tests.test_personal_strategy_learning import (
    INSTRUMENT,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _open_skill_unit,
    _seed_confirmed_decisions,
)

RECORD_ROUTE = "/api/v1/ml/personal/records/{}/predict"
# The record door's own 404 text, quoted by the node that checks it.
CROSS_RECORD = "Model version not found in this record"


def _predict_named(client, headers, record_id, version_id, skill_unit_id):
    """The combination no existing node reached: record door + named version."""
    return client.post(
        RECORD_ROUTE.format(record_id),
        headers=headers,
        params={"version_id": str(version_id)},
        json={
            "instrument_id": INSTRUMENT,
            "as_of_date": AS_OF,
            "skill_unit_id": str(skill_unit_id),
        },
    )


@pytest.fixture
def engine_must_not_run(monkeypatch):
    """Turn the inference engine into a tripwire for the refusal nodes.

    ``usage_records`` staying still would also follow from a prediction that
    refused to bill; this makes "no inference ran" the assertion itself. A node
    that needs a served prediction asks for nothing, and gets the real loader.
    """
    reached = []

    def tripwire(*args, **kwargs):
        reached.append((args, kwargs))
        raise AssertionError("the inference engine was reached by a refused request")

    monkeypatch.setattr(personal_models, "predict_personal", tripwire)
    return reached


def _settled_inference_ids(owner_id):
    db = TestSession()
    try:
        return [
            str(row.id)
            for row in db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .order_by(UsageRecord.created_at.asc())
            .all()
        ]
    finally:
        db.close()


def test_the_record_route_serves_a_version_the_caller_names(trained, client):
    """Naming the version changes who answered, not whether the caller pays once.

    The record door and the version door are held to the same accounting here:
    the settled unit, the usage row and the returned id must be one and the same
    inference, because the panel quotes that id back as the source of a decision.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200

    before = _billing(trained["owner_id"])
    served = _predict_named(
        client, headers, trained["record_id"], trained["model_a"], skill
    )

    assert served.status_code == 200, served.text
    body = served.json()
    assert body["resolved_version"]["model_version_id"] == trained["model_a"]
    assert body["resolved_version"]["resolved_from"] == "explicit_version_id"
    assert body["skill_unit_id"] == skill
    assert body["as_of"]["as_of_date"] == AS_OF
    assert _billing(trained["owner_id"]) == {
        **before,
        "used": before["used"] + 1,
        "settled": before["settled"] + 1,
        "calls": before["calls"] + 1,
    }
    ids = _settled_inference_ids(trained["owner_id"])
    assert ids[-1] == body["inference_id"], "the id handed back is not the row billed"
    assert len(ids) == len(set(ids)), "two predictions shared one inference id"


def test_naming_a_superseded_version_is_refused_and_not_swapped(
    trained, client, engine_must_not_run
):
    """The refusal answers about the version asked for, not the convenient one.

    ``model_b`` is this skill account's superseded choice and ``model_a`` is the
    one it is on, so a door that "helpfully" resolved to ``model_a`` would still
    be a 200 with a billed row and a different version in the body. Both shapes
    are excluded: the reason names the version that was refused, and the engine
    is never reached.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200
    # Give the record an alias too, so a fallback has something to fall back to.
    assert _select(client, headers, trained["model_a"]).status_code == 200

    before = _billing(trained["owner_id"])
    billed_before = _settled_inference_ids(trained["owner_id"])
    refused = _predict_named(
        client, headers, trained["record_id"], trained["model_b"], skill
    )

    assert refused.status_code == 403, refused.text
    assert _reason(refused) == "superseded_skill_unit_selection"
    detail = refused.json()["detail"]
    assert detail["skill_unit_id"] == skill
    assert detail["context"]["currently_selected_version_id"] == trained["model_a"]
    assert engine_must_not_run == []
    assert _billing(trained["owner_id"]) == before
    assert _settled_inference_ids(trained["owner_id"]) == billed_before
    # That the refused account still gets the version it *is* on is node 1's
    # claim, made with the real engine: the tripwire stays up for this whole
    # node, so no served prediction can be asserted inside it.


def test_the_record_alias_cannot_stand_in_for_a_refused_version(
    trained, client, engine_must_not_run
):
    """An alias pointing at a version authorizes no skill account for it.

    The skill was never switched; the record-wide alias is switched to
    ``model_a``. Naming that version on the record door has to be refused for the
    same reason an unnamed request is — otherwise the alias is a skeleton key
    across the owner's ten skill accounts.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"]).status_code == 200

    before = _billing(trained["owner_id"])
    refused = _predict_named(
        client, headers, trained["record_id"], trained["model_a"], skill
    )

    assert refused.status_code == 403, refused.text
    assert _reason(refused) == "no_skill_unit_selection"
    assert engine_must_not_run == []
    assert _billing(trained["owner_id"]) == before

    unnamed = client.post(
        RECORD_ROUTE.format(trained["record_id"]),
        headers=headers,
        json={
            "instrument_id": INSTRUMENT,
            "as_of_date": AS_OF,
            "skill_unit_id": str(skill),
        },
    )
    assert unnamed.status_code == 403, unnamed.text
    assert _reason(unnamed) == "no_skill_unit_selection"
    assert _billing(trained["owner_id"]) == before


def test_a_version_of_another_record_is_not_found_on_this_route(trained, client):
    """Cross-record reach is closed by the route, not by the gate downstream.

    A second record of the same owner, with its own trained version, is asked
    through the first record's door. The owner owns both, so nothing but the
    pairing refuses it — and the same version served through its own record
    answers 200, which is the control that keeps this from passing on a typo.
    """
    headers = trained["headers"]
    owner_id = trained["owner_id"]
    _, other_strategy = _create_experiment(
        client, headers, title="Another record on the same account"
    )
    _seed_confirmed_decisions(owner_id, uuid.UUID(other_strategy))
    _grant_consent(client, headers, other_strategy)
    snapshot = _build_snapshot(client, headers, other_strategy)
    job = _start_training(
        client, headers, snapshot["snapshot_id"], "logistic_regression"
    )
    other = _model_for_job(client, headers, job, other_strategy)
    assert other["record_id"] != trained["record_id"]

    skill = _open_skill_unit(client, headers)
    assert (
        _select(client, headers, other["model_version_id"], skill).status_code == 200
    )

    before = _billing(owner_id)
    crossed = _predict_named(
        client, headers, trained["record_id"], other["model_version_id"], skill
    )
    assert crossed.status_code == 404, crossed.text
    assert crossed.json()["detail"] == CROSS_RECORD, crossed.text
    assert _billing(owner_id) == before

    own = _predict_named(
        client, headers, other["record_id"], other["model_version_id"], skill
    )
    assert own.status_code == 200, own.text
    assert own.json()["resolved_version"]["model_version_id"] == (
        other["model_version_id"]
    )
    assert own.json()["resolved_version"]["resolved_from"] == "explicit_version_id"
    assert _billing(owner_id) == {
        **before,
        "used": before["used"] + 1,
        "settled": before["settled"] + 1,
        "calls": before["calls"] + 1,
    }

    # A version id that belongs to no record at all answers the same way, so the
    # 404 above is the pairing rule and not a message this route only produces
    # for someone else's data.
    absent = _predict_named(client, headers, other["record_id"], uuid.uuid4(), skill)
    assert absent.status_code == 404, absent.text
    assert absent.json()["detail"] == CROSS_RECORD, absent.text
    assert _billing(owner_id)["calls"] == before["calls"] + 1
