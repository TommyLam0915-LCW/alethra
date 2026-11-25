/** The two currency rules a frozen paper-order review lives by.

``buildReview`` in the workspace panel stamps one flat list of the facts a
submission depends on, and every later render re-derives it: any difference
voids the review before a byte leaves the browser. The server keeps its own
counter for the same state (``state_version``) and refuses a write that carries
an old one, so the counter belongs in the stamp too — without it the panel
freezes a number another actor has already moved, spends a round trip learning
that, and asks the owner to re-check something the screen could have seen for
free.

The second rule points the other way. A response arrives *after* an await, and by
then the owner may have moved to another session. Reading the ``session`` value
that the callback closed over proves only what was true when the request left,
which is exactly the fact that cannot be trusted on arrival — so the check takes
what the view holds now, and refuses to install a payload about a session nobody
is looking at.
 */

export interface ReviewFacts {
  portfolioId: string | null;
  instrumentId: string;
  action: string;
  sessionId: string | null;
  snapshotDate: string | null;
  barTimestamp: string | null;
  status: string | null;
  /** The server's lock counter. ``null`` for a session row that predates it. */
  stateVersion: number | null;
  /** Quantity for a buy, ``null`` for the actions that carry none. */
  buyQuantity: number | null;
  rationale: string;
  /** The named origin, and only while the owner still has it attached. */
  attachedInferenceId: string | null;
}

/** One comparable value for everything the submitted decision depends on. */
export function reviewStamp(facts: ReviewFacts): string {
  return JSON.stringify([
    facts.portfolioId,
    facts.instrumentId,
    facts.action,
    facts.sessionId,
    facts.snapshotDate,
    facts.barTimestamp,
    facts.status,
    facts.stateVersion,
    facts.buyQuantity,
    facts.rationale,
    facts.attachedInferenceId,
  ]);
}

/** The version to freeze with a review, or ``null`` when there is no lock to trip. */
export function frozenVersion(session: { state_version?: number } | null): number | null {
  return typeof session?.state_version === 'number' ? session.state_version : null;
}

/** May a response about ``answeredId`` still be installed into this view?
 *
 * Both sides have to name a session: a payload that carries no id and a view
 * that has not loaded one are two kinds of nothing, and "nothing equals nothing"
 * is how a write about no session at all used to reach the screen.
 */
export function isStillViewed(
  viewedId: string | null | undefined,
  answeredId: string | null | undefined
): boolean {
  return !!viewedId && !!answeredId && viewedId === answeredId;
}
