"""Which skill unit a personal model is selected for is the authorization.

A personal model is not a credential the owner holds in the abstract. It is
switched on for one skill account (one portfolio) at a time, and that switch is
the fact the inference gate reads. Three things follow, and each is tested here
against the real gate and the real selection table:

* one version selected for two skills serves both — and the verdict names only
  the skill it was asked about, so it cannot be quoted back for another one;
* a skill that was never switched to a version cannot reach it, whichever skill
  did the switching. A record-wide alias row (``skill_unit_id`` NULL) is not a
  stand-in for any skill's own choice, because NULL means "no skill", not "all";
* switching a skill to a newer version takes the older one away from it. The
  older row survives as history and stops being an authorization.

The fixture is the engineering personal stack from ``test_successful_inference``
— real daily bars, the real 14-name feature contract, a real artifact the real
loader reads, and a completed row in ``training_jobs``. The agent-strategy
family is not part of any of these paths; that separation is pinned in
``test_model_activation.py``.
"""

import pytest

from app.models.personal_model_selection import PersonalModelSelection
from app.services.model_activation import check_model_version_inference_authorization
from tests.test_successful_inference import (
    _create_skill_unit,
    _select,
    add_trained_version,
    personal_env,
)


def _verdict(env, version, skill_unit_id):
    return check_model_version_inference_authorization(
        env["db"],
        env["user"].id,
        version.id,
        version.model_specification_json,
        skill_unit_id,
    )


@pytest.fixture
def two_skills(personal_env):
    """One owner, one trained version, two of their own skill units."""
    personal_env["skill_b"] = _create_skill_unit(
        personal_env["db"], personal_env["user"].id
    )
    personal_env["db"].commit()
    return personal_env


def test_a_version_selected_for_two_skills_serves_both(two_skills):
    skill_a = two_skills["portfolio"].id
    skill_b = two_skills["skill_b"].id
    for skill_unit_id in (skill_a, skill_b):
        _select(two_skills["db"], two_skills["user"].id, two_skills["version"],
                two_skills["record"], skill_unit_id)
    two_skills["db"].commit()

    for skill_unit_id in (skill_a, skill_b):
        result = _verdict(two_skills, two_skills["version"], skill_unit_id)
        assert result["authorized"] is True, result
        assert result["skill_unit_id"] == str(skill_unit_id)


def test_a_skill_that_never_selected_is_refused(two_skills):
    result = _verdict(two_skills, two_skills["version"], two_skills["skill_b"].id)
    assert result["authorized"] is False
    assert result["reason_code"] == "no_skill_unit_selection"
    assert result["skill_unit_id"] == str(two_skills["skill_b"].id)


def test_a_record_alias_selection_authorizes_no_skill(two_skills):
    """A NULL skill unit is the record's alias history, never "all skills"."""
    db = two_skills["db"]
    db.add(
        PersonalModelSelection(
            owner_id=two_skills["user"].id,
            skill_unit_id=None,
            record_id=two_skills["record"].id,
            version_id=two_skills["version"].id,
            action="select",
            rationale="record-wide alias only",
        )
    )
    db.commit()

    result = _verdict(two_skills, two_skills["version"], two_skills["skill_b"].id)
    assert result["authorized"] is False
    assert result["reason_code"] == "no_skill_unit_selection"


def test_a_superseded_version_stops_being_an_authorization(two_skills):
    """Switching a skill forward is how the owner stops the version behind it."""
    db, record = two_skills["db"], two_skills["record"]
    skill_unit_id = two_skills["portfolio"].id
    older = two_skills["version"]
    newer = add_trained_version(two_skills)

    assert _verdict(two_skills, older, skill_unit_id)["authorized"] is True

    _select(db, two_skills["user"].id, newer, record, skill_unit_id)
    db.commit()

    assert _verdict(two_skills, newer, skill_unit_id)["authorized"] is True

    stale = _verdict(two_skills, older, skill_unit_id)
    assert stale["authorized"] is False
    assert stale["reason_code"] == "superseded_skill_unit_selection"
    assert stale["currently_selected_version_id"] == str(newer.id)


def test_switching_one_skill_leaves_the_other_on_its_own_version(two_skills):
    """Two skills of the same owner never share a switch."""
    db, record = two_skills["db"], two_skills["record"]
    skill_a = two_skills["portfolio"].id
    skill_b = two_skills["skill_b"].id
    older = two_skills["version"]
    newer = add_trained_version(two_skills)

    _select(db, two_skills["user"].id, newer, record, skill_a)
    _select(db, two_skills["user"].id, older, record, skill_b)
    db.commit()

    assert _verdict(two_skills, newer, skill_a)["authorized"] is True
    assert _verdict(two_skills, older, skill_a)["authorized"] is False
    # Skill B keeps the version it was given, and does not inherit A's switch.
    assert _verdict(two_skills, older, skill_b)["authorized"] is True
    assert _verdict(two_skills, newer, skill_b)["authorized"] is False
