#!/usr/bin/env python3
"""Skip census of a lane log, checked against a generated budget.

A skip is self-reported by the very run it excuses, so an unbounded skip count
turns "not executed" into a green line. This script makes the count a checked
quantity instead: `backend/tests/skip_budget.json` holds the per-category
ceiling, and a whole-suite lane run that exceeds it exits non-zero.

Three categories, kept apart on purpose:

  pg_only      the node needs a PostgreSQL server (row locks, psycopg2 type
               adaptation, NUMERIC, MVCC, current_database(), ALETHRA_PG_* unset)
  live_stack   the node needs an explicitly started stack (ALETHRA_LIVE_PROBES)
  other        any other excuse

Merging them would let a new non-PostgreSQL excuse hide inside the 160-odd
entries that already have a legitimate reason, so `other` carries the tightest
ceiling and is the one an auditor reads first.

The budget file is generated, never typed: `--write-budget` derives every number
from a real lane log and records that log's path, sha256 and HEAD so the ceiling
can be traced back to a measurement. Editing the numbers by hand to make a run
pass is the failure mode this exists to catch.

Exit: 0 within budget, 1 over budget or a self-check violation, 2 refused
(missing or malformed budget, whole-suite selection that does not match the
pinned one).
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys

CATEGORIES = ("pg_only", "live_stack", "other")

# SKIPPED [1] tests/x.py:76: PG-ONLY [tests/x.py:42]: <reason...>
# The bracket count is a grouping: pytest folds identical locations into
# SKIPPED [N], so the units contributed by a line are N, not 1.
SKIP_LINE = re.compile(r"^SKIPPED \[(\d+)\] (\S+?): \d+: (.*)$")
MODULE_SKIP_LINE = re.compile(r"^SKIPPED \[(\d+)\] (\S+?): (.*)$")
COLLECTED = re.compile(r"(\d+) tests? collected")
LIVE_STACK = "live-stack probe"
PGISH = re.compile(
    r"postgres|postgresql|alethra_pg|alethra_test_db|psycopg|mvcc|numeric|row lock|row-lock",
    re.I,
)


class Refused(Exception):
    """The check itself could not be performed; never report this as a pass."""


def classify(reason):
    if reason.startswith("PG-ONLY [") or reason.startswith("PG-SETUP ["):
        return "pg_only"
    if LIVE_STACK in reason:
        return "live_stack"
    if PGISH.search(reason):
        return "pg_only"
    return "other"


def census(log_text):
    """Return (units, per-category counts, rows) from a lane log."""
    counts = collections.Counter()
    units = 0
    rows = []
    for line in log_text.splitlines():
        if not line.startswith("SKIPPED ["):
            continue
        m = SKIP_LINE.match(line) or MODULE_SKIP_LINE.match(line)
        if not m:
            raise Refused(f"cannot parse a skip line: {line!r}")
        n = int(m.group(1))
        category = classify(m.group(3))
        counts[category] += n
        units += n
        rows.append((m.group(2), category, n, m.group(3)))
    return units, counts, rows


def collected_from(log_text):
    """The collected count the lane printed, or None when it printed none."""
    for line in log_text.splitlines():
        if line.startswith("selection on this tree:"):
            m = COLLECTED.search(line)
            if m:
                return int(m.group(1))
            return None
    return None


def load_budget(path):
    if not os.path.exists(path):
        raise Refused(
            f"no budget at {path}; generate it with --write-budget from a real lane "
            "log. A missing ceiling is not an unlimited ceiling."
        )
    try:
        with open(path, encoding="utf-8") as fh:
            budget = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(f"budget at {path} is unreadable: {exc}")
    for key in ("collected", "total") + CATEGORIES:
        if not isinstance(budget.get(key), int) or budget[key] < 0:
            raise Refused(f"budget at {path} has no usable integer {key!r}")
    parts = sum(budget[c] for c in CATEGORIES)
    if parts != budget["total"]:
        raise Refused(
            f"budget at {path} does not add up: {' + '.join(CATEGORIES)} = {parts} "
            f"but total = {budget['total']}"
        )
    return budget


def cmd_write_budget(args):
    log_text = open(args.log, encoding="utf-8", errors="replace").read()
    units, counts, rows = census(log_text)
    collected = collected_from(log_text)
    where = args.log
    if collected is None and args.identity:
        # The same fallback cmd_check uses: a caller who asked for `-q` gets no
        # "N tests collected" header, and the run's identity artifact holds the
        # number regardless of verbosity. Without it a quiet whole-suite run --
        # the shape the lane is normally driven in -- could never be re-measured.
        identity_text = open(args.identity, encoding="utf-8", errors="replace").read()
        collected = collected_from(identity_text)
        where = args.identity
    if collected is None:
        raise Refused(
            f"{args.log} carries no 'selection on this tree: N tests collected' line, "
            "so its skip census cannot be tied to a selection."
        )
    if units != sum(counts[c] for c in CATEGORIES):
        raise Refused("internal: census units and category counts disagree")
    head = sha = None
    for line in log_text.splitlines():
        if line.startswith("git: "):
            head = line[5:].strip()
    if os.path.exists(args.log):
        sha = hashlib.sha256(open(args.log, "rb").read()).hexdigest()
    payload = {
        "collected": collected,
        "total": units,
        **{c: counts[c] for c in CATEGORIES},
        "provenance": {
            "generated_by": "backend/scripts/skip_budget.py <lane log> write-budget",
            "source_log": args.log,
            "source_log_sha256": sha,
            "collected_from": where,
            "source_git_line": head,
            "note": "ceiling, not a target; regenerate only from a real lane log",
        },
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {args.out}: collected={collected} total={units} "
          + " ".join(f"{c}={counts[c]}" for c in CATEGORIES))
    return 0


def cmd_check(args):
    log_text = open(args.log, encoding="utf-8", errors="replace").read()
    budget = load_budget(args.budget)
    units, counts, rows = census(log_text)
    collected = collected_from(log_text)
    where = args.log
    if collected is None and args.identity:
        # A caller who asked for `-q` gets no "N tests collected" header in the
        # console transcript -- that is pytest's documented behaviour, not a
        # missing measurement. The lane records the same number in the run's
        # identity artifact regardless of the caller's verbosity, so it is read
        # from there instead of refusing a run that was measured perfectly.
        identity_text = open(args.identity, encoding="utf-8", errors="replace").read()
        collected = collected_from(identity_text)
        where = args.identity

    print(f"skip census of {args.log}: {units} units "
          + " ".join(f"{c}={counts[c]}" for c in CATEGORIES)
          + f" (categories sum to {sum(counts[c] for c in CATEGORIES)})")
    print(f"budget {args.budget}: collected={budget['collected']} total={budget['total']} "
          + " ".join(f"{c}={budget[c]}" for c in CATEGORIES))

    if args.explicit_selection:
        print("this run selected specific tests, so the whole-suite ceiling does not "
              "apply; the census is reported but not enforced.")
        for c in CATEGORIES:
            if counts[c] > budget[c]:
                print(f"  note: {c}={counts[c]} exceeds its whole-suite ceiling "
                      f"{budget[c]} because this is a subset run")
        return 0

    if collected is None:
        raise Refused(
            "neither %s%s printed a collected count, so this run cannot be shown to "
            "be the budgeted selection" % (
                args.log,
                "" if not args.identity else " nor " + args.identity)
        )
    if collected != budget["collected"]:
        raise Refused(
            f"this whole-suite run collected {collected} tests but the budget pins "
            f"{budget['collected']}: the selection changed (files added, removed or "
            "deselected), so the ceiling was not measured against this suite. "
            "Re-measure and regenerate the budget from a real lane log."
        )

    over = [c for c in CATEGORIES if counts[c] > budget[c]]
    if units > budget["total"]:
        over.append("total")
    if over:
        print("OVER BUDGET:", file=sys.stderr)
        for c in over:
            print(f"  {c}: measured {counts[c] if c != 'total' else units} "
                  f"> ceiling {budget[c]}", file=sys.stderr)
        print("  new skips must be justified in the ledger and the budget "
              "regenerated from a real log -- not raised to match.", file=sys.stderr)
        return 1
    print("within budget; headroom " + " ".join(
        f"{c}={budget[c] - counts[c]}" for c in CATEGORIES) +
        f" total={budget['total'] - units}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log", help="lane log to census")
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("write-budget", help="generate the budget from a real lane log")
    w.add_argument("--out", required=True)
    w.add_argument("--identity", help="the run's identity.txt, the fallback source "
                                      "of the collected count when the caller's "
                                      "verbosity hid it from the transcript")
    c = sub.add_parser("check", help="census the log and enforce the budget")
    c.add_argument("--budget", required=True)
    c.add_argument("--identity", help="the run's identity.txt, the fallback source "
                                      "of the collected count when the caller's "
                                      "verbosity hid it from the transcript")
    c.add_argument("--explicit-selection", action="store_true",
                   help="the run named specific tests, so only report")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "write-budget":
            ns = argparse.Namespace(log=args.log, out=args.out, identity=args.identity)
            return cmd_write_budget(ns)
        if args.cmd == "check":
            ns = argparse.Namespace(log=args.log, budget=args.budget,
                                    identity=args.identity,
                                    explicit_selection=args.explicit_selection)
            return cmd_check(ns)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    ap.error("a subcommand is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
