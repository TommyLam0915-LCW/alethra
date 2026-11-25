"""The switch that authorizes a personal model is made per skill unit, here and
nowhere else, through the endpoints a customer actually uses.

No authorization fact is written straight to the database in this module. Each
skill account is opened by ``POST /api/v1/paper-portfolio``, each model version
is produced by ``POST /api/v1/ml/personal/training-jobs`` plus the worker's own
code path, each switch is a ``POST .../select`` and each prediction a ``POST
.../predict``. What follows is therefore about the wiring, not about a fixture
that was arranged around it:

* a version serves exactly the skill accounts it was selected for — never one
  that was not, and never "all of them" because someone moved the record-wide
  alias;
* a switch made for one skill does not borrow from, revoke or rewrite another
  skill's choice, nor the record's own status line;
* the version a skill is no longer on stops being an authorization for it,
  while remaining the owner's readable history;
* a foreign skill account is refused without a trace, and a request that names
  no skill account is refused before it can mean anything.

Every decision, bar, label, snapshot and artifact below is synthetic fixture
data generated inside the test. This proves the chain is wired; it says nothing
about whether any model is useful, and no real owner consents to or activates
anything here.
"""

import shutil
import uuid

import pytest

from tests.conftest import TestSession
from app.models.billing import UsageRecord
from app.models.personal_model_selection import PersonalModelSelection
from app.models.quota import QuotaCounter, QuotaReservation
from tests.test_personal_model_comparison import _model_for_job, _start_training
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _open_skill_unit,
    _personal_artifacts,
    _seed_bars,
    _second_user_headers,
    _seed_confirmed_decisions,
    _user_id,
)

AS_OF = BARS[80].isoformat()


@pytest.fixture(autouse=True)
def _synthetic_market(setup_db):
    """The deterministic bars the feature pipeline reads at prediction time."""
    db = TestSession()
    try:
        _seed_bars(db)
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture()
def trained(client, auth_headers, tmp_path, monkeypatch):
    """One *frozen* strategy version, consented and snapshotted, trained into two versions.

    ``model_a`` and ``model_b`` come from the same frozen snapshot under the
    same model record and differ in training parameters, so "this skill is on
    the newer one" is a statement about two real models the gate can tell
    apart.

    The freeze is not decoration for this module's own claims — no node here
    asks about version eligibility — it is what makes the lane represent a
    decision the desk can actually book. Since the desk derives a claimed
    inference's own ``strategy_version_id`` when the caller omits one and then
    runs the normal eligibility check on it, an inference trained from an
    unlocked version answers ``version_not_usable`` at ``POST /{id}/decisions``.
    A module whose models are meant to originate ordinary decisions has to name
    a version that was really locked, which is the same order
    ``tests/test_trained_inference_lineage_chain.py`` uses: lock → consent →
    snapshot → train.
    """
    owner_id = _user_id()
    experiment_id, strategy_version_id = _create_experiment(
        client, auth_headers, title="Skill unit authorization"
    )
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{strategy_version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": "測試：凍結後此版本才能作為決策的來源"},
    )
    assert locked.status_code == 200, locked.text
    _seed_confirmed_decisions(owner_id, uuid.UUID(strategy_version_id))
    _grant_consent(client, auth_headers, strategy_version_id)
    snapshot = _build_snapshot(client, auth_headers, strategy_version_id)
    artifacts_dir = _personal_artifacts(tmp_path, monkeypatch)

    job_a = _start_training(
        client, auth_headers, snapshot["snapshot_id"], "logistic_regression"
    )
    model_a = _model_for_job(client, auth_headers, job_a, strategy_version_id)
    job_b = _start_training(
        client, auth_headers, snapshot["snapshot_id"], "baseline"
    )
    model_b = _model_for_job(client, auth_headers, job_b, strategy_version_id)

    return {
        "owner_id": owner_id,
        "headers": auth_headers,
        "record_id": model_a["record_id"],
        "model_a": model_a["model_version_id"],
        "model_b": model_b["model_version_id"],
        "strategy_version_id": strategy_version_id,
        "artifacts": (model_a["artifact_id"], model_b["artifact_id"]),
        "artifacts_dir": str(artifacts_dir),
    }


