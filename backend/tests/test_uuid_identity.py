"""Gate for the uuid-identity rule that tests/uuid_identity.py encodes.

The SQLite lane stores one uuid column in two spellings, chosen by which layer
wrote the row, and a single-spelling predicate silently returns nothing for the
other. That claim is only worth keeping if it stays executable, so this file
holds both halves: the nodes that pin the divergence, and the nodes that prove
the matcher closes it. If the lane's storage behaviour ever changes, the
divergence nodes fail and say so — the matcher keeps working either way.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests import uuid_identity
from tests.api_test_fixtures import _cleanup_user_data
from tests.conftest import TestSession, _IS_SQLITE

requires_divergent_storage = pytest.mark.skipif(
    not _IS_SQLITE,
    reason=(
        "The two-spelling storage divergence is a SQLite-lane fact; on PostgreSQL a "
        "uuid column casts both spellings to one value, so the divergence nodes below "
        "would assert something the lane does not do."
    ),
)


def _insert_user_orm(db, user_id, email):
    """Write a users row through the ORM — the layer that stores 32-char hex."""
    from app.auth.jwt import get_password_hash
    from app.models.user import User

    db.add(
        User(
            id=user_id,
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name=email.split("@")[0],
            is_admin=False,
            is_active=True,
        )
    )
    db.commit()


def _insert_user_raw(db, user_id, email):
    """Write a users row through raw text() — the layer that keeps the dashes."""
    db.execute(
        text(
            """
            INSERT INTO users (id, email, password_hash, display_name,
                               created_at, updated_at, is_active, is_admin)
            VALUES (:id, :email, 'nope', :display_name, :now, :now, 1, 0)
            """
        ),
        {
            "id": str(user_id),
            "email": email,
            "display_name": email.split("@")[0],
            "now": datetime.now(timezone.utc),
        },
    )
    db.commit()


def _stored_id(db, email):
    """Read the literal bytes the users.id column carries for one email."""
    return db.execute(
        text("SELECT id FROM users WHERE email = :email"), {"email": email}
    ).scalar()


class TestFormsAndClauseWithoutDB:
    """The rule itself, independent of any lane."""

    def test_forms_accepts_uuid_dashed_and_hex(self):
        uid = uuid4()
        expected = (str(uid), uid.hex)

        assert uuid_identity.forms(uid) == expected
        assert uuid_identity.forms(str(uid)) == expected
        assert uuid_identity.forms(uid.hex) == expected

    def test_forms_rejects_a_value_that_is_not_a_uuid(self):
        with pytest.raises(ValueError):
            uuid_identity.forms("not-a-uuid")

    def test_match_returns_clause_and_params_together(self):
        uid = uuid4()
        clause, params = uuid_identity.match("id", uid, name="uid")

        assert clause == "id IN (:uid_d, :uid_h)"
        assert params == {"uid_d": str(uid), "uid_h": uid.hex}

    def test_negate_matches_neither_spelling(self):
        uid = uuid4()
        clause, params = uuid_identity.match("owner_id", uid, name="uid", negate=True)

        assert clause == "owner_id NOT IN (:uid_d, :uid_h)"
        assert params == {"uid_d": str(uid), "uid_h": uid.hex}


class TestStorageDivergence:
    """The measured premise: one column, two spellings, one writer each."""

    @requires_divergent_storage
    def test_orm_and_raw_writers_store_different_forms(self):
        orm_id, raw_id = uuid4(), uuid4()
        db = TestSession()
        try:
            _insert_user_orm(db, orm_id, "orm-form@example.com")
            _insert_user_raw(db, raw_id, "raw-form@example.com")

            orm_stored = _stored_id(db, "orm-form@example.com")
            raw_stored = _stored_id(db, "raw-form@example.com")
        finally:
            db.close()

        assert len(orm_stored) == 32, f"ORM writer stored {orm_stored!r}"
        assert len(raw_stored) == 36, f"raw writer stored {raw_stored!r}"
        assert UUID(orm_stored) == orm_id
        assert UUID(raw_stored) == raw_id

    @requires_divergent_storage
    def test_single_spelling_equality_sees_only_its_own_writer(self):
        """The defect, as an executable case.

        Each spelling finds the row written by the layer that stores that
        spelling and reports the other layer's row as absent. A cleanup or a
        residual count built on one spelling therefore reads as success while
        leaving data behind — which is what this unit fixed.
        """
        orm_id, raw_id = uuid4(), uuid4()
        db = TestSession()
        try:
            _insert_user_orm(db, orm_id, "orm-miss@example.com")
            _insert_user_raw(db, raw_id, "raw-miss@example.com")

            def emails(where, **params):
                return sorted(
                    db.execute(
                        text(f"SELECT email FROM users WHERE {where}"), params
                    ).scalars().all()
                )

            assert emails("id = :v", v=str(orm_id)) == []
            assert emails("id = :v", v=orm_id.hex) == ["orm-miss@example.com"]
            assert emails("id = :v", v=str(raw_id)) == ["raw-miss@example.com"]
            assert emails("id = :v", v=raw_id.hex) == []
        finally:
            db.close()


class TestMatcherClosesTheGap:
    @requires_divergent_storage
    def test_match_finds_the_row_whichever_layer_wrote_it(self):
        """One predicate, four caller×writer combinations, always the same row.

        The caller may hold a UUID object, a dashed string, or a hex string,
        and the row may have been written by either layer. Single-spelling
        equality is right in two of those four combinations and silently wrong
        in the other two; the matcher is right in all four.
        """
        orm_id, raw_id = uuid4(), uuid4()
        db = TestSession()
        try:
            _insert_user_orm(db, orm_id, "orm-found@example.com")
            _insert_user_raw(db, raw_id, "raw-found@example.com")

            cases = [
                (orm_id, ["orm-found@example.com"]),
                (str(orm_id), ["orm-found@example.com"]),
                (orm_id.hex, ["orm-found@example.com"]),
                (raw_id, ["raw-found@example.com"]),
                (str(raw_id), ["raw-found@example.com"]),
                (raw_id.hex, ["raw-found@example.com"]),
            ]
            results = []
            for value, _ in cases:
                clause, params = uuid_identity.match("id", value, name="uid")
                rows = db.execute(
                    text(f"SELECT email FROM users WHERE {clause}"), params
                ).scalars().all()
                results.append(sorted(rows))

            # The exclusion form must keep the complement, or an ownership
            # check would report the other layer's row as someone else's.
            clause, params = uuid_identity.match("id", str(orm_id), name="uid", negate=True)
            not_orm = sorted(
                db.execute(
                    text(f"SELECT email FROM users WHERE {clause}"), params
                ).scalars().all()
            )
        finally:
            db.close()

        assert results == [expected for _, expected in cases]
        assert not_orm == ["raw-found@example.com"]


    def test_cleanup_deletes_an_orm_written_user(self):
        """The previously-leaking case, end to end.

        _cleanup_user_data used to probe users by dashed id only. A row written
        by the ORM failed that probe, the function returned early, and the row
        survived every later test run unnoticed. This node is the after-case
        for that: the same call, and the row is gone under both spellings.
        """
        user_id = uuid4()
        db = TestSession()
        try:
            _insert_user_orm(db, user_id, "cleanup-orm-user@example.com")

            clause, params = uuid_identity.match("id", user_id, name="uid")
            assert (
                db.execute(text(f"SELECT id FROM users WHERE {clause}"), params).fetchone()
                is not None
            ), "precondition: the ORM-written user must exist before cleanup"
        finally:
            db.close()

        _cleanup_user_data(str(user_id))

        verify = TestSession()
        try:
            clause, params = uuid_identity.match("id", user_id, name="uid")
            assert (
                verify.execute(text(f"SELECT COUNT(*) FROM users WHERE {clause}"), params).scalar()
                == 0
            ), f"Cleanup left the ORM-written user {user_id} behind"

            # The single spellings must also report zero, so a matcher that
            # over-matches cannot be what makes this node pass.
            dashed_count = verify.execute(
                text("SELECT COUNT(*) FROM users WHERE id = :dashed"),
                {"dashed": str(user_id)},
            ).scalar()
            hex_count = verify.execute(
                text("SELECT COUNT(*) FROM users WHERE id = :hex"), {"hex": user_id.hex}
            ).scalar()
            assert dashed_count == 0 and hex_count == 0
        finally:
            verify.close()
