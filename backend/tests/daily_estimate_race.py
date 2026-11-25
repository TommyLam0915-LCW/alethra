"""What counts as proof that the second worker *waited*, and what does not.

A6's standing complaint about the race node it was given is precise, and it is
correct: the node proved that two processes were both standing at
:func:`app.services.daily_estimates.claim_estimate` before either priced
anything, and inferred from those two timestamps that one of them must have
blocked on ``pg_advisory_xact_lock``. A timestamp at the door of a function says
where a process was; it says nothing about what the server made it do. The
worker that "lost" could equally have lost because its ledger read happened
after an unrelated commit, and the node would have read the same way.

So the evidence this module asks for is the server's own account of the wait,
sampled from a third connection while both workers are inside their claims:

* ``pg_locks`` — a row for the losing worker's *backend* pid with
  ``locktype = 'advisory'`` and ``granted = false``, beside a granted row for the
  *winning* worker's backend with the same ``(classid, objid, objsubid)``. That
  pair is the contention, on the object the product actually locks; the object is
  matched between the two rows rather than recomputed from
  :func:`hashtextextended`, because how PostgreSQL packs a 64-bit advisory key
  into those columns is the server's business, not this lane's. Both pids are the
  backends serving the two workers' connections -- a worker's own OS pid is a
  different number that has no row in this table, which is why a claim carries
  ``backend_pid`` beside ``pid`` and only the first one is joined here.
* ``pg_blocking_pids()`` — the winner's backend named as the blocker of the
  loser's.
* ``pg_stat_activity.wait_event_type`` — ``Lock`` for the loser while it waits.
* the billing ledger, read in the same frame as the lock rows — which is what
  turns "it waited" into "it re-read after the bill became durable": the frames
  taken during the wait find nothing, the first frame after the wait finds the
  row, and the loser's own refusal is the third fact.

A *frame* is therefore one instant of the observer's own clock holding both the
lock rows and the ledger count, so no judgement here compares a timestamp
across two different kinds of observation.

:func:`assess_race` is the judgement, and it is deliberately separate from the
capture so that the judgement can be tested on this lane today. Every criterion
is fail-closed: a missing observation is a refusal, never a pass by absence. An
empty ``frames`` list does not mean "no contention happened", it means "this run
did not see", which is the difference between a PostgreSQL finding and a fixture
that silently measured nothing -- the same difference the skip inventory exists
to keep honest. And a criterion this module never reached -- because there was no
key to judge, so no holder, waiter or object was ever named -- is reported under
``notes["not_evaluated"]`` rather than counted among the passes: an absence of
refusals is only an absence of *faults among the questions that were asked*.

Because a timestamp ordering on its own proves nothing about blocking, the
ordering claim is judged separately by :func:`assess_race_start` against
:data:`START_CRITERIA`: exactly two workers, one business key, each arrival
written down before the product function was entered, and both arrivals ahead of
the first price. Passing it says the contest began. It names no lock and lends
no weight to the twelve criteria above, which stay the only route to a
``proven`` race.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

#: One advisory-lock row per backend pid that holds or wants one in *this*
#: database, joined to that session's own wait state. Restricted to the current
#: database because advisory locks are cluster-wide and a shared test server may
#: hold keys belonging to another database.
CAPTURE_SQL = """
SELECT l.pid::bigint AS pid,
       l.locktype AS locktype,
       l.classid::bigint AS classid,
       l.objid::bigint AS objid,
       l.objsubid::bigint AS objsubid,
       l.granted AS granted,
       pg_blocking_pids(l.pid) AS blockers,
       a.wait_event_type AS wait_event_type,
       a.wait_event AS wait_event
FROM pg_locks l
LEFT JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory'
  AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY l.pid, l.classid, l.objid