def _select(client, headers, version_id, skill_unit_id=None, expected=None,
           rationale="自正式 API 選用此版本。"):
    body = {"rationale": rationale, "expected_active_version_id": expected}
    if skill_unit_id is not None:
        body["skill_unit_id"] = str(skill_unit_id)
    return client.post(
        f"/api/v1/ml/personal/models/{version_id}/select",
        headers=headers,
        json=body,
    )


def _predict(client, headers, version_id, skill_unit_id, record_id=None):
    path = (
        f"/api/v1/ml/personal/records/{record_id}/predict"
        if record_id
        else f"/api/v1/ml/personal/models/{version_id}/predict"
    )
    return client.post(
        path,
        headers=headers,
        json={
            "instrument_id": INSTRUMENT,
            "as_of_date": AS_OF,
            "skill_unit_id": str(skill_unit_id) if skill_unit_id else None,
        },
    )


def _reason(response):
    return response.json()["detail"]["reason_code"]


def _skill_rows(record_id, skill_unit_id):
    db = TestSession()
    try:
        return (
            db.query(PersonalModelSelection)
            .filter(
                PersonalModelSelection.record_id == uuid.UUID(str(record_id)),
                PersonalModelSelection.skill_unit_id
                == uuid.UUID(str(skill_unit_id)),
            )
            .all()
        )
    finally:
        db.close()


def test_predict_without_a_skill_unit_is_refused_as_an_invalid_request(trained, client):
    """No default "any skill": the request must say which account it serves.

    An old client gets this outcome whether it omits the field or sends it as
    null, and on either predict route — the request never reaches the gate, so
    it is neither served nor billed.
    """
    for path in (
        f"/api/v1/ml/personal/models/{trained['model_a']}/predict",
        f"/api/v1/ml/personal/records/{trained['record_id']}/predict",
    ):
        for body in (
            {"instrument_id": INSTRUMENT, "as_of_date": AS_OF},
            {"instrument_id": INSTRUMENT, "as_of_date": AS_OF, "skill_unit_id": None},
        ):
            response = client.post(path, headers=trained["headers"], json=body)
            assert response.status_code == 422, response.text
            assert response.json()["detail"][0]["loc"] == ["body", "skill_unit_id"]


def test_a_skill_that_never_selected_cannot_infer(trained, client):
    skill = _open_skill_unit(client, trained["headers"])

    before = _predict(client, trained["headers"], trained["model_a"], skill)
    assert before.status_code == 403, before.text
    assert _reason(before) == "no_skill_unit_selection"
    assert before.json()["detail"]["skill_unit_id"] == skill

    switched = _select(client, trained["headers"], trained["model_a"], skill)
    assert switched.status_code == 200, switched.text

    after = _predict(client, trained["headers"], trained["model_a"], skill)
    assert after.status_code == 200, after.text
    assert after.json()["resolved_version"]["model_version_id"] == trained["model_a"]
    assert after.json()["training_job_id"]


