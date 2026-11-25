"""#195: the customer can read back and export every decision, order or no order.

What this file proves
---------------------
``GET /api/v1/paper-portfolio/{id}/journal`` is built from the account's orders
(``app/api/paper_portfolio.py`` ``_journal_rows``), so a decision recorded without
an order cannot appear in it — and it must not, or the file would start claiming
trades that never happened. This file exercises the other half of the contract:
``GET /{id}/decision-history``, over exactly the rows
``GET /{id}/decisions`` returns, plus the inference source each decision names.

The chain is the formal one each time: ``POST /ml/personal/models/{v}/predict``
writes a durable usage row and returns its id → ``POST /{id}/decisions`` records
a decision naming that id → the decision reads back through the decisions query
→ both exports render it. Orders come from ``POST /{id}/orders``.

The boundary is part of the contract, not a footnote. ``POST /api/v1/human-decisions``
is the other door that records a decision, into ``human_decision_events``, a table
no decision surface of a skill account reads. The last node writes one event and
one correction there, proves they are durable and readable through their own door,
and shows the customer's file still answers for only the ``decision_records`` row —
then names the columns that leave an event unattributable to an account and
unlinkable to an estimate, which is why wiring that door into this export is a
product change rather than a query.

What this file does NOT prove
-----------------------------
Eight of the nine nodes sit on the ``personal_env`` fixture, whose artifact and
"completed" training job are written by test code
(``tests/test_successful_inference.py`` says so about itself); they are fine for
exercising the decision and export routes, but a version made there carries no
consented strategy scope and so cannot be switched. The model-switch node is
therefore on the other lane: ``tests/test_formal_skill_unit_authorization_endpoint.trained``
builds both versions through ``POST /ml/personal/training-jobs`` and the real
worker, under a frozen strategy version granted through
``PUT /ml/personalization/consent``.
Even there the decisions, bars and labels feeding the snapshot are synthetic
fixture data. Nothing in this file ran a browser journey or PostgreSQL, and two
conditions here can only be *produced* from test code, because no customer can
reach them through a product entry point: the retired-source node deletes the
usage row a decision names, and the owner-bound node writes a stranger's billing
row and re-points recorded decisions at it — the create door refuses exactly the
linkage that node hands the reader. Both test what the export does with a state
that exists in the data, not a state the API lets anyone create.
"""

import csv
import io
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import inspect as sqlalchemy_inspect

from app.api.paper_portfolio import (
    DECISION_HISTORY_COLUMNS,
    JOURNAL_COLUMNS,
)
from app.models.billing import UsageRecord
from app.models.human_decision import HumanDecisionEvent
from app.models.market_data import MarketDataMetadata
from app.models.paper_portfolio import (
    DecisionRecord,
    PaperDecisionActorType,
    PaperDecisionType,
    OutcomeStatus,
)
from app.models.user import User
from tests.conftest import TEST_ENGINE, TestSession
from tests.test_formal_skill_unit_authorization_endpoint import (  # noqa: F401
    AS_OF,
    trained,
)
from tests.test_human_decision_recording import (  # noqa: F401
    _DEMO_ALPHA_TRADING_DAYS,
    _create_experiment,
    _lock_experiment,
)
from tests.test_personal_strategy_learning import (  # noqa: F401  (fixture import)
    BARS,
    INSTRUMENT,
    _open_skill_unit,
    _seed_bars,
    _select_for_skill,
)
from tests.test_successful_inference import (  # noqa: F401  (fixture import)
    AS_OF_DATE,
    TEST_INSTRUMENT,
    personal_env,
)

BASE = "/api/v1/paper-portfolio"
DEMO_ALPHA = "DEMO-ALPHA"
# ``_seed_bars`` writes under this provider, and the replay-session door is asked
# for it by name, so a series left behind by another module cannot make the
# provider ambiguous here.
DEMO_PROVIDER = "synthetic"