"""

#: The criteria, in the order a reader has to check them. ``id`` names the
#: judgement and ``says`` is what a refusal on it means in plain words.
CRITERIA: Tuple[Tuple[str, str], ...] = (
    ("observer_visibility", "the sampling connection could not see the workers' locks"),
    ("wait_observed", "no frame ever showed the losing worker waiting on an advisory lock"),
    ("blocker_is_holder", "the waiting worker was not blocked by the worker that billed"),
    ("lock_object_matches", "the waited-for lock is not the object the holder holds"),
    ("key_unbilled_while_blocked", "the ledger already named the key during the wait"),
    ("bill_durable_after_release", "no frame after the release found the bill durable"),
    ("holder_claim_was_free", "the worker that billed was not told the seat was free"),
    ("loser_reread_refused", "the blocked worker did not come back refusing the key"),
    ("one_bill_per_business_key", "the durable ledger holds more than one bill for a key"),
    ("settlement_matches_bills", "the reservations do not match the bills one-for-one"),
    ("quota_never_stands_in_place_of_ledger", "the quota counter and the ledger disagree"),
    (
        "refusal_reason_is_the_ledger",
        "the second worker was stopped by the ceiling on its quota, not by the bill",
    ),
)

#: Why a unit was not spent, as the lane records it. A ceiling is not a dedup:
#: if the second worker was turned away by quota, nothing in this run proved
#: the seat held, and the run must say so instead of collecting the credit.
QUOTA_REASONS = ("quota_exceeded", "quota_unavailable", "ai_usage_limit_reached")

LEDGER_REASON = "already_estimated_this_window"

_SAYS = dict(CRITERIA)
_CRITERIA_IDS: Tuple[str, ...] = tuple(criterion_id for criterion_id, _says in CRITERIA)

#: What is never read when the capture names no single key the two workers met on,
#: and why. These are not failures: the run is not being told it waited on nothing,
#: it is being told this capture cannot answer the question at all, which is the
#: difference between a measurement and a default.
_SILENT_WITHOUT_ITEM: Tuple[Tuple[str, str], ...] = (
    (
        "observer_visibility",
        "the frames were never opened, because opening them is done for a key",
    ),
    (
        "holder_claim_was_free",
        "no key was named, so no claim on it was looked up",
    ),
    (
        "loser_reread_refused",
        "no key was named, so no refusal on it was looked up",
    ),
    (
        "blocker_is_holder",
        "there was no waiting row to ask who blocked it",
    ),
    (
        "lock_object_matches",
        "there was no waited-for object to compare a held lock against",
    ),
    (
        "key_unbilled_while_blocked",
        "the wait was never delimited, so no window of frames was read against the ledger",
    ),
    (
        "bill_durable_after_release",
        "the release was never located, so no frame after it was read",
    ),
)


def _rows_for(frame: Dict[str, Any], backend_pid: int) -> List[dict]:
    """The lock rows of one backend, as ``pg_locks`` reported them in a frame.

    The column this matches is ``l.pid`` from ``CAPTURE_SQL``, which is a
    PostgreSQL *backend* pid -- the process serving one connection, not the
    Python worker that owns it. A live run measured the two namespaces a few
    numbers apart (24594/24595 for the workers, 24607/24608 for the backends) and
    disjoint, so callers pass what :func:`_claim_backend` reads off the claim and
    nothing else can join here.
    """
    return [
        row
        for row in frame.get("rows") or []
        if int(row.get("pid", -1)) == int(backend_pid)
    ]


def _claim_backend(claim: Dict[str, Any]) -> Any:
    """The backend that ran one claim's advisory lock, or ``None`` when unnamed.

    Deliberately no fallback to ``claim["pid"]``: an OS pid has no row in
    ``pg_locks``, so a capture that never bound its backend cannot be judged on
    the server's account of the wait, and a number invented for it would be the
    one failure mode this gate cannot be recovered from -- a green that was never
    measured.
    """
    raw = claim.get("backend_pid")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _granted(frame: Dict[str, Any], backend_pid: int, *, granted: bool) -> List[dict]:
    return [
        row
        for row in _rows_for(frame, backend_pid)
        if bool(row.get("granted")) is granted
    ]


def _count(frame: Dict[str, Any], instrument: str) -> Any:
    """What this frame's ledger read counted for one key, or None if it did not look."""
    for read in frame.get("ledger") or []:
        if read.get("instrument") == instrument:
            return read.get("count")
    return None