def test_moving_the_record_alias_authorizes_no_skill(trained, client):
    """The alias is a status line, not a credential any skill can spend.

    The switch here names no skill account, so only the record-wide alias
    moves — and the skill account still cannot reach the version it was never
    given, on either predict route.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)

    alias_switch = _select(client, headers, trained["model_a"])
    assert alias_switch.status_code == 200, alias_switch.text
    assert alias_switch.json()["skill_unit_id"] is None
    assert alias_switch.json()["active_version"]["is_active"] is True

    explicit = _predict(client, headers, trained["model_a"], skill)
    assert explicit.status_code == 403, explicit.text
    assert _reason(explicit) == "no_skill_unit_selection"

    alias_route = _predict(
        client, headers, None, skill, record_id=trained["record_id"]
    )
    assert alias_route.status_code == 403, alias_route.text
    assert _reason(alias_route) == "no_skill_unit_selection"


def test_a_foreign_skill_account_is_refused_and_leaves_no_row(trained, client):
    headers = trained["headers"]
    other_headers = _second_user_headers(client)
    foreign_skill = _open_skill_unit(client, other_headers)

    refused = _select(client, headers, trained["model_a"], foreign_skill)
    assert refused.status_code == 404, refused.text
    assert refused.json()["detail"]["code"] == "skill_unit_not_found"

    history = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()["selections"] == []

    listed = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=other_headers,
        params={"skill_unit_id": foreign_skill},
    )
    assert listed.status_code == 404, listed.text


def test_two_skills_of_one_owner_switch_independently(trained, client):
    headers = trained["headers"]
    skill_a = _open_skill_unit(client, headers)
    skill_b = _open_skill_unit(client, headers)

    assert _select(client, headers, trained["model_a"], skill_a).status_code == 200
    assert _select(client, headers, trained["model_a"], skill_b).status_code == 200

    # Only skill A moves to the newer version.
    assert (
        _select(client, headers, trained["model_b"], skill_a,
                expected=trained["model_a"]).status_code
        == 200
    )

    assert _predict(client, headers, trained["model_b"], skill_a).status_code == 200
    stale = _predict(client, headers, trained["model_a"], skill_a)
    assert stale.status_code == 403, stale.text
    assert _reason(stale) == "superseded_skill_unit_selection"
    assert stale.json()["detail"]["context"]["currently_selected_version_id"] == (
        trained["model_b"]
    )

    # Skill B keeps the version it was given and does not inherit A's switch.
    assert _predict(client, headers, trained["model_a"], skill_b).status_code == 200
    borrowed = _predict(client, headers, trained["model_b"], skill_b)
    assert borrowed.status_code == 403, borrowed.text
    # B is refused because *its* selection names A, not because it has no rows.
    assert _reason(borrowed) == "superseded_skill_unit_selection"
    assert borrowed.json()["detail"]["skill_unit_id"] == skill_b
    assert borrowed.json()["detail"]["context"]["currently_selected_version_id"] == (
        trained["model_a"]
    )

    # Neither skill switch rewrote the record's own status line, and an
    # unscoped read names no skill at all.
    history = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
    ).json()
    assert history["active_version_id"] is None
    assert "skill_unit_id" not in history

    for skill, expected in (
        (skill_a, trained["model_b"]),
        (skill_b, trained["model_a"]),
    ):
        scoped = client.get(
            f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
            headers=headers,
            params={"skill_unit_id": skill},
        ).json()
        assert scoped["skill_active_version_id"] == expected


def test_each_skill_gets_its_own_version_from_the_unnamed_predict_route(trained, client):
    """One record, two skills, two versions: the record route answers per skill."""
    headers = trained["headers"]
    skill_a = _open_skill_unit(client, headers)
    skill_b = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_b"], skill_a).status_code == 200
    assert _select(client, headers, trained["model_a"], skill_b).status_code == 200

    for skill, expected in ((skill_a, trained["model_b"]), (skill_b, trained["model_a"])):
        response = _predict(client, headers, None, skill,
                            record_id=trained["record_id"])
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resolved_version"]["model_version_id"] == expected
        assert body["resolved_version"]["resolved_from"] == "skill_unit_selection"
        assert uuid.UUID(body["inference_id"])


def test_selection_history_labels_the_scope_of_every_row(trained, client):
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200
    assert _select(client, headers, trained["model_a"]).status_code == 200

    whole = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
    ).json()
    assert {row["skill_unit_id"] for row in whole["selections"]} == {skill, None}

    scoped = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
        params={"skill_unit_id": skill},
    ).json()
    assert scoped["skill_unit_id"] == skill
    assert scoped["skill_active_version_id"] == trained["model_a"]
    assert {row["skill_unit_id"] for row in scoped["selections"]} == {skill}

    # A skill account that was never switched says so instead of inheriting one.
    other = _open_skill_unit(client, headers)
    untouched = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
        params={"skill_unit_id": other},
    ).json()
    assert untouched["skill_active_version_id"] is None
    assert untouched["selections"] == []


def test_rollback_returns_one_skill_and_leaves_the_other_on_its_version(
    trained, client
):
    headers = trained["headers"]
    skill_a = _open_skill_unit(client, headers)
    skill_b = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill_a).status_code == 200
    assert _select(client, headers, trained["model_a"], skill_b).status_code == 200
    assert (
        _select(client, headers, trained["model_b"], skill_a,
                expected=trained["model_a"]).status_code
        == 200
    )

    rolled = client.post(
        f"/api/v1/ml/personal/records/{trained['record_id']}/rollback",
        headers=headers,
        json={
            "rationale": "此技能回復上一版本。",
            "expected_active_version_id": trained["model_b"],
            "skill_unit_id": skill_a,
        },
    )
    assert rolled.status_code == 200, rolled.text
    body = rolled.json()
    assert body["skill_unit_id"] == skill_a
    assert body["selection"]["action"] == "rollback"
    assert body["selection"]["version_id"] == trained["model_a"]
    assert body["selection"]["previous_version_id"] == trained["model_b"]

    # The switch the owner undid no longer authorizes, and the earlier one does.
    assert _predict(client, headers, trained["model_b"], skill_a).status_code == 403
    assert _predict(client, headers, trained["model_a"], skill_a).status_code == 200

    # Skill B is untouched: one row of history, same version, same access.
    assert len(_skill_rows(trained["record_id"], skill_b)) == 1
    assert _predict(client, headers, trained["model_a"], skill_b).status_code == 200

    # An expectation taken from the wrong scope is refused, not applied.
    stale = client.post(
        f"/api/v1/ml/personal/records/{trained['record_id']}/rollback",
        headers=headers,
        json={
            "rationale": "以為此技能仍在新版本。",
            "expected_active_version_id": trained["model_b"],
            "skill_unit_id": skill_a,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "stale_active_version"
    assert len(_skill_rows(trained["record_id"], skill_a)) == 3


def test_a_superseded_version_stays_readable_evidence(trained, client):
    """Refusing to serve a version is not deleting it."""
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200
    served = _predict(client, headers, trained["model_a"], skill)
    assert served.status_code == 200, served.text
    inference_id = served.json()["inference_id"]
    artifact_before = served.json()["artifact_sha256"]

    assert (
        _select(client, headers, trained["model_b"], skill,
                expected=trained["model_a"]).status_code
        == 200
    )

    refused = _predict(client, headers, trained["model_a"], skill)
    assert refused.status_code == 403, refused.text

    models = client.get("/api/v1/ml/personal/models", headers=headers).json()["models"]
    row = [m for m in models if m["model_version_id"] == trained["model_a"]]
    assert len(row) == 1
    assert row[0]["artifact_sha256"] == artifact_before
    assert row[0]["evaluation"] is not None
    assert row[0]["revoked_at"] is None

    journal = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
        params={"skill_unit_id": skill},
    ).json()["selections"]
    assert [row["version_id"] for row in journal] == [
        trained["model_b"],
        trained["model_a"],
    ]
    assert inference_id


# ---------------------------------------------------------------------------
# What the ledger says about each door: withdrawn candidates, foreign skill
# accounts and the reservation/settlement count on the record route.
# ---------------------------------------------------------------------------


def _code(response):
    return response.json()["detail"]["code"]


def _billing(owner_id, quota_type="ai_usage"):
    """Every number a prediction is accountable for, read back from the tables.

    Three separate counts, because they answer different questions: a refusal
    that never reached ``reserve_quota`` moves none of them, a prediction that
    died after reserving moves only ``released``, and a served one moves
    ``used``, ``settled`` and ``calls`` together. Counting rows by status is
    behaviour; scanning the source for the word "quota" would not be.
    """
    db = TestSession()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == owner_id,
                QuotaCounter.quota_type == quota_type,
                QuotaCounter.period == "lifetime",
            )
            .first()
        )
        statuses = [
            row.status
            for row in db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == owner_id,
                QuotaReservation.quota_type == quota_type,
            )
            .all()
        ]
        return {
            "used": counter.used if counter else 0,
            "reserved": statuses.count("reserved"),
            "settled": statuses.count("settled"),
            "released": statuses.count("released"),
            "calls": db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .count(),
        }
    finally:
        db.close()


def _withdraw(client, headers, trained, version_id):
    """The formal withdrawal entry point for a model version."""
    return client.post(
        f"/api/v1/model-research/{trained['record_id']}"
        f"/versions/{version_id}/revoke?reason=customer_withdrawal",
        headers=headers,
    )


def test_the_record_route_spends_exactly_one_unit_per_served_prediction(
    trained, client
):
    """One served prediction = one settled reservation and one billed call.

    The record route is the door the daily service and the panels use, so its
    accounting is asserted here rather than inferred from the version route.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    untouched_skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200

    before = _billing(trained["owner_id"])
    served = _predict(client, headers, None, skill, record_id=trained["record_id"])
    assert served.status_code == 200, served.text
    after = _billing(trained["owner_id"])
    assert after == {
        **before,
        "used": before["used"] + 1,
        "settled": before["settled"] + 1,
        "calls": before["calls"] + 1,
    }

    # The id the caller is handed is the billed call itself, and that row names
    # the skill account the verdict was answered for.
    db = TestSession()
    try:
        row = (
            db.query(UsageRecord)
            .filter(UsageRecord.user_id == trained["owner_id"])
            .order_by(UsageRecord.created_at.desc())
            .first()
        )
        booked = dict(row.metadata_json or {})
        assert str(row.id) == served.json()["inference_id"]
    finally:
        db.close()
    assert booked["prediction_type"] == "personal_decision"
    assert booked["skill_unit_id"] == skill
    assert booked["record_id"] == trained["record_id"]
    assert booked["model_version_id"] == trained["model_a"]

    # A skill account of the same owner that was never switched is refused
    # before anything is held or spent — but the two doors say it differently,
    # because only the version door goes straight to the gate. The record door
    # resolves first, finds no record-wide alias to fall back on in this
    # fixture, and answers 409 without consulting the gate; with an alias set
    # the same request answers 403 no_skill_unit_selection instead (see
    # test_moving_the_record_alias_authorizes_no_skill).
    refused = _predict(client, headers, trained["model_a"], untouched_skill)
    assert refused.status_code == 403, refused.text
    assert _reason(refused) == "no_skill_unit_selection"

    unpointed = _predict(
        client, headers, None, untouched_skill, record_id=trained["record_id"]
    )
    assert unpointed.status_code == 409, unpointed.text
    assert _code(unpointed) == "no_active_version"
    assert _billing(trained["owner_id"]) == after

    # So is the request that names a version its own skill account has moved
    # off (422 for the request that names no skill account at all is asserted in
    # test_predict_without_a_skill_unit_is_refused_as_an_invalid_request).
    stale = _predict(client, headers, trained["model_b"], skill)
    assert stale.status_code == 403, stale.text
    assert _reason(stale) == "superseded_skill_unit_selection"
    assert _billing(trained["owner_id"]) == after


