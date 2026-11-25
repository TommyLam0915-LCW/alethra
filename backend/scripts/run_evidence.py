#!/usr/bin/env python3
"""Evidence ledger for one lane run dir, built from that run's own artifacts.

Why this exists: the 2026-11-17 full-suite run reported `3479 collected` and
`3298 passed + 183 skipped + 1 failed = 3482`, and the lane's log could not say
where the three extra outcome records came from. Parsing a `-q` log cannot: the
`SKIPPED [n] file:line:` prefix is the **raise site**, not the node, and four
different raise sites appear while only three records are extra. The JUnit
artifact does say, because pytest writes a collection-level skip as
`<testcase classname="" name="tests.test_x"><skipped message="collection skipped">`
while an item-level skip carries a real classname.

So every number in this ledger is derived from `junit.xml` plus `collect.txt`
(normalised node ids) and the three are reconciled against each other. Anything
that cannot be matched to an exact node id is printed under `UNRESOLVED` and
stays unresolved -- it is never closed by arithmetic on totals.

Commands:
  ledger  <run dir>                     print/write the reconciliation
  check   <run dir> [--manifest M] [--budget B] [--explicit-selection]
                                        enforce: no vanished node, no new skip
                                        category, no unacknowledged manifest diff
  pin     <run dir> --manifest-out M --budget-out B [--ack-diff SHA]
                                        rewrite the pinned selection and ceiling,
                                        which requires acknowledging the diff

Exit: 0 ok, 1 gate violation (unreconciled records, vanished nodes, over
budget, unacknowledged diff), 2 refused (missing or malformed artifacts).
"""
import argparse
import ast
import collections
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skip_budget import CATEGORIES, Refused, census, classify  # noqa: E402  one classifier

ARTIFACTS = ("identity.txt", "pytest.log", "junit.xml", "collect.txt", "exit-code.txt")
SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected|xfailed|xpassed)")


class Unreadable(Exception):
    """A named artifact is missing or malformed -- a refusal, never a pass."""


def read(run_dir, name):
    path = os.path.join(run_dir, name)
    if not os.path.exists(path):
        raise Unreadable(
            f"{path} is missing. This tool only reconciles a run that saved its "
            "own artifacts; it will not reconstruct them from memory."
        )
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def split_node(rest):
    """`::`-split a node id's non-path part, treating `[...]` as one token.

    A parametrised id may itself contain the separator: `test_loopback_hosts[::1]`
    is the IPv6 loopback address, not a class boundary. Splitting it naively
    produces the name `1]` under a class path ending in `[`, which matches no
    JUnit record, and the ledger then reports one collected node with no outcome
    *and* one outcome with no node for a test that passed cleanly.
    """
    parts, buf, depth, i = [], [], 0, 0
    while i < len(rest):
        char = rest[i]
        if char == "[":
            depth += 1
        elif char == "]" and depth:
            depth -= 1
        if depth == 0 and rest[i:i + 2] == "::":
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(char)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p]


def junit_key(node_id):
    """The (classname, name) pair pytest writes for this exact node id."""
    module, _, rest = node_id.partition("::")
    parts = split_node(rest)
    name = parts[-1] if parts else ""
    cls = parts[:-1]
    stem = module[:-3] if module.endswith(".py") else module
    return (".".join([stem.replace("/", ".")] + cls), name)


def key_index(ids):
    index = {}
    for node_id in ids:
        index.setdefault(junit_key(node_id), node_id)
    return index


COLLECTION_SKIP = re.compile(r"""['"]Skipped:\s*(.*)['"]\s*\)\s*$""", re.S)


def collection_reason(text):
    """The sentence a module-level skip used, out of pytest's own repr.

    A collection-level `<testcase>` carries `message="collection skipped"` and
    puts the real sentence in the element text, as the Python repr of the tuple
    (path, lineno, "Skipped: <sentence>"). That repr quotes the inner string
    with `"` whenever the sentence contains an apostrophe -- and every PG-ONLY
    sentence does, because they say "PostgreSQL's" -- so a reader written for
    one quote style finds nothing, hands the classifier the placeholder instead
    of the sentence, and re-labels three PostgreSQL-only skips as `other` while
    the console census still calls them `pg_only`. Decoding the literal is
    exact; the pattern below is the fallback, never a guess.
    """
    stripped = (text or "").strip()
    if stripped.startswith("("):
        try:
            parts = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            parts = None
        if (isinstance(parts, (list, tuple)) and len(parts) == 3
                and isinstance(parts[2], str)):
            sentence = parts[2].strip()
            return (sentence[len("Skipped:"):].strip()
                    if sentence.startswith("Skipped:") else sentence)
    m = COLLECTION_SKIP.search(stripped)
    if m:
        return m.group(1).strip()
    return None


