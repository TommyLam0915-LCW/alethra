'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';

interface SchedulerStatus {
  enabled: boolean;
  running: boolean;
  interval_hours: number;
  next_run: string | null;
}

interface CollectionRun {
  id: string;
  instrument_id: string;
  interval_minutes: number;
  trade_count: number;
  created_at: string | null;
}

export default function AutoCollectionStatus() {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [history, setHistory] = useState<CollectionRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = (await api.admin.collectionStatus()) as SchedulerStatus;
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = (await api.admin.collectionHistory(20)) as CollectionRun[];
      setHistory(data);
    } catch {
      // Non-critical
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadHistory();
  }, [loadStatus, loadHistory]);

  const handleStart = async () => {
    setActionLoading('start');
    try {
      await api.admin.collectionStart();
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(null);
    }
  };

  const handleStop = async () => {
    setActionLoading('stop');
    try {
      await api.admin.collectionStop();
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(null);
    }
  };

  const handleTrigger = async () => {
    setActionLoading('trigger');
    try {
      const result = (await api.admin.collectionTrigger()) as {
        runs: number;
        trades: number;
        errors: string[];
        completed_at: string;
      };
      alert(`Collection complete: ${result.runs} runs, ${result.trades} trades`);
      await loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading...
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {error && (
        <div role="alert" style={{
          border: '1px solid var(--negative)',
          backgroundColor: 'rgba(226,99,110,0.1)',
          borderRadius: 6,
          padding: 12,
          fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* Scheduler Status Card */}
      <div style={{
        padding: '1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16, margin: '0 0 16px 0' }}>
          Scheduler Status
        </h3>
        
        {status && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Status</span>
              <span style={{
                fontSize: 14,
                fontWeight: 600,
                color: status.running ? 'var(--positive, #36C7A0)' : 'var(--text-primary)',
                padding: '4px 12px',
                backgroundColor: status.running ? 'rgba(54, 199, 160, 0.1)' : 'var(--surface-2)',
                borderRadius: 4,
              }}>
                {status.running ? 'Running' : 'Stopped'}
              </span>
            </div>
            
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Interval</span>
              <span style={{ fontSize: 14, fontWeight: 500 }}>
                Every {status.interval_hours} hours
              </span>
            </div>
            
            {status.next_run && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Next Run</span>
                <span style={{ fontSize: 14, fontWeight: 500 }}>
                  {new Date(status.next_run).toLocaleString()}
                </span>
              </div>
            )}
            
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              {!status.running ? (
                <button
                  onClick={() => void handleStart()}
                  disabled={actionLoading === 'start'}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--primary)',
                    color: 'white',
                    border: 'none',
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: actionLoading === 'start' ? 'not-allowed' : 'pointer',
                    opacity: actionLoading === 'start' ? 0.6 : 1,
                  }}
                >
                  {actionLoading === 'start' ? 'Starting...' : 'Start Scheduler'}
                </button>
              ) : (
                <button
                  onClick={() => void handleStop()}
                  disabled={actionLoading === 'stop'}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: 'var(--negative, #E2636E)',
                    color: 'white',
                    border: 'none',
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 500,
                    cursor: actionLoading === 'stop' ? 'not-allowed' : 'pointer',
                    opacity: actionLoading === 'stop' ? 0.6 : 1,
                  }}
                >
                  {actionLoading === 'stop' ? 'Stopping...' : 'Stop Scheduler'}
                </button>
              )}
              
              <button
                onClick={() => void handleTrigger()}
                disabled={actionLoading === 'trigger'}
                style={{
                  padding: '8px 16px',
                  backgroundColor: 'var(--surface-2)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: actionLoading === 'trigger' ? 'not-allowed' : 'pointer',
                  opacity: actionLoading === 'trigger' ? 0.6 : 1,
                }}
              >
                {actionLoading === 'trigger' ? 'Running...' : 'Trigger Manual Collection'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Collection History */}
      <div style={{
        padding: '1.5rem',
        backgroundColor: 'var(--surface-1)',
        borderRadius: 8,
        border: '1px solid var(--border-subtle)',
      }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16, margin: '0 0 16px 0' }}>
          Recent Collection Runs
        </h3>
        
        {history.length === 0 ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
            No collection runs yet. Click "Trigger Manual Collection" to start.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Time</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Instrument</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Interval</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Trades</th>
                </tr>
              </thead>
              <tbody>
                {history.map((run) => (
                  <tr key={run.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '8px 12px', color: 'var(--text-primary)' }}>
                      {run.created_at ? new Date(run.created_at).toLocaleString() : 'N/A'}
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-primary)' }}>
                      {run.instrument_id}
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-primary)' }}>
                      {run.interval_minutes}m
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--text-primary)', fontWeight: 500 }}>
                      {run.trade_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
