"""Walk-forward re-evaluation of the personal decision model.

These tests use synthetic, in-memory rows only. They verify the evaluation
protocol (group-aware expanding windows, one-group gap, per-fold refit) and the
fact that the published chronological hold-out is left in place.
"""

import uuid

import pytest

from app.ml.personal_trainer import (
    train_personal_model,
    walk_forward_folds,
)

FEATURE_NAMES = ["f1", "f2"]
ACTIONS = ["buy", "hold", "sell"]


def _rows(groups: int = 20, per_group: int = 5):
    rows = []
    for group in range(groups):
        instant = f"2026-01-{group + 1:02d}T10:00:00"
        for offset in range(per_group):
            rows.append(
                {
                    "decision_event_id": f"evt-{group:02d}-{offset}",
                    "decision_instant": instant,
                    "decided_at": instant,
                    "action_label": ACTIONS[(group + offset) % len(ACTIONS)],
                    "features": {
                        "f1": float(group) + offset * 0.1,
                        "f2": float(offset % len(ACTIONS)),
                    },
                }
            )
    return rows


@pytest.fixture()
def artifacts(tmp_path, monkeypatch):
    path = tmp_path / "personal-artifacts"
    path.mkdir()
    monkeypatch.setattr(
        "app.ml.personal_trainer.PERSONAL_ARTIFACTS_DIR", str(path)
    )
    return path


def _train(rows, model_type="logistic_regression"):
    return train_personal_model(
        rows,
        FEATURE_NAMES,
        owner_id=str(uuid.uuid4()),
        snapshot_id=str(uuid.uuid4()),
        snapshot_checksum="0" * 64,
        strategy_version_id=str(uuid.uuid4()),
        model_type=model_type,
    )


def test_folds_expand_on_whole_group_boundaries():
    group_sizes = [3, 2, 4, 1, 5, 2, 3, 2, 4, 1]
    folds = walk_forward_folds(group_sizes)

    assert folds, "ten groups must yield at least one fold"
    bounds = []
    cursor = 0
    for size in group_sizes:
        bounds.append(cursor)
        cursor += size

    previous_test_end = -1
    train_end_row = -1
    for fold in folds:
        # Train window always starts at the first group and expands.
        assert fold["train_group_start"] == 0
        assert fold["train_row_start"] == 0
        assert fold["train_row_end"] == (
            bounds[fold["train_group_end"]] + group_sizes[fold["train_group_end"]]
        )
        # At least one whole group sits between train and test.
        assert fold["test_group_start"] > fold["train_group_end"]
        assert fold["test_group_start"] - fold["train_group_end"] - 1 >= 1
        # Test row range is exactly the rows of its groups.
        assert fold["test_row_start"] == bounds[fold["test_group_start"]]
        assert fold["test_row_end"] == (
            bounds[fold["test_group_end"]] + group_sizes[fold["test_group_end"]]
        )
        # Windows expand and test blocks never repeat.
        assert fold["train_row_end"] > train_end_row
        assert fold["test_row_start"] > previous_test_end
        train_end_row = fold["train_row_end"]
        previous_test_end = fold["test_row_end"]


def test_folds_require_enough_groups():
    assert walk_forward_folds([]) == []
    assert walk_forward_folds([10, 10]) == []
    # Two groups train, one gap, nothing left to test.
    assert walk_forward_folds([5, 5, 5]) == []


def test_training_reports_walk_forward_next_to_holdout(artifacts):
    result = _train(_rows())

    assert result["split"]["kind"] == "chronological_holdout"
    assert "test" in result["metrics"]

    walk_forward = result["walk_forward"]
    assert walk_forward["available"] is True
    assert walk_forward["kind"] == "expanding_window"
    assert walk_forward["advisory_only"] is True
    assert walk_forward["not_a_release_gate"] is True
    assert walk_forward["primary_evaluation"] == "chronological_holdout"
    assert walk_forward["n_folds"] >= 1
    assert isinstance(walk_forward["mean_accuracy"], float)
    assert isinstance(walk_forward["baseline_mean_accuracy"], float)

    previous_end = None
    for fold in walk_forward["folds"]:
        assert fold["test_start_decision_instant"] > fold["train_end_decision_instant"]
        assert fold["test_rows"] >= 2
        assert fold["insufficient"] is False
        if previous_end is not None:
            assert fold["test_start_decision_instant"] > previous_end
        previous_end = fold["test_end_decision_instant"]


def test_baseline_model_scores_like_its_own_baseline(artifacts):
    result = _train(_rows(), model_type="baseline")
    walk_forward = result["walk_forward"]

    assert walk_forward["available"] is True
    assert walk_forward["mean_accuracy"] == pytest.approx(
        walk_forward["baseline_mean_accuracy"]
    )


def test_walk_forward_is_deterministic(artifacts):
    rows = _rows()
    first = _train(rows)["walk_forward"]
    second = _train(rows)["walk_forward"]

    assert first["folds"] == second["folds"]
    assert first["mean_accuracy"] == second["mean_accuracy"]
    assert first["mean_macro_f1"] == second["mean_macro_f1"]


def test_walk_forward_below_baseline_is_warned(artifacts):
    # Alternating labels defeat any monotone feature signal, so the fitted
    # model should not beat the most-frequent baseline on unseen folds.
    rows = []
    for group in range(20):
        instant = f"2026-01-{group + 1:02d}T10:00:00"
        for offset in range(5):
            rows.append(
                {
                    "decision_event_id": f"noise-{group:02d}-{offset}",
                    "decision_instant": instant,
                    "decided_at": instant,
                    "action_label": "buy" if (group + offset) % 2 == 0 else "sell",
                    "features": {
                        "f1": float((group * 7 + offset * 3) % 5),
                        "f2": float((group + offset) % 3),
                    },
                }
            )

    result = _train(rows)
    walk_forward = result["walk_forward"]

    assert walk_forward["available"] is True
    if walk_forward["mean_accuracy"] < walk_forward["baseline_mean_accuracy"]:
        assert "walk_forward_below_baseline" in result["warnings"]