def skip_reason(child):
    """The sentence the node used to excuse itself, from either JUnit shape.

    An item-level skip carries it in `message`; a collection-level skip carries
    `message="collection skipped"` and puts the real sentence in the element
    text. Reading only `message` would classify every module skip as `other`
    and make this census disagree with the one skip_budget.py takes from the
    console -- a silent reclassification is exactly what this tool exists to
    catch.
    """
    message = (child.get("message") or "").strip()
    if message and message != "collection skipped":
        return message
    found = collection_reason(child.text)
    if found:
        return found
    return message


def parse_junit(text, known):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise Unreadable(f"junit.xml is not parseable: {exc}")
    suites = root.findall("testsuite") or ([root] if root.tag == "testsuite" else [])
    if not suites:
        raise Unreadable("junit.xml has no <testsuite> element")
    records = []
    attrs = collections.Counter()
    for suite in suites:
        for key in ("tests", "skipped", "failures", "errors"):
            value = suite.get(key)
            if value is not None:
                attrs[key] += int(value)
        for testcase in suite.findall("testcase"):
            classname = testcase.get("classname", "")
            name = testcase.get("name", "")
            if classname == "":
                kind, ident = "collection", name
            elif (classname, name) in known:
                kind, ident = "item", known[(classname, name)]
            else:
                kind, ident = "unresolved", f"{classname}::{name}"
            child = testcase.find("skipped")
            outcome = "passed"
            reason = ""
            if child is not None:
                outcome = "skipped"
                reason = skip_reason(child)
            elif testcase.find("failure") is not None:
                outcome = "failed"
                reason = (testcase.find("failure").get("message") or "").strip()
            elif testcase.find("error") is not None:
                outcome = "error"
                reason = (testcase.find("error").get("message") or "").strip()
            records.append({"id": ident, "kind": kind, "outcome": outcome,
                            "reason": reason})
    return records, attrs


NODE_ID = re.compile(r"^\S+\.py(?:::.+)+$")


def manifest_ids(text):
    """The node ids exactly as the collector printed them, rootdir-relative.

    Three of these suites live at `backend/` rather than `backend/tests/`
    (`test_provider_isolation.py` and friends), so their ids legitimately have
    no directory. Rewriting them to `tests/...` here would make the ledger
    disagree with pytest about which node ran. A parametrised id may contain
    spaces, which is why the match is anchored on `path.py::` rather than on
    "the line has no blanks".
    """
    return [line.strip() for line in text.splitlines() if NODE_ID.match(line.strip())]


def summary_counts(log_text):
    """The numbers from pytest's own final summary line, if the log has one."""
    tail = [line for line in log_text.splitlines() if SUMMARY_COUNT.search(line)
            and ("passed" in line or "failed" in line or "error" in line or "skipped" in line)]
    if not tail:
        return {}
    counts = {}
    for value, label in SUMMARY_COUNT.findall(tail[-1]):
        counts[label] = counts.get(label, 0) + int(value)
    return {"line": tail[-1].strip("=").strip(), "counts": counts}