def _claim(claims: List[dict], instrument: str, billed: bool) -> Dict[str, Any]:
    """The earliest claim on this key that heard this answer, or ``{}``.

    Earliest by the worker's own clock, not by the order the captures arrived:
    with two workers on two keys, the first worker told "free" is the one that
    took the seat, and the first told "already billed" is the one that stepped
    back. The later answers are the same two facts on the other key.
    """
    matches = [
        claim
        for claim in claims
        if claim.get("instrument") == instrument
        and bool(claim.get("already_billed")) is billed
    ]
    if not matches:
        return {}
    return min(matches, key=lambda claim: float(claim.get("t") or 0.0))


def assess_race(observation: Dict[str, Any]) -> Dict[str, Any]:
    """Judge one captured race. Never raises, never passes on a gap.

    The input is a plain dict, so a child process can hand it over a
    ``multiprocessing`` queue and so a refusal is quotable in a report:

    ``{"proven": bool, "refused": [{"id", "says", "detail"}], "item": str|None,
      "notes": {...}}``

    ``item`` is the business key the wait evidence is read against: the one key
    that was the *first* claim of both workers, which is where the two of them
    actually met. It is not "the key that ended up both billed and refused",
    because with two declared instruments the second key reaches that description
    too -- by the time the loser got to it, the winner had simply finished. The
    wait is judged where the claims overlapped, and the other keys only feed the
    durable-ledger criteria. A run in which both workers bill the same key is not
    given a chance to look good: it fails ``one_bill_per_business_key``, which is
    the finding the node exists to make.

    Every criterion whose evidence this function actually read is named in
    ``notes["evaluated"]`` -- whether the reading passed or refused -- and every
    criterion the shape of the capture kept it from reading is named in
    ``notes["not_evaluated"]`` with its reason in ``notes["not_evaluated_because"]``.
    Silence is the third answer and is never a pass: a capture that never read the
    ledger during the wait has not shown the key stayed unbilled, and a judgement
    that credits the gap manufactures evidence the run never produced.
    ``proven`` therefore needs the whole set reached *and* none refused.
    """
    refused: List[Dict[str, str]] = []
    notes: Dict[str, Any] = {}
    reached: List[str] = []
    skipped: Dict[str, str] = {}

    def refuse(criterion_id: str, detail: str) -> None:
        reached.append(criterion_id)
        refused.append({"id": criterion_id, "says": _SAYS[criterion_id], "detail": detail})

    def held(criterion_id: str) -> None:
        """The evidence for this criterion was read, and it did not refuse the run."""
        reached.append(criterion_id)

    def silent(criterion_id: str, why: str) -> None:
        """Nothing was consulted for this criterion, so it earns no verdict at all."""
        skipped[criterion_id] = why

    frames = list(observation.get("frames") or [])
    claims = list(observation.get("claims") or [])
    bills = list(observation.get("bills") or [])
    reservations = list(observation.get("reservation_statuses") or [])
    quota = observation.get("quota_consumed")

    instruments = sorted({c.get("instrument") for c in claims if c.get("instrument")})
    first_by_slot: Dict[Any, dict] = {}
    for claim in sorted(claims, key=lambda claim: float(claim.get("t") or 0.0)):
        first_by_slot.setdefault(claim.get("slot"), claim)
    met_on: Dict[Any, List[Any]] = {}
    for slot, claim in first_by_slot.items():
        met_on.setdefault(claim.get("instrument"), []).append(slot)
    raced = sorted(str(key) for key, slots in met_on.items() if len(slots) > 1)
    contested = [
        instrument
        for instrument in instruments
        if _claim(claims, instrument, False) and _claim(claims, instrument, True)
    ]
    notes["instruments_claimed"] = instruments
    notes["first_claim_by_slot"] = {
        str(slot): claim.get("instrument")
        for slot, claim in sorted(first_by_slot.items(), key=lambda row: str(row[0]))
    }
    notes["raced"] = raced
    notes["contested"] = contested
    notes["frames"] = len(frames)

    item = raced[0] if len(raced) == 1 else None
    if item is None:
        refuse(
            "wait_observed",
            f"the two workers did not meet on exactly one key first: {raced} "
            f"(first_claim_by_slot={notes['first_claim_by_slot']})",
        )
        for criterion_id, why in _SILENT_WITHOUT_ITEM:
            silent(criterion_id, f"no single key was met on first: {why}")
    else:
        winner = _claim(claims, item, False)
        loser = _claim(claims, item, True)
        # Two namespaces, and only the second one reaches ``pg_locks``. The OS pids
        # stay in the notes because a reader recognises them from the process
        # table; every row lookup below uses the backends.
        winner_backend = _claim_backend(winner)
        loser_backend = _claim_backend(loser)
        notes["item"] = item
        notes["winner_pid"] = winner.get("pid")
        notes["loser_pid"] = loser.get("pid")
        notes["winner_backend_pid"] = winner_backend
        notes["loser_backend_pid"] = loser_backend
        if not winner:
            refuse(
                "holder_claim_was_free",
                f"neither claim on {item} was told the seat was free",
            )
        else:
            held("holder_claim_was_free")
        if not loser:
            refuse(
                "loser_reread_refused",
                f"no claim on {item} came back refusing a key somebody else had billed",
            )
        else:
            held("loser_reread_refused")

        if not frames:
            refuse("observer_visibility", "the capture holds no frame at all")
        elif not any(frame.get("rows") for frame in frames):
            refuse(
                "observer_visibility",
                f"{len(frames)} frames and every one saw no advisory lock in this "
                "database: either nothing contended while the observer polled, or "
                "this role may not read other sessions' locks and wait events",
            )
        else:
            held("observer_visibility")

        # A wait is only *this* backend's wait. Without a bound backend there is no
        # row to look for, and the run says so instead of matching on an OS pid it
        # knows cannot be there.
        bound = winner_backend is not None and loser_backend is not None
        blocked = [
            (index, frame, row)
            for index, frame in enumerate(frames)
            for row in _granted(frame, loser_backend, granted=False)
        ] if (winner and loser and bound) else []
        if not blocked:
            if not winner or not loser:
                if not winner:
                    detail = f"no wait on {item} is recorded because nobody took the seat"
                else:
                    detail = (
                        f"no wait on {item} is recorded because nobody was refused "
                        "it -- both workers were served, so the seat was not doing "
                        "the stopping"
                    )
            elif not bound:
                detail = (
                    "the capture binds no PostgreSQL backend to the claim it is "
                    f"judging (winner pid={winner.get('pid')} -> "
                    f"{winner.get('backend_pid')!r}, loser pid={loser.get('pid')} -> "
                    f"{loser.get('backend_pid')!r}): pg_locks.pid names the backend "
                    "serving a connection, not the worker process, so an unbound "
                    "claim has no row that could be read as a wait and no OS pid may "
                    "stand in for one"
                )
            else:
                detail = (
                    f"no frame showed backend pid {loser_backend} holding an "
                    "ungranted advisory lock; a claim that came back late is not a "
                    "claim that waited"
                )
            refuse("wait_observed", detail)
        else:
            first_index, first_frame, first_row = blocked[0]
            last_index = blocked[-1][0]
            notes["blocked_frames"] = len(blocked)
            notes["waited_s"] = (
                None
                if blocked[-1][1].get("t") is None or first_frame.get("t") is None
                else round(float(blocked[-1][1]["t"]) - float(first_frame["t"]), 3)
            )

            blockers = [int(pid) for pid in (first_row.get("blockers") or [])]
            if winner_backend not in blockers:
                refuse(
                    "blocker_is_holder",
                    f"backend pid {loser_backend} was blocked by {blockers}, which "
                    f"does not name the billing worker's backend {winner_backend}",
                )
            else:
                held("blocker_is_holder")
            object_id = (
                first_row.get("classid"),
                first_row.get("objid"),
                first_row.get("objsubid"),
            )
            holder_rows = [
                row
                for row in _granted(first_frame, winner_backend, granted=True)
                if (row.get("classid"), row.get("objid"), row.get("objsubid")) == object_id
            ]
            if not holder_rows:
                refuse(
                    "lock_object_matches",
                    f"the holder was not seen granted on advisory object {object_id}",
                )
            else:
                held("lock_object_matches")
            if first_row.get("wait_event_type") != "Lock":
                refuse(
                    "wait_observed",
                    f"the waiting session reported wait_event_type="
                    f"{first_row.get('wait_event_type')!r}, not 'Lock'",
                )
            else:
                held("wait_observed")

            # Every frame of the wait must still find the key unbilled: the
            # commit that fills it is the same commit that releases the lock, so
            # a frame that already sees a bill means the refusal the worker
            # reported was available to it before it ever waited.
            during = [
                index
                for index in range(first_index, last_index + 1)
                if _count(frames[index], item) is not None
            ]
            if not during:
                refuse(
                    "key_unbilled_while_blocked",
                    "no frame taken during the wait read the ledger for this key",
                )
            elif any(int(_count(frames[index], item)) > 0 for index in during):
                refuse(
                    "key_unbilled_while_blocked",
                    "the ledger already named this key while the worker was "
                    "waiting, so its refusal is not a release-then-re-read result",
                )
            else:
                held("key_unbilled_while_blocked")
            after = [index for index in range(last_index + 1, len(frames)) if _count(frames[index], item) is not None]
            if not any(int(_count(frames[index], item)) >= 1 for index in after):
                refuse(
                    "bill_durable_after_release",
                    "no ledger read taken after the last blocked frame found a "
                    "durable bill for the key",
                )
            else:
                held("bill_durable_after_release")
            if bool(loser.get("already_billed")) is not True:
                refuse(
                    "loser_reread_refused",
                    f"the blocked worker's claim returned {loser.get('already_billed')!r}",
                )
            if bool(winner.get("already_billed")) is not False:
                refuse("holder_claim_was_free", "the billing worker's claim did not take the seat")

    by_instrument: Dict[Any, List[dict]] = {}
    for bill in bills:
        by_instrument.setdefault(bill.get("instrument"), []).append(bill)
    notes["bills_per_instrument"] = {
        str(key): len(rows) for key, rows in sorted(by_instrument.items(), key=lambda row: str(row[0]))
    }
    duplicates = [key for key, rows in by_instrument.items() if len(rows) > 1]
    if not bills:
        refuse("one_bill_per_business_key", "the durable ledger holds no bill at all")
    elif duplicates:
        refuse(
            "one_bill_per_business_key",
            f"keys billed more than once: {duplicates} -- counted from the "
            "ledger rows, not from any counter",
        )
    else:
        held("one_bill_per_business_key")

    settled = reservations.count("settled")
    if not bills and not reservations:
        silent(
            "settlement_matches_bills",
            "the capture holds neither a bill nor a reservation, so there is no "
            "mapping to agree or disagree -- two empty lists agreeing is arithmetic, "
            "not a reading of this run",
        )
    elif settled != len(bills) or any(status != "settled" for status in reservations):
        refuse(
            "settlement_matches_bills",
            f"reservations={reservations!r} against {len(bills)} bills",
        )
    else:
        held("settlement_matches_bills")

    # The masking check. Quota is not the ledger: it can look right while the
    # ledger double-bills, and it can look short while the ledger is correct.
    # Everything above counts ``bills``, so the verdict cannot be produced by a
    # counter; this criterion refuses the run where the two disagree, because a
    # disagreement is exactly where a second charge hides.
    if quota is None:
        refuse("quota_never_stands_in_place_of_ledger", "no quota figure was captured")
    elif int(quota) != len(bills):
        refuse(
            "quota_never_stands_in_place_of_ledger",
            f"quota consumed {quota} against {len(bills)} ledger bills; the "
            "verdict counted the ledger, so the counter neither hides nor excuses "
            "the difference",
        )
    else:
        held("quota_never_stands_in_place_of_ledger")

    reasons = [str(reason) for reason in (observation.get("refusal_reasons") or [])]
    notes["refusal_reasons"] = reasons
    if any(reason in QUOTA_REASONS for reason in reasons):
        refuse(
            "refusal_reason_is_the_ledger",
            f"a unit was refused by the ceiling ({reasons}), so this run never "
            "tested whether the seat holds",
        )
    elif item is None:
        silent(
            "refusal_reason_is_the_ledger",
            "no key was named as the one both workers met on first, so there is no "
            "refusal of it whose author this capture could identify",
        )
    elif LEDGER_REASON not in reasons:
        refuse(
            "refusal_reason_is_the_ledger",
            f"no refusal named the billing ledger: {reasons}",
        )
    else:
        held("refusal_reason_is_the_ledger")

    notes["evaluated"] = [
        criterion_id for criterion_id in _CRITERIA_IDS if criterion_id in reached
    ]
    notes["not_evaluated"] = [
        criterion_id for criterion_id in _CRITERIA_IDS if criterion_id not in reached
    ]
    notes["not_evaluated_because"] = {
        criterion_id: skipped[criterion_id]
        for criterion_id in notes["not_evaluated"]
        if criterion_id in skipped
    }
    return {
        "proven": not refused and not notes["not_evaluated"],
        "refused": refused,
        "item": item,
        "notes": notes,
    }


