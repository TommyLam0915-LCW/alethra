"""The decision journal must belong to the portfolio it was recorded against.

``GET /api/v1/paper-portfolio/{portfolio_id}/decisions`` used to filter by owner
only, so a user with two portfolios saw both journals merged: the count rendered
in the 決策 tab was wrong and one portfolio's rationale/expected-outcome records
were presented as another's. The same owner-only lookup let an outcome
evaluation be posted to a decision through an unrelated portfolio's path.

Attribution is now stored on the decision row. Rows recorded before the link
existed cannot be attributed, so they stay visible rather than disappearing from
the journal - that fallback is pinned here too, because silently dropping
recorded history would be worse than the original leak.
"""

import uuid

from tests.conftest import TestSession

from app.models.paper_portfolio import (
    DecisionRecord,
    PaperDecisionActorType,
    PaperDecisionType,
    OutcomeStatus,
)
from app.models.user import User

ASSET = "DEMO-ALPHA"
EMAIL = "test@alethra.io"


def _make_portfolio(client, headers) -> str:
    resp = client.post(
        "/api/v1/paper-portfolio", headers=headers, json={"initial_virtual_cash": 100000.0}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _record_decision(client, headers, portfolio_id, rationale):
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
        headers=headers,
        json={
            "actor_type": "human",
            "decision_type": "no_action",
            "rationale": rationale,
            "self_reported_confidence": "medium",
        },
    )


def _buy(client, headers, portfolio_id, quantity=1):
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders",
        headers=headers,
        json={
            "asset_id": ASSET,
            "side": "BUY",
            "quantity": quantity,
            "rationale": "journal scope regression",
            "expected_outcome": "the decision belongs to this portfolio only",
            "self_reported_confidence": "medium",
        },
    )


def _journal(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestJournalIsPortfolioScoped:
    def test_a_second_portfolio_does_not_inherit_the_first_ones_journal(self, client, auth_headers):
        first = _make_portfolio(client, auth_headers)
        second = _make_portfolio(client, auth_headers)

        assert _record_decision(client, auth_headers, first, "only in the first").status_code == 201
        assert _buy(client, auth_headers, first).status_code == 201, "the order must actually fill"

        first_journal = _journal(client, auth_headers, first)
        assert len(first_journal) == 2, "one recorded no-action plus one order-generated entry"
        assert {d["decision_type"] for d in first_journal} == {"no_action", "entry"}

        # The regression: this used to return the first portfolio's two records.
        assert _journal(client, auth_headers, second) == []

    def test_override_requests_are_attributed_to_the_portfolio_they_were_filed_under(
        self, client, auth_headers
    ):
        first = _make_portfolio(client, auth_headers)
        second = _make_portfolio(client, auth_headers)

        resp = client.post(
            f"/api/v1/paper-portfolio/{first}/override-requests",
            headers=auth_headers,
            json={
                "original_rationale": "locked strategy",
                "override_reason": "deviation",
                "expected_outcome": "better returns",
                "self_reported_confidence": "medium",
                "falsification_condition": "worse than locked strategy",
            },
        )
        assert resp.status_code == 201, resp.text

        assert [d["id"] for d in _journal(client, auth_headers, first)] == [resp.json()["id"]]
        assert _journal(client, auth_headers, second) == []


class TestUnattributableHistoryStaysVisible:
    def test_a_decision_recorded_before_attribution_is_still_listed(self, client, auth_headers):
        portfolio = _make_portfolio(client, auth_headers)

        db = TestSession()
        try:
            owner = db.query(User).filter(User.email == EMAIL).first()
            assert owner is not None, "the shared test account must exist"
            legacy = DecisionRecord(
                id=uuid.uuid4(),
                owner_id=owner.id,
                paper_portfolio_id=None,
                actor_type=PaperDecisionActorType.human,
                decision_type=PaperDecisionType.no_action,
                rationale="recorded before the portfolio link existed",
                outcome_status=OutcomeStatus.pending,
            )
            db.add(legacy)
            db.commit()
            legacy_id = str(legacy.id)
        finally:
            db.close()

        listed = _journal(client, auth_headers, portfolio)
        assert legacy_id in [d["id"] for d in listed], "history must not vanish after the migration"

    def test_a_decision_from_another_portfolio_cannot_be_evaluated_through_this_one(
        self, client, auth_headers
    ):
        portfolio = _make_portfolio(client, auth_headers)
        other = _make_portfolio(client, auth_headers)

        decision_id = _record_decision(client, auth_headers, portfolio, "attributed").json()["id"]

        resp = client.post(
            f"/api/v1/paper-portfolio/{other}/decisions/{decision_id}/outcome",
            headers=auth_headers,
            json={
                "actual_outcome_summary": "evaluated through the wrong portfolio",
                "net_simulated_return_pct": 1.0,
                "evaluation_method": "manual",
            },
        )
        assert resp.status_code == 404, "another portfolio's decision is not reachable here"

        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio}/decisions/{decision_id}/outcome",
            headers=auth_headers,
            json={
                "actual_outcome_summary": "evaluated through its own portfolio",
                "net_simulated_return_pct": 1.0,
                "evaluation_method": "manual",
            },
        )
        assert resp.status_code == 201, resp.text