def build_ledger(run_dir):
    ids = manifest_ids(read(run_dir, "collect.txt"))
    known = set(ids)
    records, attrs = parse_junit(read(run_dir, "junit.xml"), key_index(ids))
    log_text = read(run_dir, "pytest.log")
    summary = summary_counts(log_text)
    exit_code = read(run_dir, "exit-code.txt").strip()

    by_outcome = collections.Counter()
    item_skips = collections.Counter()
    collection_skips = collections.Counter()
    duplicate_ids = [ident for ident, n in collections.Counter(
        r["id"] for r in records).items() if n > 1]
    for record in records:
        by_outcome[record["outcome"]] += 1
        if record["outcome"] != "skipped":
            continue
        # A collection-level skip excuses itself with the same kind of sentence
        # an item-level one does, and skip_budget.py counts both as units. The
        # ledger keeps them in the same categories so the two censuses are
        # comparable, and keeps the split so units and items never get merged.
        category = classify(record["reason"])
        if record["kind"] == "collection":
            collection_skips[category] += 1
        else:
            item_skips[category] += 1
    skips = collections.Counter(item_skips)
    skips.update(collection_skips)
    category_total = sum(skips[c] for c in CATEGORIES)

    item_records = [r for r in records if r["kind"] == "item"]
    collection_records = [r for r in records if r["kind"] == "collection"]
    unresolved = [r for r in records if r["kind"] == "unresolved"]
    executed_item_ids = {r["id"] for r in item_records}
    never_ran = sorted(known - executed_item_ids)

    collected_items = len(known)
    reported_units = attrs["tests"] if attrs["tests"] else len(records)
    item_side = (by_outcome["passed"] + by_outcome["failed"] +
                 by_outcome["error"] + by_outcome["skipped"] -
                 len(collection_records))

    # The same run, censused a second way: skip_budget.py reads the sentences
    # pytest printed, this ledger reads the sentences pytest recorded. When the
    # two disagree, one of them is mis-binning or re-counting, and the ledger
    # says so instead of picking the convenient number.
    try:
        log_units, log_counts, _ = census(log_text)
        console_census = {"units": log_units,
                          **{c: log_counts[c] for c in CATEGORIES}}
    except Refused as exc:
        console_census = {"error": str(exc)}

    disagreements = []
    if "error" in console_census:
        disagreements.append("the console census could not be parsed: "
                            + console_census["error"])
    else:
        for c in CATEGORIES:
            if console_census[c] != skips[c]:
                disagreements.append(
                    f"{c}: console says {console_census[c]}, the recorded "
                    f"outcomes say {skips[c]}")
        if console_census["units"] != category_total:
            disagreements.append(
                f"units: console says {console_census['units']}, the recorded "
                f"outcomes say {category_total}")

    ledger = {
        "run_dir": run_dir,
        "identity": {line.split(":")[0]: line.split(":", 1)[1].strip()
                     for line in read(run_dir, "identity.txt").splitlines()
                     if ":" in line},
        "exit_code": exit_code,
        "collected_items": collected_items,
        "junit": {"tests": attrs["tests"], "skipped": attrs["skipped"],
                  "failures": attrs["failures"], "errors": attrs["errors"]},
        "pytest_summary_line": summary.get("line"),
        "pytest_summary_counts": summary.get("counts", {}),
        "outcomes": dict(by_outcome),
        "collection_level_skip_modules": sorted(r["id"] for r in collection_records),
        "skip_categories": {c: skips[c] for c in CATEGORIES},
        "skip_category_total": category_total,
        "console_census": console_census,
        "census_disagreements": disagreements,
        "duplicate_collected_lines": sorted(
            i for i, n in collections.Counter(ids).items() if n > 1),
        "item_level_skips": {c: item_skips[c] for c in CATEGORIES},
        "collection_level_skips_by_category": {c: collection_skips[c] for c in CATEGORIES},
        "collection_level_skips": len(collection_records),
        "reconciliation": {
            "reported_units": reported_units,
            "collected_items": collected_items,
            "delta_units_minus_items": reported_units - collected_items,
            "collection_level_records": len(collection_records),
            "items_accounted": item_side,
            "items_vs_records_ok": item_side == collected_items,
            "delta_explained_ok": len(collection_records) ==
                                  reported_units - collected_items,
        },
        "unresolved_records": [{"id": r["id"], "outcome": r["outcome"],
                                "reason": r["reason"][:200]} for r in unresolved],
        "duplicate_records": duplicate_ids,
        "collected_but_no_record": never_ran,
        "records_without_node_id": [r["id"] for r in unresolved],
    }
    ledger["artifact_sha256"] = {
        name: hashlib.sha256(open(os.path.join(run_dir, name), "rb").read()).hexdigest()
        for name in ARTIFACTS if os.path.exists(os.path.join(run_dir, name))
    }
    return ledger