#: The business key the product's own dedup is keyed on, in the order a reader
#: has to name it. A start record that cannot spell all four does not say which
#: seat it walked up to, so the gate below treats it as silent rather than
#: guessing from the instrument alone.
BUSINESS_KEY_FIELDS: Tuple[str, ...] = ("owner_id", "skill_unit_id", "instrument", "window")

#: The five things the *start* gate can decide, and what a refusal on each means.
#: Deliberately separate from :data:`CRITERIA`: nothing here is evidence about
#: what the server made either worker do. ``assess_race`` answers that; this
#: answers only whether the contest genuinely began.
START_CRITERIA: Tuple[Tuple[str, str], ...] = (
    (
        "two_workers_reported",
        "the run does not name exactly two distinct OS workers at the claim door",
    ),
    (
        "each_worker_has_a_first_claim",
        "a worker that never stamped its arrival at the door has no place in the ordering",
    ),
    (
        "first_claims_share_one_business_key",
        "the two workers did not walk up to the same owner/skill/instrument/window seat",
    ),
    (
        "first_claim_recorded_before_entering_the_product_claim",
        "an arrival was written down at or after the product's own claim call began",
    ),
    (
        "both_arrived_before_the_first_estimate_began",
        "a worker reached its first claim only after somebody had started pricing",
    ),
)