def test_a_prediction_that_dies_after_reserving_gives_the_unit_back(trained, client):
    """Reserve-without-settle is reachable, and it never stays reserved.

    The artifact is removed from under a version the gate still authorizes:
    the gate compares recorded checksums, so it passes, the unit is reserved,
    and the loader is what fails. The route must release rather than bill.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200
    first = _predict(client, headers, None, skill, record_id=trained["record_id"])
    assert first.status_code == 200, first.text

    shutil.rmtree(f"{trained['artifacts_dir']}/{trained['artifacts'][0]}")

    before = _billing(trained["owner_id"])
    for version_id, record_id in (
        (None, trained["record_id"]),
        (trained["model_a"], None),
    ):
        missing = _predict(client, headers, version_id, skill, record_id=record_id)
        assert missing.status_code == 409, missing.text
        assert _code(missing) == "artifact_missing"
        assert "未執行推論" in missing.json()["detail"]["message_zh"]

    after = _billing(trained["owner_id"])
    assert after == {**before, "released": before["released"] + 2}
    assert after["reserved"] == 0, "a reservation was left dangling"


def test_withdrawing_the_selected_version_refuses_inference_on_both_doors(
    trained, client
):
    """A withdrawn candidate invalidates the selection that names it.

    Withdrawal does not rewrite the journal, so the record route still resolves
    to the revoked version and the caller is refused by the version check
    (409 model_version_revoked) rather than by a missing pointer. Both numbers
    are refusals; only one of them tells the owner the truth about why.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"], skill).status_code == 200
    assert _predict(
        client, headers, None, skill, record_id=trained["record_id"]
    ).status_code == 200

    withdrawn = _withdraw(client, headers, trained, trained["model_a"])
    assert withdrawn.status_code == 200, withdrawn.text

    before = _billing(trained["owner_id"])
    for version_id, record_id in (
        (trained["model_a"], None),
        (None, trained["record_id"]),
    ):
        refused = _predict(client, headers, version_id, skill, record_id=record_id)
        assert refused.status_code == 409, refused.text
        assert _code(refused) == "model_version_revoked"
        assert refused.json()["detail"]["message_zh"]
    assert _billing(trained["owner_id"]) == before

    # The journal is kept as it was, revoked version and all: the withdrawal
    # stops service without erasing what the owner had chosen.
    journal = client.get(
        f"/api/v1/ml/personal/records/{trained['record_id']}/selections",
        headers=headers,
        params={"skill_unit_id": skill},
    ).json()
    assert journal["skill_active_version_id"] == trained["model_a"]

    listed = client.get("/api/v1/ml/personal/models", headers=headers).json()["models"]
    row = [m for m in listed if m["model_version_id"] == trained["model_a"]]
    assert len(row) == 1 and row[0]["revoked_at"] is not None

    # The skill is not stranded: moved onto the surviving version it serves
    # again, and that move is what bills — the refusals above did not.
    moved = _select(client, headers, trained["model_b"], skill,
                    expected=trained["model_a"])
    assert moved.status_code == 200, moved.text
    served = _predict(client, headers, None, skill, record_id=trained["record_id"])
    assert served.status_code == 200, served.text
    assert served.json()["resolved_version"]["model_version_id"] == trained["model_b"]
    assert _billing(trained["owner_id"]) == {
        **before,
        "used": before["used"] + 1,
        "settled": before["settled"] + 1,
        "calls": before["calls"] + 1,
    }


