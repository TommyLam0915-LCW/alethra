'use client';

// The 03:00-UTC replay step, described from the server's own answer.
//
// The daily service changes this replay while the owner is not looking, so the
// card answers the four questions the worker itself uses — am I in it, on what
// clock, why not, and what happened last — and never paraphrases them into a
// promise. It reads one route and derives everything else from
// src/lib/dailyService.ts.

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import {
  DailyService,
  formatTime,
  lastRunLabel,
  serviceState,
  serviceView,
} from '@/lib/dailyService';

const TONE_COLOUR: Record<string, string> = {
  ok: 'var(--positive)',
  muted: 'var(--text-secondary)',
  warn: 'var(--warning)',
  bad: 'var(--negative)',
};

interface Props {
  sessionId: string;
  // Something about the replay changed (a step, a pause, a resume), so the
  // service's answer about it is no longer the one on screen.
  changedAt?: string | number;
}

export default function DailyServicePanel({ sessionId, changedAt }: Props) {
  const [service, setService] = useState<DailyService | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    setState('loading');
    setMessage('');
    try {
      setService((await api.simulations.dailyService(sessionId)) as DailyService);
      setState('ready');
    } catch (err) {
      setService(null);
      setMessage(err instanceof Error ? err.message : String(err));
      setState('failed');
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load, changedAt]);

  const wrapper = {
    marginBottom: '24px',
    padding: '16px',
    borderLeft: '4px solid var(--border-strong)',
  };

  if (state === 'loading') {
    return (
      <div className="card" style={wrapper} data-testid="sim-daily-service" data-service-state="loading">
        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
          Checking the daily replay service…
        </div>
      </div>
    );
  }

  if (state === 'failed' || service === null) {
    return (
      <div
        className="card"
        style={{ ...wrapper, borderLeftColor: 'var(--negative)' }}
        data-testid="sim-daily-service"
        data-service-state="unreachable"
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
          <div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              Daily replay service
            </div>
            <div style={{ fontWeight: 700 }}>Its status could not be read</div>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px' }}>
              {message || 'No reason returned.'} Nothing on this page claims the
              service is running while this fails.
            </div>
          </div>
          <button className="btn" style={{ alignSelf: 'flex-start' }} onClick={load}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  const view = serviceView(service);

  return (
    <div
      className="card"
      style={{ ...wrapper, borderLeftColor: TONE_COLOUR[view.tone] }}
      data-testid="sim-daily-service"
      data-service-state={serviceState(service)}
    >
      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
        Daily replay service
      </div>
      <div style={{ fontWeight: 700, color: TONE_COLOUR[view.tone] }}>
        {view.title}
      </div>
      <div style={{ fontSize: '13px', marginTop: '4px', lineHeight: 1.6 }}>
        {view.reason}
      </div>
      <div style={{ fontSize: '13px', marginTop: '8px' }}>
        {lastRunLabel(service)}
      </div>
      <div
        style={{
          fontSize: '13px',
          marginTop: '8px',
          color: 'var(--text-secondary)',
        }}
      >
        {view.control}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: '12px',
          marginTop: '12px',
          fontSize: '12px',
          color: 'var(--text-secondary)',
        }}
      >
        <div>
          <div>Cadence</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            {String(service.cadence_hour_utc).padStart(2, '0')}:00 {service.cadence_timezone}
          </div>
        </div>
        <div>
          <div>Next step</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            {service.next_run_at ? formatTime(service.next_run_at) : 'Not in this process'}
          </div>
        </div>
        <div>
          <div>Serves</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            {service.statuses_served.join(', ') || 'nothing'}
          </div>
        </div>
        <div>
          <div>Per account per day</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            {service.queue_position === null
              ? `${service.batch_limit} (this replay is not queued)`
              : `${service.batch_limit} (this replay is #${service.queue_position + 1})`}
          </div>
        </div>
        <div>
          <div>Last recorded step</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            {service.last_run
              ? `${service.last_run.job_status}${
                  service.last_run.scheduled_for ? ` · ${service.last_run.scheduled_for}` : ''
                }`
              : 'No tick has named this replay'}
          </div>
        </div>
        <div>
          <div>Stop / start</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            Pause and Resume below
          </div>
        </div>
      </div>
    </div>
  );
}