def render(ledger):
    out = []
    recon = ledger["reconciliation"]
    out.append(f"run dir            {ledger['run_dir']}")
    out.append(f"exit code          {ledger['exit_code']}")
    out.append(f"collected items    {ledger['collected_items']} (collect.txt)")
    out.append(f"junit records      tests={ledger['junit']['tests']} "
               f"skipped={ledger['junit']['skipped']} failures={ledger['junit']['failures']} "
               f"errors={ledger['junit']['errors']}")
    out.append(f"pytest summary     {ledger['pytest_summary_line']}")
    out.append(f"outcomes           " + " ".join(
        f"{k}={v}" for k, v in sorted(ledger["outcomes"].items())))
    out.append(f"skip categories    pg_only={ledger['skip_categories']['pg_only']} "
               f"live_stack={ledger['skip_categories']['live_stack']} "
               f"other={ledger['skip_categories']['other']} "
               f"(total {ledger['skip_category_total']} = item "
               f"{sum(ledger['item_level_skips'].values())} + collection "
               f"{sum(ledger['collection_level_skips_by_category'].values())})")
    cc = ledger["console_census"]
    if "error" in cc:
        out.append(f"console census      unparseable: {cc['error']}")
    else:
        out.append(f"console census      units={cc['units']} "
                   + " ".join(f"{c}={cc[c]}" for c in CATEGORIES)
                   + (" -> agrees with the recorded outcomes"
                      if not ledger["census_disagreements"] else
                      " -> DISAGREES: " + "; ".join(ledger["census_disagreements"])))
    out.append(f"collection-level   {ledger['collection_level_skips']} "
               f"module(s): {', '.join(ledger['collection_level_skip_modules']) or '-'}")
    out.append(f"reported units     {recon['reported_units']} = collected "
               f"{recon['collected_items']} + collection-level "
               f"{recon['collection_level_records']} -> "
               f"{'OK' if recon['items_accounted'] and recon['delta_explained_ok'] else 'DOES NOT RECONCILE'}")
    for label, key in (("unresolved records", "unresolved_records"),
                       ("duplicate records", "duplicate_records"),
                       ("collected but no record", "collected_but_no_record"),
                       ("record without id", "records_without_node_id")):
        entries = ledger[key]
        out.append(f"{label:<19} {len(entries)}" +
                   (f"  <- UNRESOLVED: {', '.join(str(e) for e in entries[:5])}" if entries else ""))
    return "\n".join(out)


def cmd_ledger(args):
    ledger = build_ledger(args.run_dir)
    text = render(ledger)
    print(text)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"ledger json: {args.json_out}")
    return 0


def structural_failures(ledger):
    """Why this run's own artifacts do not add up, with no ceiling in sight.

    These are the checks that survive a focused run: a subset has no business
    being judged against the whole suite's ceiling, but its junit records still
    have to account for its collected items, or the numbers coming out of it
    are not a measurement of anything.
    """
    recon = ledger["reconciliation"]
    failures = []
    if ledger["census_disagreements"]:
        failures.extend("the two censuses of the same run disagree, so neither "
                        "number may be quoted as this run's skip count: " + line
                        for line in ledger["census_disagreements"])
    if ledger["duplicate_collected_lines"]:
        failures.append("collect.txt lists a node id more than once: "
                        + ", ".join(ledger["duplicate_collected_lines"][:5]))
    if ledger["unresolved_records"]:
        failures.append(f"{len(ledger['unresolved_records'])} junit record(s) could not "
                        "be mapped to a collected node id")
    if ledger["duplicate_records"]:
        failures.append(f"duplicate node id(s) recorded: "
                        f"{', '.join(ledger['duplicate_records'][:5])} -- a run that "
                        "reports one node twice is not a count of nodes")
    if ledger["collected_but_no_record"]:
        failures.append(f"{len(ledger['collected_but_no_record'])} collected node(s) "
                        "produced no outcome record (whole-file skip or crash)")
    if not recon["items_accounted"]:
        failures.append(f"item records ({recon['items_accounted']}) do not equal "
                        f"collected items ({recon['collected_items']})")
    if not recon["delta_explained_ok"]:
        failures.append("the difference between reported units and collected items is "
                        "not accounted for by collection-level records; the ledger "
                        "does not close and will not be closed by subtraction")
    return failures


