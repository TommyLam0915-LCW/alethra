'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';

interface RunDetail {
  id: string;
  experiment_version_id: string;
  dataset_snapshot_id?: string | null;
  dataset_checksum?: string | null;
  engine_version: string;
  config_hash: string;
  status: string;
  metrics?: Record<string, number | string | null> | null;
  warnings?: string[] | null;
  methodology_json?: Record<string, unknown> | null;
  result_json?: Record<string, unknown> | null;
  error_details?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

interface RunSummary {
  id: string;
}

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; heading: string; detail: string; backTo: 'experiment' | 'dashboard' }
  | { phase: 'ready'; run: RunDetail };

const METRIC_KEYS: [string, string][] = [
  ['total_return_pct', '總報酬 (%)'],
  ['max_drawdown_pct', '最大回撤 (%)'],
  ['total_trades', '交易次數'],
  ['sharpe_ratio', 'Sharpe 比率'],
  ['annualized_return_pct', '年化報酬 (%)'],
];

const PROVENANCE_KEYS: [string, string][] = [
  ['data_provider', '資料來源'],
  ['dataset_checksum', '資料集 checksum'],
  ['start_date', '期間開始'],
  ['end_date', '期間結束'],
];

const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number.isFinite(v) ? v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : String(v);
  return String(v);
};

function describeError(e: unknown, fallbackHeading: string): { heading: string; detail: string } {
  if (e instanceof ApiError) {
    if (e.status === 404) return { heading: '搵唔到對應記錄', detail: e.message };
    if (e.status === 403) return { heading: '冇權限檢視此記錄', detail: e.message };
    if (e.status === 401) return { heading: '登入狀態已失效', detail: e.message };
    return { heading: fallbackHeading, detail: `${e.status} — ${e.message}` };
  }
  return { heading: fallbackHeading, detail: e instanceof Error ? e.message : '未知錯誤' };
}

