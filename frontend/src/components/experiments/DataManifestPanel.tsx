'use client';

/**
 * The data-choice step of authoring: what the stored dataset behind this exact
 * spec is, before an experiment is created from it.
 *
 * Everything the panel states about the dataset comes from the server response
 * — including the blockers, their remedies, the per-mode run behaviour and the
 * `cannot_prove` list — because the manifest is computed by the same engine
 * functions a run calls (app/engine/backtest.py :: _load_stored_bars,
 * _ambiguous_dataset_error, _compute_stored_checksum). This component decides
 * layout and how a missing value is displayed; it adds no verdict of its own,
 * and it never shows a check as current once the parameters it described have
 * changed.
 */

import { useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import {
  DataManifest,
  ManifestInstrument,
  ManifestIssue,
  display,
  displayChecksum,
  isStale,
  manifestStatusCopy,
  previewDates,
  providerCandidates,
  roleLegend,
  specFingerprint,
  statusToneColour,
} from '@/lib/dataManifest';

interface Props {
  /** The same strategy_spec object the create call sends. */
  spec: Record<string, unknown>;
  /** The provider the dataset check starts bound to, if the caller has one. */
  providerId?: string | null;
  assetLabel?: string;
}

function IssueList({
  issues,
  kind,
}: {
  issues: ManifestIssue[];
  kind: 'blocker' | 'warning';
}) {
  if (issues.length === 0) return null;
  return (
    <ul
      data-testid={`exp-manifest-${kind}s`}
      style={{ margin: '0 0 10px', paddingLeft: '18px', fontSize: '13px' }}
    >
      {issues.map((issue, index) => {
        return (
          <li
            key={`${issue.code}-${index}`}
            data-testid={`exp-manifest-${kind}-${index}`}
            style={{ marginBottom: '6px' }}
          >
            <code style={{ fontSize: '12px' }}>{issue.code}</code>
            {issue.instrument_id ? <span> · {issue.instrument_id}</span> : null}
            <div>{issue.message}</div>
            {issue.remedy && (
              <div data-testid={`exp-manifest-${kind}-${index}-remedy`} style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>
                Remedy: {issue.remedy}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function GrainCell({ row }: { row: ManifestInstrument }) {
  if (row.grains.length === 0) return <>no grain facts (no rows resolved)</>;
  return (
    <>
      {row.grains.map((grain, index) => (
        <span
          key={`${grain.interval_minutes}-${index}`}
          data-testid={`exp-manifest-grain-${row.instrument_id}-${index}`}
          style={{ marginRight: '8px', whiteSpace: 'nowrap' }}
        >
          {grain.label}
          {grain.interval_minutes === null ? '' : ` (${grain.interval_minutes}m)`} —{' '}
          {display(grain.bar_count)} bars
        </span>
      ))}
    </>
  );
}

function CalendarCell({ row }: { row: ManifestInstrument }) {
  const calendar = row.calendar;
  if (!calendar) return <>no calendar block for this row</>;
  const missing = previewDates(calendar.missing_known_open_sessions);
  const closed = previewDates(calendar.bars_on_days_the_calendar_closes);
  return (
    <div data-testid={`exp-manifest-calendar-${row.instrument_id}`} style={{ fontSize: '12px' }}>
      <div>
        Calendar {display(calendar.market)}: {display(calendar.expected_known_open_sessions)} expected
        sessions it can judge, {display(calendar.sessions_the_calendar_cannot_judge)} it cannot
        (coverage {display(calendar.coverage_start)}–{display(calendar.coverage_end)}
        {calendar.reason ? `, ${calendar.reason}` : ''}).
      </div>
      <div data-testid={`exp-manifest-missing-sessions-${row.instrument_id}`}>
        Missing on known-open days: {display(calendar.missing_known_open_count)}
        {missing.total > 0 ? ` (${missing.shown.join(', ')}${missing.hidden > 0 ? `, +${missing.hidden} more` : ''})` : ''}
      </div>
      <div data-testid={`exp-manifest-bars-on-closed-${row.instrument_id}`}>
        Bars on days the calendar closes: {display(closed.total)}
        {closed.total > 0 ? ` (${closed.shown.join(', ')}${closed.hidden > 0 ? `, +${closed.hidden} more` : ''})` : ''}
      </div>
      <div style={{ color: 'var(--text-muted)' }}>{calendar.note}</div>
    </div>
  );
}

export default function DataManifestPanel({ spec, providerId, assetLabel }: Props) {
  const [manifest, setManifest] = useState<DataManifest | null>(null);
  const [checkedFingerprint, setCheckedFingerprint] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Which provider_id the next request is bound to. `null` is a real choice: it
  // is the state the server refuses to merge two providers in, so unbinding has
  // to be as reachable as binding.
  const [boundProvider, setBoundProvider] = useState<string | null>(providerId ?? null);
  // Two clicks can come back out of order; the older response must not replace
  // the newer verdict.
  const sequence = useRef(0);

  const fingerprint = useMemo(() => specFingerprint(spec, boundProvider), [spec, boundProvider]);
  const stale = isStale(checkedFingerprint, fingerprint);
  const statusCopy = manifest ? manifestStatusCopy(manifest.status) : null;
  const candidates = providerCandidates(manifest);

  const check = async (provider: string | null = boundProvider) => {
    if (loading) return;
    const token = ++sequence.current;
    setLoading(true);
    setError('');
    // The verdict is about this binding, so the binding is recorded even when
    // the request fails and no verdict survives.
    setBoundProvider(provider);
    const requestedFingerprint = specFingerprint(spec, provider);
    try {
      const data = (await api.strategyTemplates.dataManifest(spec, provider)) as DataManifest;
      if (token !== sequence.current) return;
      setManifest(data);
      setCheckedFingerprint(requestedFingerprint);
    } catch (err: any) {
      if (token !== sequence.current) return;
      setError(err?.message || 'The data manifest request failed.');
      setManifest(null);
      setCheckedFingerprint(null);
    } finally {
      if (token === sequence.current) setLoading(false);
    }
  };

  return (
    <section
      className="card"
      data-testid="exp-manifest-panel"
      data-status={manifest?.status ?? 'unchecked'}
      data-stale={stale ? 'true' : 'false'}
      style={{
        marginBottom: '16px',
        borderColor: statusCopy && !stale ? statusToneColour(statusCopy.tone) : undefined,
      }}
    >
      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '8px' }}>
        <h2 style={{ margin: 0, fontSize: '16px' }}>Data this run would read</h2>
        <button
          type="button"
          className="btn btn-secondary"
          data-testid="exp-manifest-check"
          onClick={() => check()}
          disabled={loading}
          style={{ marginLeft: 'auto' }}
        >
          {loading ? 'Checking…' : stale && manifest ? 'Re-check the data' : 'Check the data'}
        </button>
      </div>
      <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '0 0 10px' }}>
        Asks this server which stored bars a backtest built from the parameters above would price
        off, and what each data_source mode does with them. Read-only: it creates no experiment,
        version, run or reservation, contacts no provider, and does not authorize anything.
      </p>

      {error && (
        <p role="alert" data-testid="exp-manifest-error" style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--negative-text)' }}>
          {error}
        </p>
      )}

      {!manifest && !error && !loading && (
        <p data-testid="exp-manifest-unchecked" style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0 }}>
          Not checked{assetLabel ? ` for ${assetLabel}` : ''}. Creating the experiment does not run a
          backtest, so nothing here has failed — but no data verdict has been made either.
        </p>
      )}

      {manifest && statusCopy && (
        <>
          {stale && (
            <div
              className="alert alert-warning"
              data-testid="exp-manifest-stale"
              style={{ fontSize: '13px', marginBottom: '10px' }}
            >
              The parameters changed after this check, so the verdict below describes a different
              window or dataset. Re-check before relying on it.
            </div>
          )}

          <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', marginBottom: '6px', flexWrap: 'wrap' }}>
            <strong data-testid="exp-manifest-status" style={{ color: statusToneColour(statusCopy.tone) }}>
              {statusCopy.label}
            </strong>
            <code style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              {display(manifest.template_type)}
            </code>
            <span data-testid="exp-manifest-window" style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              {display(manifest.window?.start_date)} → {display(manifest.window?.end_date)}
              {manifest.provider_id_bound ? ` · provider ${manifest.provider_id_bound}` : ' · provider unbound'}
            </span>
          </div>
          <p data-testid="exp-manifest-status-detail" style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            {statusCopy.detail}
          </p>

          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
                <th style={{ padding: '4px 8px 4px 0' }}>Instrument / role</th>
                <th style={{ padding: '4px 8px 4px 0' }}>Stored rows</th>
                <th style={{ padding: '4px 8px 4px 0' }}>Grain</th>
                <th style={{ padding: '4px 8px 4px 0' }}>Provenance</th>
                <th style={{ padding: '4px 0 4px 0' }}>Calendar</th>
              </tr>
            </thead>
            <tbody>
              {manifest.instruments.map((row, index) => {
                const duplicates = previewDates(row.duplicate_dates);
                return (
                  <tr
                    key={`${row.instrument_id}-${index}`}
                    data-testid={`exp-manifest-row-${index}`}
                    data-status={row.status}
                    style={{ borderTop: '1px solid var(--border-strong)', verticalAlign: 'top' }}
                  >
                    <td style={{ padding: '6px 8px 6px 0' }}>
                      <div data-testid={`exp-manifest-row-${index}-id`}>
                        <code>{row.instrument_id}</code>
                      </div>
                      <div style={{ color: 'var(--text-muted)' }}>
                        {row.role} — {roleLegend(row.role)}
                      </div>
                      <div>status: {display(row.status)}</div>
                    </td>
                    <td style={{ padding: '6px 8px 6px 0' }}>
                      <div data-testid={`exp-manifest-row-${index}-bars`}>
                        {display(row.bar_count)} bars
                      </div>
                      <div>
                        {display(row.first_bar_date)} → {display(row.last_bar_date)}
                      </div>
                      {duplicates.total > 0 && (
                        <div data-testid={`exp-manifest-row-${index}-duplicates`} style={{ color: 'var(--warning)' }}>
                          {duplicates.total} dates carrying more than one row ({duplicates.shown.join(', ')}
                          {duplicates.hidden > 0 ? `, +${duplicates.hidden} more` : ''})
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '6px 8px 6px 0' }}>
                      <GrainCell row={row} />
                    </td>
                    <td style={{ padding: '6px 8px 6px 0' }}>
                      <div data-testid={`exp-manifest-row-${index}-nature`}>
                        data_nature: {display(row.data_nature)}
                      </div>
                      <div>
                        provider: {display(row.provider_id)}
                        {row.provider_ids.length > 1 ? ` (all: ${row.provider_ids.join(', ')})` : ''}
                      </div>
                      <div>source: {display(row.source)}</div>
                    </td>
                    <td style={{ padding: '6px 0' }}>
                      <CalendarCell row={row} />
                    </td>
                  </tr>
                );
              })}
              {manifest.instruments.length === 0 && (
                <tr data-testid="exp-manifest-row-none">
                  <td colSpan={5} style={{ padding: '6px 0', color: 'var(--text-muted)' }}>
                    No instruments were resolved — the spec did not pass the contract gate, so no
                    dataset was read.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <div data-testid="exp-manifest-checksum" style={{ fontSize: '12px', margin: '10px 0' }}>
            <div>
              dataset_checksum ({display(manifest.bars_loaded)} bars loaded):{' '}
              <code style={{ wordBreak: 'break-all' }}>{displayChecksum(manifest.dataset_checksum)}</code>
            </div>
            <div style={{ color: 'var(--text-muted)' }}>{manifest.checksum_basis}</div>
          </div>

          <IssueList issues={manifest.blockers} kind="blocker" />

          {candidates.length > 0 && (
            <div
              data-testid="exp-manifest-provider-bind"
              style={{ margin: '0 0 10px', fontSize: '12px' }}
            >
              <div style={{ color: 'var(--text-secondary)', marginBottom: '6px' }}>
                Re-check bound to one provider. The values below are the ones this response listed
                on the rows it refused to merge; binding is this account&apos;s choice of which
                provider&apos;s rows to read, not a merge the server performs.
              </div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {candidates.map((candidate) => (
                  <button
                    key={candidate}
                    type="button"
                    className="btn btn-secondary"
                    data-testid={`exp-manifest-bind-${candidate}`}
                    onClick={() => check(candidate)}
                    disabled={loading}
                    style={{ fontSize: '12px', padding: '4px 10px' }}
                  >
                    {candidate}
                  </button>
                ))}
                <button
                  type="button"
                  className="btn btn-secondary"
                  data-testid="exp-manifest-bind-none"
                  onClick={() => check(null)}
                  disabled={loading}
                  style={{ fontSize: '12px', padding: '4px 10px' }}
                >
                  no provider
                </button>
              </div>
            </div>
          )}

          <IssueList issues={manifest.warnings} kind="warning" />

          <details style={{ marginBottom: '10px' }} data-testid="exp-manifest-run-behavior">
            <summary style={{ fontSize: '13px', cursor: 'pointer' }}>
              What each data_source mode does with exactly this dataset
            </summary>
            <ul style={{ margin: '8px 0 0', paddingLeft: '18px', fontSize: '13px' }}>
              {Object.entries(manifest.run_behavior ?? {}).map(([mode, behaviour], index) => (
                <li key={mode} data-testid={`exp-manifest-behavior-${index}`}>
                  <code style={{ fontSize: '12px' }}>{mode}</code>: {behaviour}
                </li>
              ))}
            </ul>
          </details>

          <p data-testid="exp-manifest-scope-note" style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 8px' }}>
            {manifest.scope_note}
          </p>

          <details data-testid="exp-manifest-cannot-prove" style={{ fontSize: '12px' }}>
            <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>
              What this manifest cannot prove
            </summary>
            <ul style={{ margin: '8px 0 0', paddingLeft: '18px', color: 'var(--text-secondary)' }}>
              {manifest.cannot_prove.map((line, index) => (
                <li key={index} data-testid={`exp-manifest-cannot-prove-${index}`}>
                  {line}
                </li>
              ))}
            </ul>
          </details>

          <details data-testid="exp-manifest-engine-basis" style={{ fontSize: '12px' }}>
            <summary style={{ cursor: 'pointer', color: 'var(--text-muted)' }}>
              Which engine code this verdict was read from
            </summary>
            <ul style={{ margin: '8px 0 0', paddingLeft: '18px', color: 'var(--text-muted)' }}>
              {manifest.engine_basis.map((line, index) => (
                <li key={index}>
                  <code style={{ fontSize: '11px' }}>{line}</code>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}
