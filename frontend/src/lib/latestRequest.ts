/**
 * Drops async responses that are no longer the newest request.
 *
 * A handler that writes state after an `await` runs whenever *its* response
 * resolves, and HTTP responses have no ordering guarantee: with a 4-second poll
 * (`src/app/training/page.tsx:229`), a route parameter that changes mid-flight
 * (`src/app/experiments/[id]/page.tsx:183`), or two clicks on a Retry button
 * (`src/app/dashboard/page.tsx:192`), the request issued first can be the one
 * that lands last. The state then shows the older answer, and the older
 * answer's `finally` also clears the loading flag the newer request raised.
 * The result is not a cosmetic flash — the compare view, the robustness study
 * list and the training job table all render rows from the discarded response.
 *
 * The fix is a per-component sequence number. Each handler `begin()`s before it
 * issues the request and only writes state, or clears a loading flag, while its
 * own token is still `isCurrent()`. Nothing is cancelled on the wire: an
 * obsolete read is allowed to finish and is then ignored, which keeps the
 * guard free of side effects on the server.
 */

export interface RequestGuard {
  /** Marks this moment as the newest request and returns its token. */
  begin(): number;
  /** True while `token` still names the newest request begun by this guard. */
  isCurrent(token: number): boolean;
}

export function createRequestGuard(): RequestGuard {
  let seq = 0;
  return {
    begin() {
      seq += 1;
      return seq;
    },
    isCurrent(token: number) {
      return token === seq;
    },
  };
}