def _register_and_login(client, email, password="testpass123"):
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": password,
            "display_name": email.split("@")[0],
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _predict(client, env, *, as_of=AS_OF_DATE):
    """One model execution through the formal endpoint, returning its id."""
    response = client.post(
        f"/api/v1/ml/personal/models/{env['version'].id}/predict",
        headers=env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": as_of,
            "skill_unit_id": str(env["portfolio"].id),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["inference_id"]


def _formal_predict(client, headers, version_id, skill_unit_id):
    """The same formal predict call, on the lane whose models were really trained."""
    response = client.post(
        f"/api/v1/ml/personal/models/{version_id}/predict",
        headers=headers,
        json={
            "instrument_id": INSTRUMENT,
            "as_of_date": AS_OF,
            "skill_unit_id": str(skill_unit_id),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["inference_id"]


def _decision(
    client, headers, portfolio_id, *, inference_id=None, decision_type="entry",
    rationale=None, simulation_session_id=None,
):
    body = {
        "decision_type": decision_type,
        "actor_type": "human",
        "rationale": rationale or "依據模型估計記下的判斷，不主張報酬。",
    }
    if decision_type in ("entry", "exit"):
        body["expected_outcome"] = "測試記錄用途。"
    if inference_id is not None:
        body["linked_inference_id"] = inference_id
    if simulation_session_id is not None:
        body["simulation_session_id"] = simulation_session_id
    return client.post(
        f"{BASE}/{portfolio_id}/decisions", headers=headers, json=body
    )


def _record_decision(client, env, **kwargs):
    return _decision(client, env["headers"], env["portfolio"].id, **kwargs)


def _history(client, env, *, portfolio_id=None, fmt="json", headers=None):
    return client.get(
        f"{BASE}/{portfolio_id or env['portfolio'].id}/decision-history?format={fmt}",
        headers=headers or env["headers"],
    )


def _journal(client, env, *, fmt="json"):
    return client.get(
        f"{BASE}/{env['portfolio'].id}/journal?format={fmt}", headers=env["headers"]
    )


def _place_order(client, env, *, side="BUY", quantity=1):
    return client.post(
        f"{BASE}/{env['portfolio'].id}/orders",
        headers=env["headers"],
        json={
            "asset_id": "DEMO-ALPHA",
            "side": side,
            "quantity": quantity,
            "order_type": "market",
            "rationale": "下單入口自己記下的決策",
            "expected_outcome": "fill",
            "self_reported_confidence": "medium",
        },
    )


def _rows(body):
    return {row["decision_id"]: row for row in body["records"]}


def _parse_csv(content):
    text = content.decode("utf-8")
    table = list(csv.reader(io.StringIO(text)))
    header = table[0]
    return header, [dict(zip(header, line)) for line in table[1:]], text


def _cell(value):
    """How ``_journal_cell`` renders a value into a CSV field."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@pytest.fixture()
def formal_market(setup_db):
    """The synthetic ``US:AAPL`` series the trained lane reads at predict time.

    Same seeding as ``tests.test_formal_skill_unit_authorization_endpoint``'s
    autouse fixture, built from that module's own helpers rather than a second
    copy of the prices; the metadata row is what marks the series as synthetic.
    """
    db = TestSession()
    try:
        _seed_bars(db)
        db.add(
            MarketDataMetadata(
                instrument_id=INSTRUMENT,
                provider_id="synthetic",
                data_nature="synthetic",
                first_bar_date=BARS[0],
                last_bar_date=BARS[-1],
                total_bars=len(BARS),
            )
        )
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture()
def demo_alpha_series(setup_db):
    """Stored bars for ``DEMO-ALPHA``, which the replay-session door insists upon.

    ``POST /api/v1/simulations/versions/{id}`` refuses a version whose asset has
    no bar in the frozen range, so this is a precondition of the human-decision
    door rather than anything the export needs. Same synthetic series generator
    and the same trading days as ``tests/test_human_decision_recording.py``; the
    provider is named in the session payload so that whatever another module
    leaked into a shared database cannot decide this node's provider.
    """
    db = TestSession()
    try:
        _seed_bars(db, DEMO_ALPHA, end=len(_DEMO_ALPHA_TRADING_DAYS) + 1)
        db.commit()
    finally:
        db.close()
    yield


def _open_replay_session(client, headers):
    """An experiment, its frozen version and a simulation session, through the doors."""
    experiment_id, version_id = _create_experiment(client, headers, "boundary node")
    _lock_experiment(client, headers, experiment_id)
    created = client.post(
        f"/api/v1/simulations/versions/{version_id}",
        headers=headers,
        json={"initial_virtual_cash": 100000.0, "provider_id": DEMO_PROVIDER},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _human_decision(client, headers, session_id, *, key):
    """One decision recorded through the replay door, which is the other store."""
    return client.post(
        "/api/v1/human-decisions",
        headers=headers,
        json={
            "simulation_session_id": session_id,
            "idempotency_key": key,
            "action": "buy",
            "quantity": 100.0,
            "rationale": "在回放 session 裡記下的判斷。",
        },
    )


def test_three_kinds_of_decision_all_reach_the_customer_export(client, personal_env):
    """Order-bearing, order-less-with-source and order-less-without-source."""
    env = personal_env
    inference_id = _predict(client, env)
    sourced = _record_decision(client, env, inference_id=inference_id)
    assert sourced.status_code == 201, sourced.text
    sourced_id = sourced.json()["id"]

    thesis = _record_decision(
        client, env, decision_type="no_action", rationale="今日不動手。"
    )
    assert thesis.status_code == 201, thesis.text
    thesis_id = thesis.json()["id"]

    order = _place_order(client, env)
    assert order.status_code == 201, order.text

    answer = _history(client, env)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["scope"] == "decisions"
    assert body["columns"] == DECISION_HISTORY_COLUMNS
    rows = _rows(body)
    assert body["row_count"] == len(rows) == 3, body["records"]

    with_source = rows[sourced_id]
    assert with_source["record_type"] == "decision"
    assert with_source["linked_inference_id"] == inference_id
    assert with_source["inference_source_state"] == "present"
    assert with_source["inference_model_version_id"] == str(env["version"].id)
    assert with_source["inference_artifact_sha256"] == env["artifact_sha256"]
    assert with_source["inference_as_of_date"] == AS_OF_DATE
    assert with_source["inference_skill_unit_id"] == str(env["portfolio"].id)
    assert with_source["decision_scope"] == "this_portfolio"
    assert with_source["has_order"] is False
    assert with_source["order_count"] == 0
    assert with_source["order_ids"] is None
    assert with_source["fill_count"] == 0

    without_source = rows[thesis_id]
    assert without_source["linked_inference_id"] is None
    assert without_source["inference_source_state"] == "none"
    assert without_source["decision_type"] == "no_action"
    assert without_source["rationale"] == "今日不動手。"

    placed = [row for row in rows.values() if row["has_order"]]
    assert len(placed) == 1, placed
    assert placed[0]["inference_source_state"] == "none"
    assert placed[0]["order_count"] == 1
    assert placed[0]["fill_count"] == 1

    # The two order-less decisions stay out of the trade journal, which is built
    # from orders: one filled order, one fill row, nothing invented.
    journal = _journal(client, env)
    assert journal.status_code == 200, journal.text
    jbody = journal.json()
    assert jbody["columns"] == JOURNAL_COLUMNS
    assert jbody["row_count"] == 1, jbody["records"]
    assert {row["record_type"] for row in jbody["records"]} == {"fill"}
    exported_ids = {row["decision_id"] for row in jbody["records"]}
    assert exported_ids == {placed[0]["decision_id"]}
    assert sourced_id not in exported_ids and thesis_id not in exported_ids


def test_csv_and_json_of_the_decision_file_describe_the_same_rows(client, personal_env):
    """Same rows, same values, one file readable without the application."""
    env = personal_env
    inference_id = _predict(client, env)
    assert _record_decision(client, env, inference_id=inference_id).status_code == 201
    assert _record_decision(
        client, env, decision_type="no_action", rationale="有逗號, 有「引號」\n而且有換行"
    ).status_code == 201
    assert _place_order(client, env).status_code == 201

    json_rows = _rows(_history(client, env).json())
    answer = _history(client, env, fmt="csv")
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/csv")
    assert "decision-history-" in answer.headers["content-disposition"]
    header, csv_rows, text = _parse_csv(answer.content)
    assert header == DECISION_HISTORY_COLUMNS
    assert not text.startswith("\ufeff")
    assert "\r\n" in text
    assert len(csv_rows) == len(json_rows) == 3

    by_id = {row["decision_id"]: row for row in csv_rows}
    for decision_id, expected in json_rows.items():
        got = by_id[decision_id]
        for column in DECISION_HISTORY_COLUMNS:
            assert got[column] == _cell(expected[column]), (
                decision_id,
                column,
                got[column],
                _cell(expected[column]),
            )
    # Non-ASCII free text with an embedded comma and newline survives the round
    # trip through a real CSV parser.
    assert any("有逗號" in row["rationale"] for row in csv_rows)


def test_two_inferences_in_one_skill_keep_their_own_sources(client, personal_env):
    """The newest estimate does not overwrite the decision recorded on the older."""
    env = personal_env
    first = _predict(client, env)
    # The second run is made one day earlier in the data, which is the only way
    # to tell the two resolved sources apart: same version, same skill, same
    # artifact. Without a difference here, "every row resolves the newest usage
    # row" would pass this node.
    earlier = (date.fromisoformat(AS_OF_DATE) - timedelta(days=1)).isoformat()
    second = _predict(client, env, as_of=earlier)
    assert first != second
    one = _record_decision(client, env, inference_id=first, rationale="第一次估計。")
    two = _record_decision(client, env, inference_id=second, rationale="第二次估計。")
    assert one.status_code == 201, one.text
    # Two entry decisions in one account with no source_trade_id: the per-account
    # uniqueness on (paper_portfolio_id, source_trade_id, decision_type) cannot
    # collide, because rows whose source id is NULL do not conflict.
    assert two.status_code == 201, two.text

    rows = _rows(_history(client, env).json())
    assert rows[one.json()["id"]]["linked_inference_id"] == first
    assert rows[one.json()["id"]]["rationale"] == "第一次估計。"
    assert rows[two.json()["id"]]["linked_inference_id"] == second
    assert rows[two.json()["id"]]["rationale"] == "第二次估計。"
    assert {row["inference_source_state"] for row in rows.values()} == {"present"}
    # Each row's source fields come from *its own* usage row, not from the newest
    # inference this skill account happens to have.
    assert rows[one.json()["id"]]["inference_as_of_date"] == AS_OF_DATE
    assert rows[two.json()["id"]]["inference_as_of_date"] == earlier


def test_switching_the_selected_model_leaves_recorded_sources_alone(
    client, formal_market, trained
):
    """Train → select → predict → decide → switch → decide, all through the API.

    This node deliberately runs on the fully formal lane rather than the
    ``personal_env`` fixture used above: ``add_trained_version`` in
    ``tests/test_successful_inference.py`` records no ``strategy_version_id``,
    and ``app/services/personal_selection.py:265-287`` refuses such a version
    with ``strategy_scope_missing``. ``trained`` produces two real artifacts
    through ``POST /ml/personal/training-jobs`` and the worker's own code path
    under a consented strategy version, so every switch here is the same
    ``POST .../select`` gate a customer passes through.
    """
    headers = trained["headers"]
    skill = _open_skill_unit(client, headers)

    _select_for_skill(client, headers, trained["model_a"], skill)
    first_inference = _formal_predict(client, headers, trained["model_a"], skill)
    # The request body names no strategy version — the desk takes it from the
    # origin it was told about, so this is the omitted-version path on the lane
    # where the model was really trained from a really frozen version.
    first = _decision(client, headers, skill, inference_id=first_inference)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    assert (
        first.json()["linked_experiment_version_id"] == trained["strategy_version_id"]
    )

    before = _rows(
        client.get(
            f"{BASE}/{skill}/decision-history?format=json", headers=headers
        ).json()
    )[first_id]
    assert before["inference_model_version_id"] == trained["model_a"]
    assert before["inference_source_state"] == "present"
    assert before["linked_experiment_version_id"] == trained["strategy_version_id"]
    assert (
        before["inference_strategy_version_id"] == trained["strategy_version_id"]
    )

    _select_for_skill(
        client, headers, trained["model_b"], skill, expected=trained["model_a"]
    )
    second_inference = _formal_predict(client, headers, trained["model_b"], skill)
    assert second_inference != first_inference
    second = _decision(
        client, headers, skill, inference_id=second_inference, rationale="第二次估計。"
    )
    assert second.status_code == 201, second.text

    rows = _rows(
        client.get(
            f"{BASE}/{skill}/decision-history?format=json", headers=headers
        ).json()
    )
    older = rows[first_id]
    newer = rows[second.json()["id"]]
    assert older == before, "the switch rewrote a recorded decision's source"
    assert older["linked_inference_id"] == first_inference
    assert older["inference_model_version_id"] == trained["model_a"]
    assert newer["linked_inference_id"] == second_inference
    assert newer["inference_model_version_id"] == trained["model_b"]
    assert newer["inference_artifact_sha256"] != older["inference_artifact_sha256"]
    assert newer["inference_artifact_sha256"]
    assert newer["inference_skill_unit_id"] == older["inference_skill_unit_id"] == skill
    # Both models came off the one frozen strategy version, so the derived half
    # of the lineage does not move when the selected model does — the two
    # versions the row can name are different quantities, and only one of them
    # is a per-skill switch.
    assert newer["linked_experiment_version_id"] == trained["strategy_version_id"]
    assert newer["inference_strategy_version_id"] == trained["strategy_version_id"]

    # Reading it again answers the same thing: each row resolves from stored rows,
    # not from whichever version the skill happens to select now.
    assert _rows(
        client.get(
            f"{BASE}/{skill}/decision-history?format=json", headers=headers
        ).json()
    ) == rows


def test_the_export_answers_for_one_account_and_one_owner(client, personal_env):
    """Another skill account of the same owner, and another owner entirely."""
    env = personal_env
    inference_id = _predict(client, env)
    mine = _record_decision(client, env, inference_id=inference_id)
    assert mine.status_code == 201, mine.text

    other_portfolio = client.post(
        BASE,
        headers=env["headers"],
        json={"initial_virtual_cash": 5000.0, "currency": "USD"},
    )
    assert other_portfolio.status_code == 201, other_portfolio.text
    sibling_id = other_portfolio.json()["id"]

    sibling = _history(client, env, portfolio_id=sibling_id)
    assert sibling.status_code == 200, sibling.text
    assert sibling.json()["records"] == []
    assert sibling.json()["row_count"] == 0
    assert sibling.json()["portfolio_id"] == sibling_id

    stranger_email = f"stranger-{uuid.uuid4().hex[:8]}@test.com"
    stranger = _register_and_login(client, stranger_email)
    refused = _history(client, env, headers=stranger)
    assert refused.status_code == 403, refused.text
    anonymous = client.get(
        f"{BASE}/{env['portfolio'].id}/decision-history?format=csv"
    )
    assert anonymous.status_code in (401, 403)
    bad_format = _history(client, env, fmt="pdf")
    assert bad_format.status_code == 400, bad_format.text


def test_a_retired_source_still_exports_its_id_and_says_what_is_gone(client, personal_env):
    """No foreign key behind the lineage: retention shows up as a state, not a lie.

    ``decision_records.linked_inference_id`` is an application-level reference by
    design (``migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py``
    adds a plain column plus an index), so the billing row it names can be retired
    first. The decision keeps its id — hiding it would rewrite recorded history —
    and the export says the source no longer reads back.
    """
    env = personal_env
    inference_id = _predict(client, env)
    created = _record_decision(client, env, inference_id=inference_id)
    assert created.status_code == 201, created.text
    decision_id = created.json()["id"]

    before = _rows(_history(client, env).json())[decision_id]
    assert before["inference_source_state"] == "present"

    with TestSession() as db:
        usage = db.query(UsageRecord).filter(
            UsageRecord.id == uuid.UUID(inference_id)
        ).first()
        assert usage is not None
        db.delete(usage)
        db.commit()

    after = _rows(_history(client, env).json())[decision_id]
    assert after["linked_inference_id"] == inference_id
    assert after["inference_source_state"] == "missing"
    assert after["inference_model_version_id"] is None
    assert after["inference_artifact_sha256"] is None

    # The decisions query itself is unchanged by the missing source: the recorded
    # decision and its claimed lineage both still read back.
    listed = client.get(
        f"{BASE}/{env['portfolio'].id}/decisions", headers=env["headers"]
    )
    assert listed.status_code == 200, listed.text
    match = [d for d in listed.json() if d["id"] == decision_id]
    assert len(match) == 1
    assert match[0]["linked_inference_id"] == inference_id


def _link_decision_to(db, decision_id, inference_id):
    """Re-point a recorded decision at another usage row, bypassing the write door.

    No entry point can produce this: ``app/services/decision_lineage.py`` refuses
    an inference the owner does not hold. So the only way to test the read door's
    own bound is to write the linkage the read door must survive — which is what a
    backfill or a direct edit would leave behind.
    """
    db.query(DecisionRecord).filter(DecisionRecord.id == decision_id).update(
        {"linked_inference_id": inference_id}, synchronize_session=False
    )
    db.commit()


def _stranger_usage_row(db, owner_id, skill_unit_id):
    """A billing row of the stranger, carrying the metadata the export renders.

    Deliberately indistinguishable, in the four columns this file reads, from a
    row the prediction path wrote: the point is that the reader may not render
    *whose* model and artifact a decision ran on, not that the row is malformed.
    """
    artifact = f"sha256-of-someone-elses-model-{uuid.uuid4().hex[:16]}"
    row = UsageRecord(
        user_id=owner_id,
        endpoint="/api/v1/ml/personal/models/predict",
        tokens_used=1,
        cost=1,
        metadata_json={
            "prediction_type": "personal_decision",
            "model_version_id": str(uuid.uuid4()),
            "artifact_sha256": artifact,
            "as_of_date": AS_OF_DATE,
            "skill_unit_id": str(skill_unit_id),
        },
    )
    db.add(row)
    db.commit()
    return row


def test_a_lineage_id_that_is_not_the_readers_reads_back_as_missing(
    client, personal_env
):
    """The source lookup is bounded by owner, and the same row renders for its owner.

    ``POST /{id}/decisions`` already refuses an inference this owner does not
    hold, so under API writes the two bounds agree. This node tests the read door
    on its own, because a reference can arrive without one: it holds that a
    stranger's model version, artifact digest and skill account never appear in
    this owner's export — and, as the paired control, that the very same usage
    row still reads back as ``present`` for the owner who holds it. Without the
    second half, a blanket refusal would pass too.
    """
    env = personal_env
    mine = _record_decision(client, env, decision_type="no_action", rationale="我的判斷。")
    assert mine.status_code == 201, mine.text
    my_decision_id = uuid.UUID(mine.json()["id"])

    # A second owner, their own skill account and their own recorded decision.
    stranger_email = f"lineage-{uuid.uuid4().hex[:8]}@test.com"
    stranger = _register_and_login(client, stranger_email)
    their_account = client.post(
        BASE,
        headers=stranger,
        json={"initial_virtual_cash": 5000.0, "currency": "USD"},
    )
    assert their_account.status_code == 201, their_account.text
    their_portfolio_id = uuid.UUID(their_account.json()["id"])
    theirs = _decision(
        client, stranger, their_portfolio_id, decision_type="no_action", rationale="他人的判斷。"
    )
    assert theirs.status_code == 201, theirs.text
    their_decision_id = uuid.UUID(theirs.json()["id"])

    with TestSession() as db:
        stranger_id = db.query(User).filter(User.email == stranger_email).first().id
        foreign = _stranger_usage_row(db, stranger_id, their_portfolio_id)
        _link_decision_to(db, my_decision_id, foreign.id)
        _link_decision_to(db, their_decision_id, foreign.id)
        foreign_id, foreign_metadata = str(foreign.id), dict(foreign.metadata_json)

    # Whoever else asks, the reference itself is never hidden...
    asked = _history(client, env)
    as_owner = asked.json()
    my_row = _rows(as_owner)[str(my_decision_id)]
    assert my_row["linked_inference_id"] == foreign_id
    # ...but neither of the rendered source columns nor the raw text leaks across.
    assert my_row["inference_source_state"] == "missing"
    for column in (
        "inference_model_version_id",
        "inference_artifact_sha256",
        "inference_as_of_date",
        "inference_skill_unit_id",
    ):
        assert my_row[column] is None, column
    assert foreign_metadata["artifact_sha256"] not in asked.text
    assert foreign_metadata["model_version_id"] not in asked.text
    assert foreign_id in _history(client, env, fmt="csv").text

    # The owner who holds the row gets the full source back from the same query.
    their_row = _rows(_history(client, env, portfolio_id=their_portfolio_id, headers=stranger).json())[
        str(their_decision_id)
    ]
    assert their_row["inference_source_state"] == "present"
    assert their_row["inference_model_version_id"] == foreign_metadata["model_version_id"]
    assert their_row["inference_artifact_sha256"] == foreign_metadata["artifact_sha256"]
    assert their_row["inference_skill_unit_id"] == str(their_portfolio_id)
    assert their_row["inference_as_of_date"] == foreign_metadata["as_of_date"]

    # And the honest path still renders: this owner's own inference, named at the
    # door that validates it, is unaffected by the bound added for the stranger's.
    readable = _record_decision(client, env, inference_id=_predict(client, env))
    assert readable.status_code == 201, readable.text
    live = _rows(_history(client, env).json())[readable.json()["id"]]
    assert live["inference_source_state"] == "present"
    assert live["inference_artifact_sha256"] == env["artifact_sha256"]


def _add_unattributed_row(owner_id, rationale):
    """Write one decision as it was before the account link existed.

    This is the shape production data still holds (see
    ``tests/test_paper_decision_journal_scope.py``): no current entry point
    produces it, so a fixture is the only way to test that the export renders it
    honestly rather than dropping it or leaking it to another owner.
    """
    with TestSession() as db:
        row = DecisionRecord(
            id=uuid.uuid4(),
            owner_id=owner_id,
            paper_portfolio_id=None,
            actor_type=PaperDecisionActorType.human,
            decision_type=PaperDecisionType.no_action,
            rationale=rationale,
            outcome_status=OutcomeStatus.pending,
        )
        db.add(row)
        db.commit()
        return str(row.id)


def test_unattributed_history_exports_for_its_owner_and_nobody_else(
    client, personal_env
):
    """The export keeps the same inclusion rule as the query, including its limit.

    Rows that predate the account link cannot be attributed, so they stay visible
    on every portfolio of the owner who recorded them — and on nobody else's.
    """
    env = personal_env
    mine = _add_unattributed_row(env["user"].id, "在帳號連結存在之前就記下的判斷。")
    stranger_email = f"legacy-{uuid.uuid4().hex[:8]}@test.com"
    stranger = _register_and_login(client, stranger_email)
    with TestSession() as db:
        stranger_id = db.query(User).filter(User.email == stranger_email).first().id
    theirs = _add_unattributed_row(stranger_id, "另一個人記下的判斷。")

    answer = _history(client, env)
    assert answer.status_code == 200, answer.text
    rows = _rows(answer.json())
    assert rows[mine]["decision_scope"] == "unattributed"
    assert rows[mine]["rationale"] == "在帳號連結存在之前就記下的判斷。"
    assert rows[mine]["has_order"] is False
    assert theirs not in rows

    # The same owner's second account also lists it, exactly as GET /decisions
    # does: the fallback is per owner, not per account.
    second_account = client.post(
        BASE,
        headers=env["headers"],
        json={"initial_virtual_cash": 5000.0, "currency": "USD"},
    )
    assert second_account.status_code == 201, second_account.text
    in_second = _rows(_history(client, env, portfolio_id=second_account.json()["id"]).json())
    assert in_second[mine]["decision_scope"] == "unattributed"
    assert in_second[mine]["portfolio_id"] == second_account.json()["id"]

    # And the stranger's own export names their row, never this owner's.
    stranger_account = client.post(
        BASE, headers=stranger, json={"initial_virtual_cash": 5000.0, "currency": "USD"}
    )
    assert stranger_account.status_code == 201, stranger_account.text
    theirs_rows = _rows(
        _history(client, env, portfolio_id=stranger_account.json()["id"], headers=stranger).json()
    )
    assert theirs_rows[theirs]["decision_scope"] == "unattributed"
    assert mine not in theirs_rows


def test_a_human_decision_event_never_becomes_a_row_in_the_decision_history(
    client, personal_env, demo_alpha_series
):
    """``human_decision_events`` is a second store this export does not read.

    Two doors record a decision and neither writes the other's table. The paper
    door ``POST /{id}/decisions`` inserts ``decision_records``, which is the only
    decision row set this file exports; the replay door
    ``POST /api/v1/human-decisions`` inserts ``human_decision_events``, whose
    lineage the export never sees. The brief asks that this be stated as an
    unwired capability rather than booked as another table's PASS, so the node
    pins it instead of describing it: a human-provenance event really is durably
    written and really reads back through its own door, and the customer's file
    still answers for exactly the paper row — in JSON and in CSV, before and
    after a correction that adds a second event for the same decision.

    The missing lineage here is a fact of the door, not of the table: since
    ``d4e5f6g7h8i9`` the event row has a ``linked_inference_id`` column, and the
    simulation page's ``POST /simulations/{id}/decision`` writes the inference it
    validated into it. ``POST /api/v1/human-decisions`` — the door this node
    presses — accepts no inference identity at all, so an event recorded through
    it has NULL lineage whatever the column can hold. What keeps the event table
    out of this export is that it has no ``paper_portfolio_id``, so it cannot be
    attributed to the account this file is scoped by, and no ``outcome_status``.
    ``simulation_session_id`` is the one column both tables hold; the row here
    renders it from the decision, and nothing joins the two on it.

    What the column readback is worth, stated exactly: ``inspect(engine)`` queries
    the database the export runs against, but on this lane that schema was made by
    ``Base.metadata.create_all`` from the same model classes the route imports. So
    it proves the queried table has, and lacks, these columns on this lane. It is
    not evidence about the migration source, a DDL derivation or what a
    PostgreSQL upgrade left behind; those are separate levels, and this file runs
    none of them.
    """
    env = personal_env
    headers = env["headers"]
    session_id = _open_replay_session(client, headers)

    inference_id = _predict(client, env)
    recorded = _record_decision(
        client, env, inference_id=inference_id, simulation_session_id=session_id
    )
    assert recorded.status_code == 201, recorded.text
    decision_id = recorded.json()["id"]

    event = _human_decision(client, headers, session_id, key="boundary-1")
    assert event.status_code == 201, event.text
    assert event.json()["actor_type"] == "human", "not the channel the door recorded"
    assert event.json()["execution_status"] == "record_only"
    event_id = event.json()["id"]

    body = _history(client, env).json()
    rows = _rows(body)
    assert body["row_count"] == len(rows) == 1, body["records"]
    assert list(rows) == [decision_id]
    assert rows[decision_id]["record_type"] == "decision"
    assert rows[decision_id]["simulation_session_id"] == session_id
    assert rows[decision_id]["linked_inference_id"] == inference_id
    assert rows[decision_id]["inference_source_state"] == "present"

    correction = client.post(
        f"/api/v1/human-decisions/{event_id}/correct",
        headers=headers,
        json={"action": "hold", "correction_reason": "Owner changed their mind."},
    )
    assert correction.status_code == 201, correction.text
    correction_id = correction.json()["id"]

    # Both events are readable through their own door, so the export's silence is
    # a boundary and not a write that went missing. Order is not asserted: the two
    # rows share a created_at column the door does not promise to separate.
    listed = client.get(
        f"/api/v1/human-decisions?simulation_session_id={session_id}", headers=headers
    )
    assert listed.status_code == 200, listed.text
    assert {entry["id"] for entry in listed.json()} == {event_id, correction_id}

    after = _rows(_history(client, env).json())
    assert list(after) == [decision_id], "a human event reached the customer's file"
    assert event_id not in after and correction_id not in after

    csv_text = _history(client, env, fmt="csv").content.decode("utf-8")
    assert decision_id in csv_text
    assert event_id not in csv_text and correction_id not in csv_text

    # Neither decision carries an order, so the trade journal stays empty too:
    # the two human events did not become fills either.
    journal = _journal(client, env)
    assert journal.status_code == 200, journal.text
    assert journal.json()["row_count"] == 0

    with TestSession() as db:
        stored_decisions = (
            db.query(DecisionRecord)
            .filter(DecisionRecord.owner_id == env["user"].id)
            .all()
        )
        stored_events = (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.owner_id == env["user"].id)
            .all()
        )
    assert [str(row.id) for row in stored_decisions] == [decision_id]
    assert {str(row.id) for row in stored_events} == {event_id, correction_id}
    superseded = [
        str(row.supersedes_event_id)
        for row in stored_events
        if row.supersedes_event_id is not None
    ]
    assert superseded == [event_id]

    columns = {
        table: {
            column["name"]
            for column in sqlalchemy_inspect(TEST_ENGINE).get_columns(table)
        }
        for table in ("decision_records", "human_decision_events")
    }
    paper_only = {"paper_portfolio_id", "outcome_status"}
    event_only = {"idempotency_key", "supersedes_event_id", "server_step_number"}
    assert paper_only <= columns["decision_records"]
    assert not paper_only & columns["human_decision_events"]
    assert event_only <= columns["human_decision_events"]
    assert not event_only & columns["decision_records"]
    assert "simulation_session_id" in columns["decision_records"]
    assert "simulation_session_id" in columns["human_decision_events"]
    # Lineage is the one thing both stores now carry, by the same name and with no
    # FK behind it on either side: ``c3d4e5f6g7h8`` gave the paper row its reference
    # and ``d4e5f6g7h8i9`` gave the event row the same one, so the two doors that
    # validate an inference can both record which one they accepted. Naming it here
    # is what keeps the disjointness claims above from being read as "the event
    # table has no lineage column".
    assert "linked_inference_id" in columns["decision_records"]
    assert "linked_inference_id" in columns["human_decision_events"]
    # Both events in this node came through a door that takes no inference, so the
    # column is present and empty — which is a different claim from "unrecordable".
    assert {row.linked_inference_id for row in stored_events} == {None}