def test_another_owners_skill_account_is_refused_like_one_that_does_not_exist(
    trained, client
):
    """Guessing someone else's skill account tells the caller nothing new.

    The write door answers 404 skill_unit_not_found (see
    test_a_foreign_skill_account_is_refused_and_leaves_no_row); the inference
    door answers the same 403 no_skill_unit_selection for a foreign account, a
    never-selected own account and a random uuid, so it does not report which
    ids exist. Either way nothing is held, spent or journalized.
    """
    headers = trained["headers"]
    other_headers = _second_user_headers(client)
    foreign_skill = _open_skill_unit(client, other_headers)
    own_skill = _open_skill_unit(client, headers)
    assert _select(client, headers, trained["model_a"]).status_code == 200
    assert _select(client, headers, trained["model_a"], own_skill).status_code == 200

    served_before = _billing(trained["owner_id"])
    for guessed in (foreign_skill, str(uuid.uuid4())):
        for version_id, record_id in (
            (trained["model_a"], None),
            (None, trained["record_id"]),
        ):
            refused = _predict(client, headers, version_id, guessed,
                               record_id=record_id)
            assert refused.status_code == 403, refused.text
            assert _reason(refused) == "no_skill_unit_selection"
            assert refused.json()["detail"]["skill_unit_id"] == guessed
            assert _billing(trained["owner_id"]) == served_before

    assert _skill_rows(trained["record_id"], foreign_skill) == []

    # The owner's own account is unaffected by the guesses made against it.
    still = _predict(client, headers, None, own_skill,
                     record_id=trained["record_id"])
    assert still.status_code == 200, still.text
    assert _billing(trained["owner_id"]) == {
        **served_before,
        "used": served_before["used"] + 1,
        "settled": served_before["settled"] + 1,
        "calls": served_before["calls"] + 1,
    }
