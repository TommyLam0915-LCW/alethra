// What one "Run backtest" click costs when the previous click was not answered.
//
// backend/app/api/runs.py bills a research unit per admitted request and
// recognises a retry only through the `Idempotency-Key` header. So a click whose
// response never arrived — a dropped connection, a proxy that gave up while the
// backtest kept running — is indistinguishable from a deliberate re-run unless
// the next click sends the same key. These functions are the whole policy, kept
// out of the components so it can be measured without rendering one.
//
// The key is kept only when there was no HTTP response at all. A 5xx counts as
// answered: the server spoke, the user can see what it said, and holding the key
// would make every later click on that version replay the same closed run
// instead of starting the new one the user is asking for.
//
// MAX_IDEMPOTENCY_KEY_LENGTH is the server's number, not this file's: it is the
// length of `research_runs.idempotency_key`, and a longer key is refused with 400
// before anything is admitted. frontend/tests/runRetry.test.mjs reads that column
// out of backend/app/models/research_run.py and pins the two together, so a
// rename on one side is a failing lane rather than a header the server rejects.

export interface RunAttempt {
  versionId: string;
  key: string;
}

export const MAX_IDEMPOTENCY_KEY_LENGTH = 128;

/** The platform's own source of keys: a v4 UUID, 36 characters. */
export function mintRunKey(): string {
  return globalThis.crypto.randomUUID();
}

/** A key the server will accept: non-blank and within the column. */
export function newRunKey(mint: () => string): string | null {
  let key: unknown;
  try {
    key = mint();
  } catch {
    // No crypto in this runtime (an insecure origin, say). The caller runs
    // without a key, which is what every click did before this policy existed:
    // a retry costs a unit, and nothing here pretends otherwise.
    return null;
  }
  return typeof key === 'string' && key.trim() && key.length <= MAX_IDEMPOTENCY_KEY_LENGTH
    ? key.trim()
    : null;
}

/**
 * The attempt this click represents: the stored one, or a fresh key.
 *
 * A stored key belongs to one version. A click on any other version is a
 * different request and must not inherit it — the backend scopes the key to the
 * version, so reusing it there would only ever produce a conflict.
 */
export function attemptFor(
  stored: RunAttempt | null,
  versionId: string,
  mint: () => string
): { attempt: RunAttempt | null; reused: boolean } {
  if (stored && stored.versionId === versionId) {
    return { attempt: stored, reused: true };
  }
  const key = newRunKey(mint);
  return { attempt: key ? { versionId, key } : null, reused: false };
}

/**
 * What to remember after the request returned.
 *
 * `answered` is whether the server sent an HTTP response at all — a 409 is an
 * answer, a rejected `fetch` is not. Only the unanswered case keeps the key, and
 * it keeps it for exactly one version.
 */
export function keepAttemptAfter(
  attempt: RunAttempt | null,
  answered: boolean
): RunAttempt | null {
  return attempt && !answered ? attempt : null;
}

/**
 * Whether a rejected request proved the server spoke.
 *
 * `ApiError` carries the HTTP status of a response that was read and refused —
 * any status, including a 5xx, is an answer. A `fetch` that never completed
 * throws a `TypeError` with no status, and `authorizedFetch`'s expired-session
 * error carries none either, so the presence of a numeric status is the
 * distinction, without importing the error class into this file.
 */
export function wasAnswered(err: unknown): boolean {
  return (
    typeof err === 'object' &&
    err !== null &&
    typeof (err as { status?: unknown }).status === 'number'
  );
}