def cmd_check(args):
    ledger = build_ledger(args.run_dir)
    print(render(ledger))
    failures = structural_failures(ledger)
    if args.explicit_selection:
        # A subset cannot hold the whole suite's 164 PostgreSQL-only nodes, so
        # its census is reported and not judged -- but its own artifacts still
        # have to add up, which is what the block above checks.
        print("this run selected specific tests, so the whole-suite ceiling does not "
              "apply; the census is reported but not enforced.")
    else:
        collected_now = set(manifest_ids(read(args.run_dir, "collect.txt")))
        if args.manifest:
            if os.path.exists(args.manifest):
                with open(args.manifest, encoding="utf-8") as fh:
                    pinned = set(manifest_ids(fh.read()))
            else:
                pinned = None
                failures.append(
                    f"no pinned selection manifest at {args.manifest}. A whole-suite "
                    "run is only comparable once the node ids it collected are pinned "
                    "by `run_evidence.py <run dir> pin`; totals alone cannot show that "
                    "a node was deleted.")
            if pinned is not None:
                gone = sorted(pinned - collected_now)
                added = sorted(collected_now - pinned)
                if gone:
                    failures.append(f"{len(gone)} pinned node(s) are no longer collected "
                                    f"(removed, renamed or made uncollectable): "
                                    f"{', '.join(gone[:5])}")
                if added:
                    failures.append(f"{len(added)} node(s) are collected but not pinned; the "
                                    "pinned selection is re-written only by `pin --ack-diff`, "
                                    "never by accepting the newest total: "
                                    f"{', '.join(added[:5])}")
        if args.budget and os.path.exists(args.budget):
            with open(args.budget, encoding="utf-8") as fh:
                budget = json.load(fh)
            for c in CATEGORIES:
                if ledger["skip_categories"][c] > budget.get(c, 0):
                    failures.append(f"{c}: measured {ledger['skip_categories'][c]} > "
                                    f"ceiling {budget[c]}")
            if ledger["skip_category_total"] > budget.get("total", 0):
                failures.append(f"total: measured {ledger['skip_category_total']} > "
                                f"ceiling {budget['total']}")
    if failures:
        print("EVIDENCE GATE REFUSES:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("evidence gate: ledger closes against this run's own artifacts")
    return 0


def diff_block(pinned, current):
    gone = sorted(pinned - current)
    added = sorted(current - pinned)
    body = "\n".join(["-" + g for g in gone] + ["+" + a for a in added])
    return body, gone, added


def cmd_pin(args):
    ledger = build_ledger(args.run_dir)
    current = set(manifest_ids(read(args.run_dir, "collect.txt")))
    broken = structural_failures(ledger)
    if broken:
        print("refused: a ceiling is not pinned from a run whose own ledger does not "
              "close:", file=sys.stderr)
        for line in broken:
            print(f"  - {line}", file=sys.stderr)
        return 2
    if ledger["exit_code"] not in ("0", "1"):
        print(f"refused: this run exited {ledger['exit_code']!r}, which is not a "
              "measurement of a suite that ran, so no ceiling and no manifest come "
              "from it.", file=sys.stderr)
        return 2
    pinned = set()
    if os.path.exists(args.manifest_out):
        pinned = set(manifest_ids(open(args.manifest_out, encoding="utf-8").read()))
    body, gone, added = diff_block(pinned, current)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    print(f"selection diff vs pinned: -{len(gone)} +{len(added)}")
    print("acknowledgement digest: " + digest)
    for line in body.splitlines()[:40]:
        print("  " + line)
    if body and args.ack_diff != digest:
        print("refused: re-pinning requires --ack-diff <the digest above>, so the "
              "exact added/removed node ids have to be read before the ceiling moves.",
              file=sys.stderr)
        return 2
    with open(args.manifest_out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(current)) + "\n")
    provenance = {
        "run_dir": args.run_dir,
        "exit_code": ledger["exit_code"],
        "collected_items": ledger["collected_items"],
        "artifacts": ledger["artifact_sha256"],
        "identity": ledger["identity"],
    }
    payload = {
        "collected": ledger["collected_items"],
        "total": ledger["skip_category_total"],
        **{c: ledger["skip_categories"][c] for c in CATEGORIES},
        "provenance": {
            "generated_by": "backend/scripts/run_evidence.py <run dir> pin",
            "note": "ceiling, not a target; regenerated only from a run dir whose "
                    "ledger closes, and only after reading the selection diff",
            # The console census is the one body of text both tools can re-read, so
            # the budget names it even when the pin -- not write-budget -- wrote it.
            "source_log": os.path.join(args.run_dir, "pytest.log"),
            "source_log_sha256": ledger["artifact_sha256"]["pytest.log"],
            **provenance,
        },
    }
    with open(args.budget_out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"pinned {args.manifest_out} ({len(current)} node ids) and {args.budget_out} "
          f"(total={payload['total']} " +
          " ".join(f"{c}={payload[c]}" for c in CATEGORIES) + ")")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    sub = ap.add_subparsers(dest="cmd")
    l = sub.add_parser("ledger")
    l.add_argument("--json-out")
    c = sub.add_parser("check")
    c.add_argument("--manifest")
    c.add_argument("--budget")
    c.add_argument("--explicit-selection", action="store_true")
    p = sub.add_parser("pin")
    p.add_argument("--manifest-out", required=True)
    p.add_argument("--budget-out", required=True)
    p.add_argument("--ack-diff")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "ledger":
            return cmd_ledger(args)
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "pin":
            return cmd_pin(args)
    except (Unreadable, Refused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    ap.error("a subcommand is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
