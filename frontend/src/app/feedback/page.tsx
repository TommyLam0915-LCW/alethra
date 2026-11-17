'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import {
  FEEDBACK_CATEGORIES,
  FEEDBACK_LIMITS,
  emptyFeedbackDraft,
  feedbackDraftFilename,
  formatFeedbackDraft,
  toFeedbackSubmitRequest,
  type FeedbackDraft,
  type FeedbackEntry,
} from '@/lib/feedbackDraft';

const SAVED_LIST_LIMIT = 20;

function messageOf(err: any): string {
  return err instanceof ApiError && err.message ? err.message : 'The request failed.';
}

function categoryLabel(value: string): string {
  return FEEDBACK_CATEGORIES.find((entry) => entry.value === value)?.label ?? value;
}

function Counted({
  field,
  value,
  limit,
}: {
  field: string;
  value: string;
  limit: number;
}) {
  const over = value.length > limit;
  return (
    <span
      data-testid={`feedback-count-${field}`}
      style={{ fontSize: '12px', color: over ? 'var(--danger)' : 'var(--text-muted)' }}
    >
      {over ? `${value.length}/${limit} characters — too long to submit` : `${value.length}/${limit}`}
    </span>
  );
}

export default function FeedbackDraftPage() {
  const [draft, setDraft] = useState<FeedbackDraft>(emptyFeedbackDraft);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState('');

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [receipt, setReceipt] = useState<FeedbackEntry | null>(null);

  const [saved, setSaved] = useState<FeedbackEntry[]>([]);
  const [savedLoading, setSavedLoading] = useState(true);
  const [savedError, setSavedError] = useState('');
  // A refresh can overlap a submission's own read-back. Only the newest reply
  // may replace the list, so a slow earlier one cannot restore stale rows.
  const savedSeq = useRef(0);

  const text = useMemo(() => formatFeedbackDraft(draft), [draft]);

  const loadSaved = useCallback(async () => {
    const seq = ++savedSeq.current;
    setSavedLoading(true);
    setSavedError('');
    try {
      const rows = (await api.feedback.list(SAVED_LIST_LIMIT)) as FeedbackEntry[];
      if (savedSeq.current !== seq) return;
      setSaved(rows);
    } catch (err: any) {
      if (savedSeq.current !== seq) return;
      setSaved([]);
      setSavedError(
        err instanceof ApiError && err.status === 401
          ? 'Sign in to see the reports saved under your account.'
          : messageOf(err),
      );
    } finally {
      if (savedSeq.current === seq) setSavedLoading(false);
    }
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || !localStorage.getItem('token')) {
      setSaved([]);
      setSavedLoading(false);
      setSavedError('Sign in to see the reports saved under your account.');
      return;
    }
    loadSaved();
  }, [loadSaved]);

  const update = (field: keyof FeedbackDraft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setCopied(false);
    setCopyError('');
  };

  const handleSubmit = async () => {
    const request = toFeedbackSubmitRequest(draft);
    if (!request.steps || !request.actual) {
      setSaveError('Fill in both "Steps taken" and "Actual result" before submitting.');
      return;
    }
    setSaving(true);
    setSaveError('');
    setReceipt(null);
    try {
      const created = (await api.feedback.submit(request)) as FeedbackEntry;
      let readBack: FeedbackEntry;
      try {
        readBack = (await api.feedback.get(String(created.id))) as FeedbackEntry;
      } catch (err: any) {
        setSaveError(`The server accepted the report but it could not be read back: ${messageOf(err)}`);
        return;
      }
      if (
        readBack.id !== created.id ||
        readBack.category !== created.category ||
        readBack.steps !== created.steps ||
        readBack.actual !== created.actual
      ) {
        setSaveError('The report read back does not match what was submitted.');
        return;
      }
      setReceipt(readBack);
      await loadSaved();
    } catch (err: any) {
      setSaveError(messageOf(err));
    } finally {
      setSaving(false);
    }
  };

  const handleCopy = async () => {
    setCopyError('');
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard is unavailable');
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch (err: any) {
      setCopied(false);
      setCopyError(err?.message || 'The draft could not be copied.');
    }
  };

  const handleDownload = () => {
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = feedbackDraftFilename();
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '60px' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ fontSize: '26px', margin: '0 0 6px' }}>Feedback draft</h1>
        <p style={{ color: 'var(--text-secondary)', maxWidth: '760px', margin: 0 }}>
          Write a report here, then submit it to your account, or copy or export it
          and share it through your usual support channel.
        </p>
      </div>

      <div
        className="card"
        data-testid="feedback-status"
        role="status"
        style={{ borderColor: 'var(--warning, #9a5b00)', marginBottom: '20px' }}
      >
        {receipt ? (
          <>
            <strong>Submitted — saved under your account.</strong>
            <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              The receipt below was read back from the server, not kept in this browser.
              This build stores the report and lists it to you; it does not create a ticket
              number and does not notify anyone outside your own account. Anything you have
              not submitted again is still only a draft.
            </p>
          </>
        ) : (
          <>
            <strong>Draft only — not submitted.</strong>
            <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Nothing is sent to the platform until you press “Submit to platform”. Copying
              and downloading never send anything. No ticket number exists. An unsubmitted
              draft is not saved anywhere: if you leave the page it is gone unless you
              copied or exported it.
            </p>
          </>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: '20px',
        }}
      >
        <section className="card" aria-label="Feedback draft fields">
          <div style={{ display: 'grid', gap: '12px' }}>
            <div className="form-group" style={{ margin: 0 }}>
              <label htmlFor="feedback-category">Problem category</label>
              <select
                id="feedback-category"
                className="input"
                data-testid="feedback-category"
                value={draft.category}
                onChange={(e) => update('category', e.target.value)}
              >
                {FEEDBACK_CATEGORIES.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group" style={{ margin: 0 }}>
              <label htmlFor="feedback-page">Page or screen</label>
              <input
                id="feedback-page"
                className="input"
                data-testid="feedback-page"
                value={draft.page}
                onChange={(e) => update('page', e.target.value)}
                placeholder="For example: Experiment results, or /workspace"
              />
            </div>

            <div className="form-group" style={{ margin: 0 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  gap: '8px',
                }}
              >
                <label htmlFor="feedback-steps">Steps taken</label>
                <Counted field="steps" value={draft.steps} limit={FEEDBACK_LIMITS.steps} />
              </div>
              <textarea
                id="feedback-steps"
                className="input"
                data-testid="feedback-steps"
                rows={4}
                value={draft.steps}
                onChange={(e) => update('steps', e.target.value)}
                placeholder="What did you click or enter, in order?"
                style={{ resize: 'vertical' }}
              />
            </div>

            <div className="form-group" style={{ margin: 0 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  gap: '8px',
                }}
              >
                <label htmlFor="feedback-expected">Expected result</label>
                <Counted field="expected" value={draft.expected} limit={FEEDBACK_LIMITS.expected} />
              </div>
              <textarea
                id="feedback-expected"
                className="input"
                data-testid="feedback-expected"
                rows={3}
                value={draft.expected}
                onChange={(e) => update('expected', e.target.value)}
                style={{ resize: 'vertical' }}
              />
            </div>

            <div className="form-group" style={{ margin: 0 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  gap: '8px',
                }}
              >
                <label htmlFor="feedback-actual">Actual result</label>
                <Counted field="actual" value={draft.actual} limit={FEEDBACK_LIMITS.actual} />
              </div>
              <textarea
                id="feedback-actual"
                className="input"
                data-testid="feedback-actual"
                rows={3}
                value={draft.actual}
                onChange={(e) => update('actual', e.target.value)}
                style={{ resize: 'vertical' }}
              />
            </div>

            <div className="form-group" style={{ margin: 0 }}>
              <label htmlFor="feedback-reference">Error reference (optional)</label>
              <input
                id="feedback-reference"
                className="input"
                data-testid="feedback-reference"
                value={draft.errorReference}
                onChange={(e) => update('errorReference', e.target.value)}
                placeholder="The reference number shown with an error, if any"
              />
            </div>

            <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
              Do not paste passwords, API keys or access tokens. A submitted report carries
              only the fields above, plus which account sent it — the page attaches no
              cookies, headers, environment or other session data.
            </p>
          </div>
        </section>

        <section className="card" aria-label="Feedback draft preview">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              gap: '8px',
              marginBottom: '8px',
            }}
          >
            <h2 style={{ fontSize: '15px', margin: 0 }}>Preview</h2>
            <span data-testid="feedback-submit-state" style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              {saving ? 'Submitting…' : receipt ? 'Submitted' : 'Not submitted'}
            </span>
          </div>
          <pre
            data-testid="feedback-preview"
            aria-label="Feedback draft preview text"
            style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontFamily: 'monospace',
              fontSize: '12px',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '6px',
              padding: '12px',
              margin: '0 0 12px',
              maxHeight: '420px',
              overflow: 'auto',
            }}
          >
            {text}
          </pre>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              type="button"
              className="btn btn-primary"
              data-testid="feedback-submit"
              onClick={handleSubmit}
              disabled={saving}
            >
              {saving ? 'Submitting…' : 'Submit to platform'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="feedback-copy"
              onClick={handleCopy}
            >
              {copied ? 'Copied' : 'Copy draft'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="feedback-download"
              onClick={handleDownload}
            >
              Download .md
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="feedback-clear"
              onClick={() => {
                setDraft(emptyFeedbackDraft());
                setCopied(false);
                setCopyError('');
              }}
            >
              Clear
            </button>
          </div>
          {copied && (
            <p data-testid="feedback-copied" style={{ margin: '8px 0 0', fontSize: '12px' }}>
              Copied to the clipboard. This copy still has not been submitted — copying
              never sends anything.
            </p>
          )}
          {copyError && (
            <p
              role="alert"
              data-testid="feedback-copy-error"
              style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--danger)' }}
            >
              {copyError}
            </p>
          )}
          {saveError && (
            <p
              role="alert"
              data-testid="feedback-submit-error"
              style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--danger)' }}
            >
              Not submitted: {saveError}
            </p>
          )}
          {receipt && (
            <div
              data-testid="feedback-receipt"
              style={{
                marginTop: '12px',
                border: '1px solid var(--border-subtle)',
                borderRadius: '6px',
                padding: '12px',
                background: 'var(--surface-2)',
              }}
            >
              <div style={{ fontSize: '13px', marginBottom: '6px' }}>
                <strong>Saved.</strong> Read back from the server as report{' '}
                <code data-testid="feedback-receipt-id">{receipt.id}</code>
                {' '}at <span data-testid="feedback-receipt-time">{formatDateTime(receipt.created_at)}</span>
                {' '}by <span data-testid="feedback-receipt-user">{receipt.submitted_by}</span>.
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Category: {categoryLabel(receipt.category)}
                {receipt.page ? ` · Page: ${receipt.page}` : ''}
                {receipt.error_reference ? ` · Reference: ${receipt.error_reference}` : ''}
              </div>
            </div>
          )}
        </section>
      </div>

      <section
        className="card"
        aria-label="Reports saved under your account"
        style={{ marginTop: '20px' }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: '8px',
            marginBottom: '10px',
          }}
        >
          <h2 style={{ fontSize: '15px', margin: 0 }}>Saved reports</h2>
          <button
            type="button"
            className="btn btn-secondary"
            data-testid="feedback-refresh"
            onClick={loadSaved}
            disabled={savedLoading}
          >
            {savedLoading ? 'Reloading…' : 'Reload from server'}
          </button>
        </div>
        {savedError && (
          <p
            role="alert"
            data-testid="feedback-list-error"
            style={{ margin: '0 0 10px', fontSize: '12px', color: 'var(--danger)' }}
          >
            {savedError}
          </p>
        )}
        {!savedError && saved.length === 0 && !savedLoading && (
          <p data-testid="feedback-list-empty" style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
            No report has been submitted from this account yet.
          </p>
        )}
        {saved.length > 0 && (
          <table
            data-testid="feedback-saved-list"
            style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}
          >
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                {['Saved', 'Category', 'Page', 'Actual result', 'Reference'].map((heading) => (
                  <th key={heading} style={{ padding: '6px', textAlign: 'left' }}>
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {saved.map((row) => (
                <tr key={row.id} data-testid="feedback-saved-row" style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                    {formatDateTime(row.created_at)}
                  </td>
                  <td style={{ padding: '6px', verticalAlign: 'top' }}>{categoryLabel(row.category)}</td>
                  <td style={{ padding: '6px', verticalAlign: 'top' }}>{row.page || '—'}</td>
                  <td
                    style={{ padding: '6px', verticalAlign: 'top', wordBreak: 'break-word' }}
                    title={row.steps ? `Steps: ${row.steps}` : undefined}
                  >
                    {row.actual}
                  </td>
                  <td style={{ padding: '6px', verticalAlign: 'top' }}>{row.error_reference || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
          This table is the server’s own copy, re-read from your account — it is not the
          text still in the form above.
        </p>
      </section>
    </div>
  );
}
