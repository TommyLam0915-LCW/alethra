"""The customer-visible failure vocabulary is a cross-language contract.

``frontend/src/lib/mlFailureMessages.ts`` repeats a stored ``error_details``
verbatim only for strings the backend writes itself, and
``app/services/personal_journey.py`` quotes one of them inside ``text_zh``.
Nothing at runtime connects those lists to the services that produce them, so a
reword in ``robustness.py`` silently turns a labelled reason into an
"unverified" one, and a rename of the refusal keys silently drops the code from
the card. Every node here re-derives one side from the source of the other.

Static analysis only: no DB, no HTTP, no quota.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from app.services.personal_journey import _stored_failure_parts

BACKEND_APP = Path(__file__).resolve().parents[1] / "app"
FRONTEND_GATE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "mlFailureMessages.ts"

# Services that assign or return into ``error_details`` / ``error_details``- shaped
# text. Found by searching the writers, not by guessing which module looks related.
PRODUCER_FILES = [
    "services/inference.py",
    "services/robustness.py",
    "services/research_runs.py",
    "services/job_control.py",
    "services/training.py",
    "ml/trainer.py",
]

requires_frontend_gate = pytest.mark.skipif(
    not FRONTEND_GATE.exists(),
    reason="frontend gate module is not part of this checkout",
)


def _tree(rel: str) -> ast.Module:
    return ast.parse((BACKEND_APP / rel).read_text(encoding="utf-8"))


def _string_constants(tree: ast.Module) -> set:
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def _controlled_messages() -> list:
    """The whitelist as the frontend wrote it, minus the comments around it."""
    source = FRONTEND_GATE.read_text(encoding="utf-8")
    start = source.index("CONTROLLED_JOB_MESSAGES = [")
    body = source[start + len("CONTROLLED_JOB_MESSAGES = ["): source.index("];", start)]
    body = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("//")
    )
    return re.findall(r"'((?:[^'\\]|\\.)*)'", body)


@requires_frontend_gate
def test_every_controlled_message_is_a_literal_a_service_writes():
    """A string quoted to customers verbatim has to be authored by the platform."""
    written = set()
    for rel in PRODUCER_FILES:
        written |= _string_constants(_tree(rel))
    missing = [msg for msg in _controlled_messages() if msg not in written]
    assert not missing, (
        f"{len(missing)} whitelisted message(s) are not written by any producer "
        f"service, so the UI is labelling text nobody authors: {missing[:2]}"
    )


@requires_frontend_gate
def test_the_whitelist_is_not_empty():
    # An empty list would satisfy the parity check above by accident.
    assert len(_controlled_messages()) >= 7, "the frontend whitelist collapsed"


def test_personal_training_refusal_keeps_the_three_fields():
    """``json.dumps({"code", "message_zh", "details"})`` is what both readers parse."""
    tree = _tree("services/personal_training.py")
    shapes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "dumps"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Dict):
            continue
        keys = tuple(
            k.value for k in node.args[0].keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
        shapes.add(keys)
    assert ("code", "message_zh", "details") in shapes, (
        f"no refusal payload is written with the documented keys; saw {sorted(shapes)}"
    )


def test_no_caller_overrides_the_cancellation_reason():
    """``"Job cancelled by user"`` is whitelisted because it is the only text a
    cancel path can store — an API passing ``reason=user_input`` would break
    that promise, so the default is not allowed to be overridden from outside."""
    offenders = []
    for path in sorted(BACKEND_APP.rglob("*.py")):
        rel = path.relative_to(BACKEND_APP).as_posix()
        if rel == "services/job_control.py":
            continue  # the pass-through inside stop_if_cancelled itself
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in {"mark_cancelled", "stop_if_cancelled"}:
                continue
            if any(kw.arg == "reason" for kw in node.keywords):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"cancel callers pass their own reason text: {offenders}"


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The refusal shape personal_training.py writes.
        (
            json.dumps(
                {
                    "code": "PERSONAL_TRAINING_NO_SNAPSHOT",
                    "message_zh": "尚未建立凍結快照，無法訓練。",
                    "details": {"snapshot_id": None, "feature_names": ["ret_1d"]},
                },
                ensure_ascii=False,
            ),
            ("PERSONAL_TRAINING_NO_SNAPSHOT", "尚未建立凍結快照，無法訓練。"),
        ),
        # safe_error_message() output: readable, platform-unauthorised, kept whole.
        ("訓練中止：artifact 無法寫入。", (None, "訓練中止：artifact 無法寫入。")),
        # Nothing recorded.
        (None, (None, None)),
        ("", (None, None)),
        # Anything that is not the documented refusal shape must not be read as one.
        ("not json at all", (None, "not json at all")),
        ('{"code":"X"}', (None, '{"code":"X"}')),
        ('{"message_zh":"y"}', (None, '{"message_zh":"y"}')),
        ('{"code":"","message_zh":"y"}', (None, '{"code":"","message_zh":"y"}')),
        ('{"code":"X","message_zh":""}', (None, '{"code":"X","message_zh":""}')),
        ('{"code":7,"message_zh":"y"}', (None, '{"code":7,"message_zh":"y"}')),
        ('{"code":"X","message_zh":[1]}', (None, '{"code":"X","message_zh":[1]}')),
        ('[{"code":"X","message_zh":"y"}]', (None, '[{"code":"X","message_zh":"y"}]')),
        ('"a json string"', (None, '"a json string"')),
        ('null', (None, 'null')),
    ],
)
def test_stored_failure_parts(raw, expected):
    assert _stored_failure_parts(raw) == expected


def test_stored_failure_parts_never_surfaces_details():
    raw = json.dumps(
        {
            "code": "PERSONAL_TRAINING_SINGLE_CLASS_DATASET",
            "message_zh": "決策只涵蓋一個動作類別。",
            "details": {"user_id": "u-secret", "snapshot_id": "s-1", "classes": ["hold"]},
        },
        ensure_ascii=False,
    )
    code, text = _stored_failure_parts(raw)
    assert code == "PERSONAL_TRAINING_SINGLE_CLASS_DATASET"
    for internal in ("u-secret", "s-1", "snapshot_id", "user_id"):
        assert internal not in text, f"{internal} reached the customer text"
