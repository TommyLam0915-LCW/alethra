#!/usr/bin/env python3
"""Operate the delegated write-batch registry from a shell.

Examples:

    python scripts/operation_batch.py predeploy
    python scripts/operation_batch.py claim --owner-email owner@example.com \\
        --batch-key f209c278-doc-batch --executor agent-a \\
        --caps '{"backtests": 1}'
    python scripts/operation_batch.py status --owner-email owner@example.com \\
        --batch-key f209c278-doc-batch
    python scripts/operation_batch.py complete --owner-email owner@example.com \\
        --batch-key f209c278-doc-batch --executor agent-a --result-file result.json

Exit codes: 0 allowed / completed, 2 refused or deployment blocked, 3 bad input.

A second executor never repeats the writes: ``claim`` on a completed batch
prints the stored result with ``"outcome": "already_completed"``.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models.user import User
from app.services.batch_coordination import (
    BatchConflict,
    abandon_batch,
    batch_status,
    claim_batch,
    complete_batch,
    deployment_precheck,
)


def _fail(message: str, code: int = 3) -> None:
    print(json.dumps({"error": message}, ensure_ascii=False))
    raise SystemExit(code)


def _dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _owner_id(db, email: str):
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        _fail(f"no user with email {email!r}")
    return user.id


def _json_arg(value, label: str):
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        _fail(f"--{label} is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail(f"--{label} must be a JSON object")
    return parsed


def _json_file(path: str, label: str):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except OSError as exc:
        _fail(f"cannot read --{label}: {exc}")
    except json.JSONDecodeError as exc:
        _fail(f"--{label} is not valid JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail(f"--{label} must contain a JSON object")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def owner_args(command):
        command.add_argument("--owner-email", required=True)
        command.add_argument("--batch-key", required=True)

    status = sub.add_parser("status", help="read back one batch")
    owner_args(status)

    claim = sub.add_parser("claim", help="claim the batch for one executor")
    owner_args(claim)
    claim.add_argument("--executor", required=True)
    claim.add_argument("--operations", help="JSON object describing the writes")
    claim.add_argument("--caps", help="JSON object of the batch's caps")
    claim.add_argument("--precheck", help="JSON object read back before acting")

    complete = sub.add_parser("complete", help="record read-back and close")
    owner_args(complete)
    complete.add_argument("--executor", required=True)
    complete.add_argument("--result-file", required=True)

    abandon = sub.add_parser("abandon", help="release a claim that will not finish")
    owner_args(abandon)
    abandon.add_argument("--executor", required=True)
    abandon.add_argument("--reason", required=True)

    sub.add_parser("predeploy", help="refuse deployment while a batch is claimed")

    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        if args.command == "predeploy":
            report = deployment_precheck(db)
            _dump(report)
            return 0 if report["safe_to_deploy"] else 2

        owner_id = _owner_id(db, args.owner_email)

        if args.command == "status":
            payload = batch_status(db, owner_id=owner_id, batch_key=args.batch_key)
            if payload is None:
                _dump({"found": False, "batch_key": args.batch_key})
                return 2
            _dump({"found": True, **payload})
            return 0

        if args.command == "claim":
            payload = claim_batch(
                db,
                owner_id=owner_id,
                batch_key=args.batch_key,
                executor=args.executor,
                intended_operations=_json_arg(args.operations, "operations"),
                caps=_json_arg(args.caps, "caps"),
                precheck=_json_arg(args.precheck, "precheck"),
            )
            _dump(payload)
            return 0

        if args.command == "complete":
            payload = complete_batch(
                db,
                owner_id=owner_id,
                batch_key=args.batch_key,
                executor=args.executor,
                result=_json_file(args.result_file, "result-file"),
            )
            _dump(payload)
            return 0

        if args.command == "abandon":
            payload = abandon_batch(
                db,
                owner_id=owner_id,
                batch_key=args.batch_key,
                executor=args.executor,
                reason=args.reason,
            )
            _dump(payload)
            return 0

        _fail(f"unknown command {args.command}")
    except BatchConflict as exc:
        _dump({"error": exc.code, "message_zh": exc.message_zh, **exc.details})
        return 2
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