export default function RunDetailPage() {
  const params = useParams<{ id: string; runId: string }>();
  const experimentId = params?.id ?? '';
  const runId = params?.runId ?? '';
  const [state, setState] = useState<LoadState>({ phase: 'loading' });

  useEffect(() => {
    if (!runId || !experimentId) {
      setState({ phase: 'error', heading: '網址唔完整', detail: '缺少實驗 ID 或 run ID。', backTo: 'dashboard' });
      return;
    }
    let cancelled = false;
    (async () => {
      const [runRes, listRes] = await Promise.allSettled([
        api.runs.get(runId),
        api.runs.list(experimentId),
      ]);
      if (cancelled) return;

      if (runRes.status === 'rejected') {
        const { heading, detail } = describeError(runRes.reason, '載入執行記錄失敗');
        setState({ phase: 'error', heading, detail, backTo: 'dashboard' });
        return;
      }
      if (listRes.status === 'rejected') {
        const { heading, detail } = describeError(listRes.reason, '無法確認此 run 所屬嘅實驗');
        setState({ phase: 'error', heading, detail, backTo: 'dashboard' });
        return;
      }

      const run = runRes.value as RunDetail;
      const ownedRuns = (listRes.value as RunSummary[]) ?? [];
      if (!ownedRuns.some((r) => r.id === run.id)) {
        setState({
          phase: 'error',
          heading: '呢個 run 唔屬於網址所指嘅實驗',
          detail: '為避免展示唔對應嘅結果，本頁已停止渲染該 run 嘅指標。',
          backTo: 'dashboard',
        });
        return;
      }
      setState({ phase: 'ready', run });
    })();
    return () => {
      cancelled = true;
    };
  }, [experimentId, runId]);

  if (state.phase === 'loading') {
    return (
      <div className="dash-root" style={{ padding: '32px' }}>
        <p style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>載入中…</p>
      </div>
    );
  }

  if (state.phase === 'error') {
    const href = state.backTo === 'experiment' ? `/experiments/${experimentId}` : '/dashboard';
    const label = state.backTo === 'experiment' ? '← 返回實驗' : '← 返回控制台';
    return (
      <div className="dash-root" style={{ padding: '32px' }}>
        <Link href={href} style={{ color: 'var(--accent-blue)', textDecoration: 'none', fontSize: '13px' }}>
          {label}
        </Link>
        <div className="dash-disclaimer" data-testid="run-detail-error" style={{ marginTop: '16px' }}>
          <strong>{state.heading}：</strong>
          {state.detail}
        </div>
        <div style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
          Run ID：{runId || '—'}
        </div>
      </div>
    );
  }

  const run = state.run;
  const recorded = run.metrics ?? {};
  const resultMeta = (run.result_json ?? {}) as Record<string, unknown>;
  const extraMetricCount = Object.keys(recorded).filter((k) => !METRIC_KEYS.some(([m]) => m === k)).length;

  return (
    <div className="dash-root" data-testid="run-detail-page" style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <Link
          href={`/experiments/${experimentId}`}
          data-testid="run-detail-back"
          style={{ color: 'var(--accent-blue)', textDecoration: 'none', fontSize: '13px' }}
        >
          ← 返回實驗
        </Link>
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>狀態：{run.status}</span>
      </div>

      <h1 style={{ margin: '0 0 8px', fontSize: '24px', color: 'var(--text-white)' }}>執行結果</h1>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>執行身分</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            <div>Run ID</div><div style={{ fontFamily: 'monospace' }}>{run.id}</div>
            <div>實驗 ID</div><div style={{ fontFamily: 'monospace' }}>{experimentId}</div>
            <div>Version ID</div><div style={{ fontFamily: 'monospace' }}>{run.experiment_version_id}</div>
            <div>Config hash</div><div style={{ fontFamily: 'monospace' }}>{run.config_hash}</div>
            <div>Engine version</div><div style={{ fontFamily: 'monospace' }}>{run.engine_version}</div>
            <div>開始時間</div><div>{run.started_at ? new Date(run.started_at).toLocaleString('zh-Hant') : '—'}</div>
            <div>完成時間</div><div>{run.completed_at ? new Date(run.completed_at).toLocaleString('zh-Hant') : '—'}</div>
          </div>
        </div>
      </div>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>已記錄指標</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            {METRIC_KEYS.map(([k, label]) => (
              <div key={k} style={{ display: 'contents' }}>
                <div>{label}</div>
                <div style={{ fontWeight: 600, color: 'var(--text-white)' }}>{fmt(recorded[k])}</div>
              </div>
            ))}
          </div>
          {extraMetricCount > 0 && (
            <>
              <h4 style={{ margin: '16px 0 8px', fontSize: '13px', color: 'var(--text-muted)' }}>其他指標</h4>
              <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '6px 16px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                {Object.entries(recorded)
                  .filter(([k]) => !METRIC_KEYS.some(([m]) => m === k))
                  .map(([k, v]) => (
                    <div key={k} style={{ display: 'contents' }}>
                      <div style={{ fontFamily: 'monospace' }}>{k}</div>
                      <div>{fmt(v)}</div>
                    </div>
                  ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="dash-panel" style={{ marginBottom: '16px' }}>
        <div className="dash-panel-body" style={{ padding: '20px' }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>資料來源與快照</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: '8px 16px', fontSize: '13px', color: 'var(--text-secondary)' }}>
            {PROVENANCE_KEYS.map(([k, label]) => (
              <div key={k} style={{ display: 'contents' }}>
                <div>{label}</div>
                <div style={{ fontFamily: 'monospace' }}>{fmt(resultMeta[k])}</div>
              </div>
            ))}
            <div>Dataset snapshot</div>
            <div style={{ fontFamily: 'monospace' }}>{fmt(run.dataset_snapshot_id)}</div>
            <div>Dataset checksum (欄位)</div>
            <div style={{ fontFamily: 'monospace' }}>{fmt(run.dataset_checksum)}</div>
          </div>
        </div>
      </div>

      {run.error_details && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>錯誤詳情</h3>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
              {fmt(run.error_details)}
            </div>
          </div>
        </div>
      )}

      {Array.isArray(run.warnings) && run.warnings.length > 0 && (
        <div className="dash-panel" style={{ marginBottom: '16px' }}>
          <div className="dash-panel-body" style={{ padding: '20px' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>警告</h3>
            <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {run.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        </div>
      )}

      <div className="dash-disclaimer" style={{ marginTop: '24px', fontSize: '12px' }}>
        提醒：以上內容來自已記錄嘅 run，並已核實該 run 屬於此實驗嘅執行歷史。部分欄位可能缺失。本系統係研究工具，結果唔構成投資建議。
      </div>
    </div>
  );
}
