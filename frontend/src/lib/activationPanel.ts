/**
 * Pure decisions for the activation panel on /agent-strategy.
 *
 * The panel reads, approves and withdraws one artifact at a time, and every one
 * of those calls is awaited. Between the call and its answer the owner can
 * select a different artifact, so an answer that arrives late still belongs to
 * the artifact it was asked about — not to the one on screen. Left unchecked it
 * would write the previous artifact's status, its acknowledgement text and its
 * success notice into the panel now showing another job, which is how a screen
 * ends up narrating an approval the server never made for that artifact.
 *
 * The three questions a click leaves behind are separate facts and stay
 * separate here:
 *
 * * did the write resolve, or did the gate refuse it (409 with a named code)?
 * * did a read of *this* artifact come back at all?
 * * does that read show the intent holding?
 *
 * Only the conjunction of all three is a success notice. `backend/app/services/
 * model_activation.py` answers the third from recorded rows, so this module
 * never infers an approval from the fact that a request was sent.
 */

// Mirrors ACTIVATED / NOT_APPROVED at backend/app/services/model_activation.py:36-37.
export const ACTIVATION_APPROVED = 'APPROVED';
export const ACTIVATION_NOT_APPROVED = 'NOT_APPROVED';

export type ActivationAction = 'approve' | 'withdraw';
export type ActivationTone = 'ok' | 'warn' | 'err';

export interface ActivationNotice {
  tone: ActivationTone;
  text: string;
}

/** The part of `GET /training/jobs/{id}/activation` that settles an intent. */
export interface ActivationRead {
  activation_status: string;
  reason_code: string | null;
  approval_id: string | null;
}

/** Which read of which artifact a panel currently owns. */
export interface ActivationReadId {
  jobId: string;
  seq: number;
}

export const NO_ACTIVATION_READ: ActivationReadId = { jobId: '', seq: 0 };

/**
 * A new read is the current one for as long as it is both the newest and the
 * one asked about the artifact on screen.
 */
export function beginActivationRead(
  jobId: string,
  previous: ActivationReadId,
): ActivationReadId {
  return { jobId, seq: previous.seq + 1 };
}

/**
 * Whether the artifact a click was made on is still the artifact on screen.
 * Writes (a notice, a busy flag, a cleared acknowledgement) belong to the panel
 * only while this holds.
 */
export function isDisplayedActivationJob(
  current: ActivationReadId,
  jobId: string,
): boolean {
  return current.jobId !== '' && current.jobId === jobId;
}

/**
 * Whether an answer may still touch the panel. `answeredJobId` is the id the
 * server put in its own response (`requirements.job_id`), so a payload that
 * identifies a different artifact is dropped even when the client asked for the
 * right one. An unanswered id (null/undefined) is not a mismatch: a refusal
 * body carries no requirements.
 */
export function acceptActivationRead(
  current: ActivationReadId,
  read: ActivationReadId,
  answeredJobId?: string | null,
): boolean {
  if (!isDisplayedActivationJob(current, read.jobId)) return false;
  if (current.seq !== read.seq) return false;
  if (answeredJobId != null && answeredJobId !== read.jobId) return false;
  return true;
}

export type ActivationVerification =
  | { state: 'confirmed' }
  | { state: 'not_confirmed'; observed_status: string; reason_code: string | null }
  | { state: 'read_failed' }
  | { state: 'read_stale' };

/** What one awaited read of the activation endpoint turned out to be. */
export type ActivationReadResult =
  | { outcome: 'loaded'; state: ActivationRead }
  | { outcome: 'failed' }
  | { outcome: 'stale' };

/**
 * Turn the outcome of the read that follows a write into the verification the
 * notice is allowed to claim. A read that never happened is never reported as
 * the state it was meant to confirm.
 */
export function verificationOf(
  action: ActivationAction,
  read: ActivationReadResult,
): ActivationVerification {
  if (read.outcome === 'loaded') return verifyActivation(action, read.state);
  if (read.outcome === 'stale') return { state: 'read_stale' };
  return { state: 'read_failed' };
}

/** An approval only counts when the record itself is named; a status of
 *  APPROVED with no `approval_id` is not a row the gate can point at. */
export function activationIntentHeld(
  action: ActivationAction,
  read: ActivationRead,
): boolean {
  const approved =
    read.activation_status === ACTIVATION_APPROVED && read.approval_id !== null;
  return action === 'approve' ? approved : !approved;
}

export function verifyActivation(
  action: ActivationAction,
  read: ActivationRead,
): ActivationVerification {
  return activationIntentHeld(action, read)
    ? { state: 'confirmed' }
    : {
        state: 'not_confirmed',
        observed_status: read.activation_status,
        reason_code: read.reason_code,
      };
}

export type ActivationOutcome =
  | { refused: { message: string; tone: 'warn' | 'err' } }
  | { verified: ActivationVerification };

const VERBS: Record<ActivationAction, { past: string; participle: string }> = {
  approve: { past: 'recorded', participle: 'approved' },
  withdraw: { past: 'withdrawn', participle: 'withdrawn' },
};

/**
 * The sentence the panel is allowed to show. A write that came back without a
 * confirming read says so instead of claiming the state it was aiming for, so
 * "submitted, status unverified" can never be read as a completed approval.
 */
export function activationWriteNotice(
  action: ActivationAction,
  outcome: ActivationOutcome,
): ActivationNotice {
  const verb = VERBS[action].past;
  if ('refused' in outcome) {
    return {
      tone: outcome.refused.tone,
      text: `The approval was not ${verb}: ${outcome.refused.message}`,
    };
  }
  const verification = outcome.verified;
  if (verification.state === 'confirmed') {
    return {
      tone: 'ok',
      text:
        action === 'approve'
          ? 'Approval recorded, and this artifact reads back as APPROVED on the server.'
          : 'Approval withdrawn, and this artifact no longer reads back as APPROVED on the server.',
    };
  }
  if (verification.state === 'read_stale') {
    return {
      tone: 'warn',
      text:
        `The request was sent, but the panel had already moved to another artifact, ` +
        `so this one's status was not read back. It is not shown as ${VERBS[action].participle}.`,
    };
  }
  if (verification.state === 'read_failed') {
    return {
      tone: 'warn',
      text:
        `The request was sent, but its status could not be read back. ` +
        `This artifact is not shown as ${VERBS[action].participle} until it is.`,
    };
  }
  return {
    tone: 'warn',
    text:
      `The request was sent, but the server still reads this artifact as ` +
      `${verification.observed_status}` +
      (verification.reason_code ? ` (${verification.reason_code})` : '') +
      `. It is not shown as ${VERBS[action].participle}.`,
  };
}
