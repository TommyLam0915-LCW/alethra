"""Fixture identities must be scoped by account, not just by fixture name.

Regression: the personal-learning seed derived one fixed user id and reused it
under whatever email it was given, so running the tracked E2E spec against the
candidate database replaced the candidate owner account and broke its login.
"""

from app.services.fixture_identity import fixture_uuid

MARKER = "alethra-personal-learning-e2e"
OWNER = "candidate.owner@example.com"
STAMPED = "personal.learning.e2e.1793323045564@alethra.io"


class TestFixtureIdentity:
    def test_same_account_and_name_are_stable(self):
        assert fixture_uuid(MARKER, OWNER, "user") == fixture_uuid(MARKER, OWNER, "user")

    def test_different_accounts_never_share_rows(self):
        for name in ("user", "experiment", "version", "snapshot"):
            assert fixture_uuid(MARKER, OWNER, name) != fixture_uuid(MARKER, STAMPED, name)

    def test_account_is_normalized(self):
        assert fixture_uuid(MARKER, " Candidate.Owner@Example.COM ", "user") == fixture_uuid(
            MARKER, OWNER, "user"
        )

    def test_fixture_marker_still_namespaces_ids(self):
        assert fixture_uuid("marker-a", OWNER, "user") != fixture_uuid("marker-b", OWNER, "user")

    def test_ids_are_uuid5(self):
        assert fixture_uuid(MARKER, OWNER, "user").version == 5
