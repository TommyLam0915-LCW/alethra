'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import {
  PREFLIGHT_STATUS_COPY,
  buildDefaultSpec,
  formatConstraint,
  parseSpecText,
  type PreflightResult,
  type StrategyTemplateCatalog,
} from '@/lib/strategyPreflight';

export default function StrategyTemplatesPage() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<StrategyTemplateCatalog | null>(null);
  const [loadError, setLoadError] = useState('');
  const [template, setTemplate] = useState('buy_and_hold');
  const [specText, setSpecText] = useState('');

  const [preflighting, setPreflighting] = useState(false);
  const [result, setResult] = useState<PreflightResult | null>(null);
  const [localError, setLocalError] = useState('');
  const [requestError, setRequestError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadCatalog();
  }, [router]);

  const loadCatalog = async () => {
    try {
      const data = (await api.strategyTemplates.catalog()) as StrategyTemplateCatalog;
      setCatalog(data);
      const first = data.templates[0]?.name ?? 'buy_and_hold';
      setTemplate(first);
      setSpecText(JSON.stringify(buildDefaultSpec(first), null, 2));
    } catch (err: any) {
      setLoadError(err?.message || 'The template catalogue could not be loaded.');
    }
  };

  const handleTemplateChange = (name: string) => {
    setTemplate(name);
    setSpecText(JSON.stringify(buildDefaultSpec(name), null, 2));
    setResult(null);
    setLocalError('');
    setRequestError('');
  };

  const handlePreflight = async () => {
    if (preflighting) return;
    setLocalError('');
    setRequestError('');
    const parsed = parseSpecText(specText);
    if (!parsed.ok) {
      setResult(null);
      setLocalError(parsed.error);
      return;
    }
    setPreflighting(true);
    try {
      const data = (await api.strategyTemplates.preflight(parsed.value)) as PreflightResult;
      setResult(data);
    } catch (err: any) {
      setResult(null);
      setRequestError(err?.message || 'The preflight request failed.');
    } finally {
      setPreflighting(false);
    }
  };

  const statusCopy = result ? PREFLIGHT_STATUS_COPY[result.status] : null;
  const statusColour =
    statusCopy?.tone === 'ok'
      ? 'var(--success, #1d7a46)'
      : statusCopy?.tone === 'warn'
        ? 'var(--warning, #9a5b00)'
        : 'var(--text-muted)';

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '60px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href="/dashboard" style={{ color: 'var(--primary-text)', textDecoration: 'none' }}>
          ← Back to research
        </Link>
        <h1 style={{ fontSize: '26px', margin: '10px 0 6px' }}>Strategy templates</h1>
        <p style={{ color: 'var(--text-secondary)', maxWidth: '760px', margin: 0 }}>
          These are the templates the daily engine can actually run. Check a spec against
          the current contract before you build an experiment. Preflighting is read-only:
          it does not create, save, queue or run anything.
        </p>
      </div>

      {loadError && (
        <div className="card" role="alert" style={{ borderColor: 'var(--danger)', marginBottom: '20px' }}>
          <p style={{ margin: 0 }}>{loadError}</p>
        </div>
      )}

      {catalog && (
        <>
          <section data-testid="template-catalog" style={{ marginBottom: '32px' }}>
            <h2 style={{ fontSize: '18px', margin: '0 0 12px' }}>Supported now</h2>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                gap: '16px',
              }}
            >
              {catalog.templates.map((entry) => (
                <article
                  key={entry.name}
                  className="card"
                  data-testid={`template-card-${entry.name}`}
                  style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}
                >
                  <div>
                    <h3 style={{ fontSize: '16px', margin: 0 }}>{entry.label}</h3>
                    <code style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{entry.name}</code>
                  </div>
                  <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '13px' }}>
                    {entry.purpose}
                  </p>
                  <div>
                    <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '4px' }}>
                      Signal parameters
                    </div>
                    {entry.signal_parameters.length === 0 ? (
                      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        None required.
                      </div>
                    ) : (
                      <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '12px' }}>
                        {entry.signal_parameters.map((param) => (
                          <li key={param.name}>
                            <code>{param.name}</code>: {param.type} {param.min}–{param.max}{' '}
                            (required)
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '4px' }}>
                      Known limits
                    </div>
                    <ul
                      style={{
                        margin: 0,
                        paddingLeft: '18px',
                        fontSize: '12px',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {entry.known_limits.map((limit) => (
                        <li key={limit}>{limit}</li>
                      ))}
                    </ul>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section data-testid="unsupported-templates" style={{ marginBottom: '32px' }}>
            <h2 style={{ fontSize: '18px', margin: '0 0 8px' }}>Not available in this build</h2>
            <p style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              These are not offered as options because a spec using them would not run.
            </p>
            <div style={{ display: 'grid', gap: '10px' }}>
              {catalog.unsupported.map((entry) => (
                <div
                  key={entry.name}
                  className="card"
                  data-testid={`unsupported-${entry.name}`}
                  style={{ opacity: 0.85 }}
                >
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                    <strong>{entry.label}</strong>
                    <code style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{entry.name}</code>
                  </div>
                  <p style={{ margin: '4px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                    {entry.reason}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section data-testid="preflight-panel" style={{ marginBottom: '32px' }}>
            <h2 style={{ fontSize: '18px', margin: '0 0 8px' }}>Preflight a spec</h2>
            <p style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              Select a template, edit or paste its spec, then preflight. Choosing a template
              replaces the editor with that template&apos;s example.
            </p>
            <div className="card" style={{ display: 'grid', gap: '12px' }}>
              <label htmlFor="preflight-template" style={{ fontSize: '13px', fontWeight: 600 }}>
                Template
              </label>
              <select
                id="preflight-template"
                className="input"
                data-testid="preflight-template"
                value={template}
                onChange={(e) => handleTemplateChange(e.target.value)}
              >
                {catalog.templates.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.label}
                  </option>
                ))}
              </select>

              <label htmlFor="preflight-spec" style={{ fontSize: '13px', fontWeight: 600 }}>
                Strategy spec (JSON)
              </label>
              <textarea
                id="preflight-spec"
                className="input"
                data-testid="preflight-spec"
                aria-label="Strategy spec JSON"
                value={specText}
                onChange={(e) => setSpecText(e.target.value)}
                rows={18}
                spellCheck={false}
                style={{ fontFamily: 'monospace', fontSize: '12px', resize: 'vertical' }}
              />

              <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  data-testid="preflight-submit"
                  onClick={handlePreflight}
                  disabled={preflighting}
                >
                  {preflighting ? 'Checking…' : 'Preflight spec'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    setSpecText(JSON.stringify(buildDefaultSpec(template), null, 2));
                    setResult(null);
                    setLocalError('');
                    setRequestError('');
                  }}
                >
                  Reset to example
                </button>
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  Read-only. No experiment, version, run or job is created.
                </span>
              </div>

              {localError && (
                <p role="alert" data-testid="preflight-local-error" style={{ margin: 0, color: 'var(--danger)' }}>
                  {localError}
                </p>
              )}
              {requestError && (
                <p role="alert" data-testid="preflight-request-error" style={{ margin: 0, color: 'var(--danger)' }}>
                  {requestError}
                </p>
              )}
            </div>
          </section>

          {result && statusCopy && (
            <section
              className="card"
              data-testid="preflight-result"
              data-status={result.status}
              style={{ marginBottom: '32px', borderColor: statusColour }}
            >
              <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', marginBottom: '8px' }}>
                <strong data-testid="preflight-status" style={{ color: statusColour }}>
                  {statusCopy.label}
                </strong>
                {result.template_type && (
                  <code style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    {result.template_type}
                  </code>
                )}
              </div>
              <p style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                {statusCopy.detail}
              </p>
              {result.reason && (
                <p style={{ margin: '0 0 10px', fontSize: '13px' }} data-testid="preflight-reason">
                  {result.reason}
                </p>
              )}
              {result.errors.length > 0 && (
                <ul style={{ margin: '0 0 10px', paddingLeft: '18px', fontSize: '13px' }}>
                  {result.errors.map((error, index) => (
                    <li key={`${error.field}-${index}`} data-testid={`preflight-error-${error.field}`}>
                      <code>{error.field}</code>: {error.message}
                    </li>
                  ))}
                </ul>
              )}
              {result.status === 'valid' && (
                <div
                  style={{
                    display: 'flex',
                    gap: '12px',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    marginTop: '12px',
                  }}
                >
                  <Link
                    href="/experiments/new"
                    className="btn btn-primary"
                    data-testid="preflight-continue"
                  >
                    Continue to create an experiment
                  </Link>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    Opening the form creates nothing until you submit it there.
                  </span>
                </div>
              )}
            </section>
          )}

          <section>
            <details>
              <summary style={{ cursor: 'pointer', fontSize: '14px', fontWeight: 600 }}>
                Spec fields and formats
              </summary>
              <div className="card" style={{ marginTop: '10px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                  <thead>
                    <tr style={{ textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px' }}>Field</th>
                      <th style={{ padding: '6px 8px' }}>Type and constraints</th>
                      <th style={{ padding: '6px 8px' }}>Required</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalog.spec_fields.map((field) => (
                      <tr key={field.name}>
                        <td style={{ padding: '6px 8px', fontFamily: 'monospace' }}>{field.name}</td>
                        <td style={{ padding: '6px 8px' }}>{formatConstraint(field)}</td>
                        <td style={{ padding: '6px 8px' }}>{field.required ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                  {catalog.scope_note}
                </p>
              </div>
            </details>
          </section>
        </>
      )}
    </div>
  );
}