_START_SAYS = dict(START_CRITERIA)
_START_IDS: Tuple[str, ...] = tuple(criterion_id for criterion_id, _says in START_CRITERIA)


def _business_key(start: Dict[str, Any]) -> Tuple[str, ...]:
    """The four-part seat one start record names, or ``()`` if it names less."""
    parts = tuple(start.get(field) for field in BUSINESS_KEY_FIELDS)
    if any(part is None for part in parts):
        return ()
    return tuple(str(part) for part in parts)


def _first_of(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One worker's earliest door record.

    By that worker's own sequence first, because the sequence is the loop's own
    order and cannot tie; its own clock is only the fallback for a record that
    predates the sequence. The clock of one worker is never used to order two
    different workers here -- that comparison is what :func:`assess_race_start`
    refuses to draw a conclusion from on its own.
    """
    return min(
        rows,
        key=lambda start: (
            start.get("seq") if isinstance(start.get("seq"), int) else float("inf"),
            float(start.get("t") or 0.0),
        ),
    )


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def assess_race_start(observation: Dict[str, Any]) -> Dict[str, Any]:
    """Judge whether the two workers genuinely met at the same seat, first.

    Passing this means one sentence and no more: *both* workers were standing at
    the door of the real :func:`app.services.daily_estimates.claim_estimate` for
    *one* business key before *either* began pricing. It is not evidence that
    PostgreSQL made one of them wait, that a lock was contended, or that the
    seat held -- those are the twelve ledger criteria in :func:`assess_race`,
    read from the server's own ``pg_locks`` frames. The old harness node inferred
    the wait from a time ordering, and this function exists so that inference is
    no longer available: it draws its five conclusions from the same data without
    ever naming a lock, and it stays fail-closed on a gap.

    ``{"proven": bool, "refused": [{"id", "says", "detail"}], "item": str|None,
      "notes": {"evaluated": [...], "not_evaluated": [...], ...}}``

    A criterion whose preconditions were missing is *not evaluated*, listed under
    ``not_evaluated`` rather than passed by absence -- so a capture that died
    before the second worker spoke can never read as a start that was proven.
    """
    refused: List[Dict[str, str]] = []
    notes: Dict[str, Any] = {}
    evaluated: List[str] = []

    def refuse(criterion_id: str, detail: str) -> None:
        refused.append(
            {"id": criterion_id, "says": _START_SAYS[criterion_id], "detail": detail}
        )

    starts = [row for row in (observation.get("starts") or []) if isinstance(row, dict)]
    claims = [row for row in (observation.get("claims") or []) if isinstance(row, dict)]
    signals = [row for row in (observation.get("worker_signals") or []) if isinstance(row, dict)]
    estimates = [row for row in (observation.get("estimates") or []) if isinstance(row, dict)]
    notes["start_records"] = len(starts)
    notes["estimate_records"] = len(estimates)
    notes["worker_signals"] = len(signals)

    seen: Dict[str, Any] = {}
    for rows in (starts, claims, signals):
        for row in rows:
            if row.get("slot") is None:
                continue
            seen.setdefault(str(row["slot"]), set()).add(row.get("pid"))
    slots = sorted(seen)
    pids = sorted({str(pid) for group in seen.values() for pid in group if pid is not None})
    pairs = sorted(
        f"{slot}:{pid}"
        for slot, group in seen.items()
        for pid in group
        if pid is not None
    )
    notes["workers_seen"] = {
        slot: sorted(str(pid) for pid in group) for slot, group in sorted(seen.items())
    }
    notes["worker_pids"] = pids
    notes["worker_slots"] = slots
    #: The process channel on its own: which slots the run read an exit status for.
    #: Marks alone can be written by one child that was told to play two roles, so a
    #: worker with no row here is named by the ordering and by nothing outside it.
    signal_slots = sorted(
        {str(row["slot"]) for row in signals if row.get("slot") is not None}
    )
    notes["signal_slots"] = signal_slots
    evaluated.append("two_workers_reported")
    if len(slots) != 2 or len(pids) != 2 or len(pairs) != 2 or len(signal_slots) != 2:
        refuse(
            "two_workers_reported",
            f"the arrival, claim and exit-status records together name {len(slots)} "
            f"worker slot(s), {len(pids)} process id(s), {len(pairs)} slot:pid "
            f"pair(s) and {len(signal_slots)} slot(s) with an exit status "
            f"({notes['workers_seen']}); two OS workers means two of each -- a slot "
            "without a pid, one slot holding two pids, one pid answering to two "
            "slots, or a worker the process table never reported is a fixture, not a "
            "second process",
        )

    first: Dict[str, Dict[str, Any]] = {}
    if len(slots) == 2:
        evaluated.append("each_worker_has_a_first_claim")
        for slot in slots:
            rows = [start for start in starts if str(start.get("slot")) == slot]
            if rows:
                first[slot] = _first_of(rows)
        if len(first) != 2:
            missing = [slot for slot in slots if slot not in first]
            refuse(
                "each_worker_has_a_first_claim",
                f"worker(s) {missing} are known to the run but never stamped a "
                "claim_enter, so their arrival has no time to order",
            )

    item: Any = None
    if len(first) == 2:
        keys = {slot: _business_key(start) for slot, start in sorted(first.items())}
        notes["first_claim_by_slot"] = {
            slot: {
                "t": first[slot].get("t"),
                "seq": first[slot].get("seq"),
                "call_start_t": first[slot].get("call_start_t"),
                "pid": first[slot].get("pid"),
                "key": list(keys[slot]),
            }
            for slot in sorted(first)
        }
        evaluated.append("first_claims_share_one_business_key")
        unnamed = sorted(slot for slot, key in keys.items() if not key)
        if unnamed:
            refuse(
                "first_claims_share_one_business_key",
                f"the first claim of worker(s) {unnamed} does not name every part of "
                f"{BUSINESS_KEY_FIELDS}, so it cannot be shown to be the same seat",
            )
        elif len(set(keys.values())) != 1:
            differing = [
                field
                for index, field in enumerate(BUSINESS_KEY_FIELDS)
                if len({key[index] for key in keys.values()}) > 1
            ]
            refuse(
                "first_claims_share_one_business_key",
                f"the two workers' first claims differ on {differing}: {keys} -- "
                "ordering two different seats says nothing about competing for one",
            )
        else:
            item = list(next(iter(keys.values())))[BUSINESS_KEY_FIELDS.index("instrument")]
            notes["item"] = item

        evaluated.append("first_claim_recorded_before_entering_the_product_claim")
        unstamped = sorted(
            slot
            for slot, start in first.items()
            if not _number(start.get("t")) or not _number(start.get("call_start_t"))
        )
        late = sorted(
            (
                slot,
                float(first[slot]["t"]),
                float(first[slot]["call_start_t"]),
            )
            for slot in first
            if _number(first[slot].get("t"))
            and _number(first[slot].get("call_start_t"))
            and float(first[slot]["t"]) > float(first[slot]["call_start_t"])
        )
        if unstamped or late:
            refuse(
                "first_claim_recorded_before_entering_the_product_claim",
                f"arrival marks that are not pre-call evidence: missing a stamp "
                f"{unstamped}; stamped at or after the real claim was entered {late}. "
                "The mark has to be written before the product function is called: "
                "one written from inside it records where the worker had got to, and "
                "says nothing about whether it had been let in.",
            )

        evaluated.append("both_arrived_before_the_first_estimate_began")
        priced = [float(row["t"]) for row in estimates if _number(row.get("t"))]
        if not priced:
            refuse(
                "both_arrived_before_the_first_estimate_began",
                f"{len(estimates)} estimate record(s) and not one carries a time, so "
                "nothing can be said about which came first",
            )
        elif unstamped:
            notes["first_estimate_enter_t"] = min(priced)
            refuse(
                "both_arrived_before_the_first_estimate_began",
                f"cannot order the run: worker(s) {unstamped} have no arrival time",
            )
        else:
            first_estimate = min(priced)
            arrived = {slot: float(start["t"]) for slot, start in sorted(first.items())}
            notes["first_estimate_enter_t"] = first_estimate
            notes["arrived_t"] = arrived
            notes["margin_s"] = round(first_estimate - max(arrived.values()), 6)
            behind = sorted(slot for slot, at in arrived.items() if at >= first_estimate)
            if behind:
                refuse(
                    "both_arrived_before_the_first_estimate_began",
                    f"worker(s) {behind} reached their first claim at "
                    f"{[arrived[slot] for slot in behind]}, which is not before the "
                    f"earliest estimate began at {first_estimate}",
                )

    notes["evaluated"] = [cid for cid in _START_IDS if cid in evaluated]
    notes["not_evaluated"] = [cid for cid in _START_IDS if cid not in evaluated]
    return {
        "proven": not refused and not notes["not_evaluated"],
        "refused": refused,
        "item": item,
        "notes": notes,
    }
