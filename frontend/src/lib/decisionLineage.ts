// Reading of `inference_source_state`, the one column of
// GET /paper-portfolio/{id}/decision-history that says whether a decision's
// claimed origin is actually in the billing ledger.
//
// The page used to print `linked_inference_id` and stop there, which let a
// decision naming a row nobody can read back look exactly like a decision whose
// row was read back. The route already separates the three cases; the card has
// to as well.

/** Exactly the route's own vocabulary — no renamed middle ground. */
export type InferenceSourceState = 'present' | 'missing' | 'none';

/**
 * Narrow the export's value. Anything unrecognised stays ``undefined``: a state
 * this build does not know is not evidence of the state it is closest to, and
 * the card says it could not read rather than guessing.
 */
export function inferenceSourceState(value: unknown): InferenceSourceState | undefined {
  return value === 'present' || value === 'missing' || value === 'none' ? value : undefined;
}

export const SOURCE_STATE_LABELS: Record<InferenceSourceState, string> = {
  present: 'Source readable — the billing ledger has this row, for this owner',
  missing: 'Source unreadable — the decision names an inference id, but this owner\'s ledger has no such row',
  none: 'No source claimed by this decision',
};

// Why 「unreadable」 is not an accusation about the write: the write door
// verified the row, so whatever made it unreadable happened afterwards, or the
// row arrived without that door. Saying 「the source was deleted」 would claim
// more than this column proves.
export const SOURCE_STATE_MISSING_NOTE =
  'This says only that the row cannot be found under this owner now. It does not say when it ' +
  'disappeared, or whether the write that recorded it was verified — the entry point did check ' +
  'owner, personal-model prediction type and skill account at write time.';

/** Shown when the export carried a state string this build cannot name. */
export const SOURCE_STATE_UNREADABLE_NOTE =
  'The export carried a source state this page does not recognise, so nothing is claimed about it.';
