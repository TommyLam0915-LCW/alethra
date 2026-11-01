'use client';

import { useMemo, useState } from 'react';
import {
  FEEDBACK_CATEGORIES,
  emptyFeedbackDraft,
  feedbackDraftFilename,
  formatFeedbackDraft,
  type FeedbackDraft,
} from '@/lib/feedbackDraft';

export default function FeedbackDraftPage() {
  const [draft, setDraft] = useState<FeedbackDraft>(emptyFeedbackDraft);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState('');

  const text = useMemo(() => formatFeedbackDraft(draft), [draft]);

  const update = (field: keyof FeedbackDraft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setCopied(false);
    setCopyError('');
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
          Write a report here, then copy or export it and share it through your usual
          support channel.
        </p>
      </div>

      <div
        className="card"
        data-testid="feedback-status"
        role="status"
        style={{ borderColor: 'var(--warning, #9a5b00)', marginBottom: '20px' }}
      >
        <strong>Draft only — not submitted.</strong>
        <p style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
          This page does not send anything to the platform, does not create a ticket, and
          cannot show progress. No ticket number exists. Nothing is saved to this browser:
          if you leave the page, the draft is gone unless you copied or exported it.
        </p>
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
              <label htmlFor="feedback-steps">Steps taken</label>
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
              <label htmlFor="feedback-expected">Expected result</label>
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
              <label htmlFor="feedback-actual">Actual result</label>
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
              Do not paste passwords, API keys or access tokens. This page never reads
              cookies or account credentials.
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
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Not submitted</span>
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
              Copied to the clipboard. It still has not been submitted.
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
        </section>
      </div>
    </div>
  );
}
