'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter, useParams, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';

interface EvidenceDetail {
  source_category?: string;
  record_id?: string;
  field_path?: string;
  derivation?: string;
}

interface ComparisonField {
  field: string;
  value_a: any;
  value_b: any;
  comparison_status: 'match' | 'differ' | 'insufficient';
  provenance_a: 'captured' | 'current' | 'absent' | 'unavailable' | 'derived';
  provenance_b: 'captured' | 'current' | 'absent' | 'unavailable' | 'derived';
  evidence_source?: string;
  evidence_detail?: { run_a?: EvidenceDetail; run_b?: EvidenceDetail };
}

interface PeriodOverlap {
  run_a_period: { start: string; end: string };
  run_b_period: { start: string; end: string };
  overlap: { start: string; end: string };
}

interface ComparisonData {
  run_a_id: string;
  run_b_id: string;
  experiment_id: string;
  experiment_version_a_id: string;
  experiment_version_b_id: string;
  status_a: string;
  status_b: string;
  metrics_comparison: ComparisonField[];
  conditions_comparison: ComparisonField[];
  limitations: string[];
  period_overlap?: PeriodOverlap;
}

const PROVENANCE_LABELS: Record<string, string> = {
  captured: '記錄',
  current: '當前 metadata',
  absent: '缺失',
  unavailable: '無法取得',
  derived: '衍生',
};

const PROVENANCE_COLORS: Record<string, string> = {
  captured: '#d4edda',
  current: '#fff3cd',
  absent: '#e2e3e5',
  unavailable: '#f8d7da',
  derived: '#d1ecf1',
};

export default function RunComparisonPage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const experimentId = params.id as string;
  const runA = searchParams.get('run_a');
  const runB = searchParams.get('run_b');
  const savedComparisonIdParam = searchParams.get('saved_comparison_id');

  const [comparison, setComparison] = useState<ComparisonData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set());
  
  // Save comparison state
  const [purpose, setPurpose] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [savedComparisonId, setSavedComparisonId] = useState<string | null>(null);
  // Guards against a slow response for a previously-opened record overwriting
  // the currently-selected record (stale write / data leak across switches).
  const activeSavedIdRef = useRef<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }

    // If loading a saved comparison, fetch it first
    if (savedComparisonIdParam) {
      activeSavedIdRef.current = savedComparisonIdParam;
      loadSavedComparison(savedComparisonIdParam);
      return;
    }

    // Fresh comparison (not a saved record): ensure we never PATCH a stale id.
    activeSavedIdRef.current = null;
    setSavedComparisonId(null);
    setPurpose('');
    setNotes('');
    setSaveSuccess(false);

    if (!runA || !runB) {
      setError('缺少 run_a 或 run_b 參數');
      setErrorStatus(400);
      setLoading(false);
      return;
    }
    if (runA === runB) {
      setError('不能比較同一個 run');
      setErrorStatus(400);
      setLoading(false);
      return;
    }
    loadComparison();
  }, [experimentId, runA, runB, savedComparisonIdParam, router]);

  const loadSavedComparison = async (comparisonId: string) => {
    setLoading(true);
    setError('');
    setErrorStatus(null);
    try {
      const saved = await api.savedComparisons.get(comparisonId);
      // Discard if the user switched records while this request was in flight.
      if (activeSavedIdRef.current !== comparisonId) return;
      setSavedComparisonId(saved.id);
      setPurpose(saved.purpose);
      setNotes(saved.notes || '');
      // Now load the actual comparison data using the saved run IDs
      const data = await api.runs.compare(saved.run_a_id, saved.run_b_id);
      if (activeSavedIdRef.current !== comparisonId) return;
      setComparison(data);
    } catch (err: any) {
      if (activeSavedIdRef.current !== comparisonId) return;
      if (err instanceof ApiError) {
        setErrorStatus(err.status);
        setError(err.status === 404 ? '找不到已儲存嘅比較或無權限查看' : err.message);
      } else {
        setError('載入已儲存比較失敗');
      }
    } finally {
      if (activeSavedIdRef.current === comparisonId) setLoading(false);
    }
  };

  const loadComparison = async () => {
    setLoading(true);
    setError('');
    setErrorStatus(null);
    try {
      const data = await api.runs.compare(runA!, runB!);
      setComparison(data);
    } catch (err: any) {
      if (err instanceof ApiError) {
        setErrorStatus(err.status);
        if (err.status === 404) {
          setError('找不到 run 或無權限查看');
        } else if (err.status === 400) {
          setError(err.message);
        } else {
          setError(err.message);
        }
      } else {
        setError('載入比較失敗');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!purpose.trim()) {
      setSaveError('請填寫比較目的');
      return;
    }
    // Creating requires the two runs from the URL; updating a reopened record
    // does not (its run references are already fixed server-side).
    if (!savedComparisonId && (!runA || !runB)) {
      setSaveError('缺少 run_a 或 run_b，無法建立新比較');
      return;
    }

    setSaving(true);
    setSaveError('');
    setSaveSuccess(false);

    try {
      // Empty notes field → send null so the stored note is cleared (omitting
      // it would keep the previous value server-side).
      const notesValue = notes.trim() ? notes.trim() : null;
      if (savedComparisonId) {
        // Update the SAME record (PATCH), never a duplicate create.
        await api.savedComparisons.update(savedComparisonId, {
          purpose: purpose.trim(),
          notes: notesValue,
        });
        setSaveSuccess(true);
      } else {
        // Create new saved comparison and retain the returned identity so a
        // second save PATCHes this record instead of POSTing a duplicate.
        const saved = await api.savedComparisons.create({
          experiment_id: experimentId,
          run_a_id: runA!,
          run_b_id: runB!,
          purpose: purpose.trim(),
          notes: notesValue,
        });
        setSavedComparisonId(saved.id);
        setSaveSuccess(true);
      }
    } catch (err: any) {
      // Leave purpose/notes state untouched so the user's text survives a failed save.
      setSaveError(err.message || '儲存失敗');
    } finally {
      setSaving(false);
    }
  };

  const toggleFieldExpand = (fieldKey: string) => {
    setExpandedFields(prev => {
      const next = new Set(prev);
      if (next.has(fieldKey)) {
        next.delete(fieldKey);
      } else {
        next.add(fieldKey);
      }
      return next;
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'match': return '#d4edda';
      case 'differ': return '#fff3cd';
      case 'insufficient': return '#e2e3e5';
      default: return '#f5f5f5';
    }
  };

  const getProvenanceBadge = (provenance: string) => {
    const color = PROVENANCE_COLORS[provenance] || '#e2e3e5';
    return (
      <span style={{
        display: 'inline-block',
        padding: '2px 6px',
        backgroundColor: color,
        borderRadius: '3px',
        fontSize: '11px',
        marginLeft: '4px',
      }}>
        {PROVENANCE_LABELS[provenance] || provenance}
      </span>
    );
  };

  const formatValue = (value: any) => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'number') {
      if (value % 1 === 0) return value.toLocaleString('en-US');
      return value.toFixed(4);
    }
    if (Array.isArray(value)) return value.join(', ');
    return String(value);
  };

  const formatDelta = (field: ComparisonField) => {
    if (field.comparison_status !== 'differ') return null;
    if (typeof field.value_a === 'number' && typeof field.value_b === 'number') {
      const delta = field.value_b - field.value_a;
      const isPercent = field.field.includes('return') || field.field.includes('drawdown');
      const prefix = delta > 0 ? '+' : '';
      const suffix = isPercent ? ' 百分點' : '';
      return (
        <span style={{ color: delta < 0 ? '#dc3545' : '#28a745', fontWeight: 600 }}>
          {prefix}{delta.toFixed(2)}{suffix}
        </span>
      );
    }
    return <span style={{ color: '#dc3545' }}>Values differ</span>;
  };

  const renderEvidenceDetail = (evidence: EvidenceDetail | undefined, label: string) => {
    if (!evidence) return <span style={{ color: '#999' }}>—</span>;
    return (
      <div style={{ marginBottom: '4px' }}>
        <strong>{label}:</strong>
        {evidence.source_category && <span> {evidence.source_category}</span>}
        {evidence.field_path && <span> · <code style={{ fontSize: '11px' }}>{evidence.field_path}</code></span>}
        {evidence.derivation && <div style={{ color: '#666', fontSize: '12px', marginTop: '2px' }}>{evidence.derivation}</div>}
      </div>
    );
  };

  if (loading) {
    return <div className="container" style={{ paddingTop: '40px' }}>載入比較中...</div>;
  }

  if (error) {
    const isRetryable = errorStatus !== 400;
    return (
      <div className="container" style={{ paddingTop: '40px' }}>
        <div style={{
          background: errorStatus === 404 ? '#fff3cd' : '#f8d7da',
          borderLeft: `4px solid ${errorStatus === 404 ? '#ffc107' : '#dc3545'}`,
          padding: '16px',
          marginBottom: '16px',
          borderRadius: '4px',
        }}>
          <strong>{errorStatus === 404 ? '找不到資料' : errorStatus === 400 ? '參數錯誤' : '載入失敗'}</strong>
          <p style={{ margin: '8px 0 0 0' }}>{error}</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          {isRetryable && (
            <button onClick={loadComparison} className="btn" style={{ backgroundColor: '#0070f3', color: '#fff' }}>
              重試
            </button>
          )}
          <Link href={`/experiments/${experimentId}`} className="btn" style={{ backgroundColor: '#eaeaea', textDecoration: 'none' }}>
            返回實驗
          </Link>
        </div>
      </div>
    );
  }

  if (!comparison) {
    return <div className="container" style={{ paddingTop: '40px' }}>無比較資料</div>;
  }

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ marginBottom: '20px' }}>
        <Link href={`/experiments/${experimentId}`} style={{ color: '#0070f3', textDecoration: 'none' }}>
          ← 返回實驗
        </Link>
      </div>

      <h1 style={{ marginBottom: '8px' }}>回測結果比較</h1>
      <p style={{ color: '#666', marginBottom: '24px' }}>
        實驗 {experimentId.slice(0, 8)}...
      </p>

      {/* Task Prompt */}
      <div style={{
        background: '#e7f3ff',
        border: '2px solid #007bff',
        padding: '15px',
        marginBottom: '24px',
        borderRadius: '4px',
      }}>
        <strong>任務：</strong>你要決定下一步調查邊個設定。請用呢個畫面檢查兩個結果，講出你目前可以得出甚麼結論，以及下一步會做乜。
      </div>

      {/* Save Comparison Form */}
      <div style={{
        background: '#f8f9fa',
        border: '1px solid #dee2e6',
        padding: '16px',
        marginBottom: '24px',
        borderRadius: '4px',
      }}>
        <h3 style={{ marginTop: 0, marginBottom: '16px' }}>儲存比較</h3>
        
        <div style={{ marginBottom: '12px' }}>
          <label style={{ display: 'block', marginBottom: '4px', fontWeight: 500 }}>
            比較目的 <span style={{ color: '#dc3545' }}>*</span>
          </label>
          <input
            type="text"
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            placeholder="例如：檢查佣金率對回報嘅影響"
            maxLength={500}
            style={{
              width: '100%',
              padding: '8px',
              border: '1px solid #ced4da',
              borderRadius: '4px',
              fontSize: '14px',
            }}
            disabled={saving}
          />
        </div>

        <div style={{ marginBottom: '12px' }}>
          <label style={{ display: 'block', marginBottom: '4px', fontWeight: 500 }}>
            筆記
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="記錄你嘅觀察同結論..."
            maxLength={5000}
            rows={4}
            style={{
              width: '100%',
              padding: '8px',
              border: '1px solid #ced4da',
              borderRadius: '4px',
              fontSize: '14px',
              resize: 'vertical',
            }}
            disabled={saving}
          />
        </div>

        {saveError && (
          <div style={{
            background: '#f8d7da',
            border: '1px solid #f5c6cb',
            color: '#721c24',
            padding: '8px',
            marginBottom: '12px',
            borderRadius: '4px',
          }}>
            {saveError}
          </div>
        )}

        {saveSuccess && (
          <div style={{
            background: '#d4edda',
            border: '1px solid #c3e6cb',
            color: '#155724',
            padding: '8px',
            marginBottom: '12px',
            borderRadius: '4px',
          }}>
            比較已儲存
          </div>
        )}

        <button
          onClick={handleSave}
          disabled={saving || !purpose.trim()}
          style={{
            backgroundColor: saving ? '#6c757d' : '#0070f3',
            color: '#fff',
            padding: '8px 16px',
            border: 'none',
            borderRadius: '4px',
            cursor: saving ? 'not-allowed' : 'pointer',
            fontSize: '14px',
            fontWeight: 500,
          }}
        >
          {saving ? '儲存中...' : savedComparisonId ? '更新' : '儲存'}
        </button>
      </div>

      {/* Run Identities */}
      <div style={{
        background: '#d1ecf1',
        borderLeft: '4px solid #17a2b8',
        padding: '12px',
        marginBottom: '24px',
      }}>
        <strong>選取嘅結果：</strong>
        <ul style={{ margin: '8px 0 0 20px', listStyle: 'none', padding: 0 }}>
          <li style={{ marginBottom: '4px' }}>
            Run A: <Link href={`/experiments/${experimentId}/runs/${comparison.run_a_id}`} style={{ color: '#0070f3', textDecoration: 'none' }}>
              {comparison.run_a_id.slice(0, 8)}...
            </Link> (狀態: {comparison.status_a})
          </li>
          <li>
            Run B: <Link href={`/experiments/${experimentId}/runs/${comparison.run_b_id}`} style={{ color: '#0070f3', textDecoration: 'none' }}>
              {comparison.run_b_id.slice(0, 8)}...
            </Link> (狀態: {comparison.status_b})
          </li>
        </ul>
      </div>

      {/* Limitations */}
      {comparison.limitations.length > 0 && (
        <div style={{
          background: '#fff3cd',
          borderLeft: '4px solid #ffc107',
          padding: '12px',
          marginBottom: '24px',
        }}>
          <strong>比較限制：</strong>
          <ul style={{ margin: '8px 0 0 20px' }}>
            {comparison.limitations.map((lim, i) => (
              <li key={i}>{lim}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Period Overlap */}
      {comparison.period_overlap && (
        <div style={{
          background: '#f8f9fa',
          border: '1px solid #dee2e6',
          padding: '12px',
          marginBottom: '24px',
          borderRadius: '4px',
        }}>
          <strong>期間重疊：</strong>
          <div style={{ marginTop: '8px', fontSize: '13px' }}>
            <div>Run A: {comparison.period_overlap.run_a_period.start} 至 {comparison.period_overlap.run_a_period.end}</div>
            <div>Run B: {comparison.period_overlap.run_b_period.start} 至 {comparison.period_overlap.run_b_period.end}</div>
            <div style={{ marginTop: '4px', color: '#007bff' }}>
              重疊期間: {comparison.period_overlap.overlap.start} 至 {comparison.period_overlap.overlap.end}
            </div>
          </div>
        </div>
      )}

      {/* Metrics Comparison */}
      <h2 style={{ marginBottom: '16px' }}>結果差異</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '24px' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #ddd' }}>
            <th style={{ padding: '10px', textAlign: 'left', width: '30px' }}></th>
            <th style={{ padding: '10px', textAlign: 'left' }}>指標</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run A</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run B</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>差異 (B − A)</th>
            <th style={{ padding: '10px', textAlign: 'left' }}>來源狀態</th>
          </tr>
        </thead>
        <tbody>
          {comparison.metrics_comparison.map((field, i) => {
            const fieldKey = `metrics-${field.field}`;
            const isExpanded = expandedFields.has(fieldKey);
            const hasEvidence = field.evidence_detail?.run_a || field.evidence_detail?.run_b;
            return (
              <tr key={i}>
                <td colSpan={6} style={{ padding: 0, borderBottom: isExpanded ? 'none' : '1px solid #eee' }}>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '30px 1fr 1fr 1fr 1fr 1fr',
                      backgroundColor: getStatusColor(field.comparison_status),
                      cursor: hasEvidence ? 'pointer' : 'default',
                    }}
                    onClick={() => hasEvidence && toggleFieldExpand(fieldKey)}
                  >
                    <div style={{ padding: '10px', fontSize: '12px', color: '#666' }}>
                      {hasEvidence ? (isExpanded ? '▼' : '▶') : ''}
                    </div>
                    <div style={{ padding: '10px', fontWeight: 500 }}>{field.field}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.value_a)}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.value_b)}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatDelta(field)}</div>
                    <div style={{ padding: '10px' }}>
                      {getProvenanceBadge(field.provenance_a)}
                      {field.provenance_a !== field.provenance_b && getProvenanceBadge(field.provenance_b)}
                    </div>
                  </div>
                  {isExpanded && hasEvidence && (
                    <div style={{ padding: '8px 10px 12px 40px', background: '#f8f9fa', fontSize: '13px', borderBottom: '1px solid #eee' }}>
                      {renderEvidenceDetail(field.evidence_detail?.run_a, 'Run A')}
                      {renderEvidenceDetail(field.evidence_detail?.run_b, 'Run B')}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Conditions Comparison */}
      <h2 style={{ marginBottom: '16px' }}>執行條件差異</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: '24px' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #ddd' }}>
            <th style={{ padding: '10px', textAlign: 'left', width: '30px' }}></th>
            <th style={{ padding: '10px', textAlign: 'left' }}>欄位</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run A</th>
            <th style={{ padding: '10px', textAlign: 'right' }}>Run B</th>
            <th style={{ padding: '10px', textAlign: 'left' }}>比較狀態</th>
            <th style={{ padding: '10px', textAlign: 'left' }}>來源狀態</th>
          </tr>
        </thead>
        <tbody>
          {comparison.conditions_comparison.map((field, i) => {
            const fieldKey = `conditions-${field.field}`;
            const isExpanded = expandedFields.has(fieldKey);
            const hasEvidence = field.evidence_detail?.run_a || field.evidence_detail?.run_b;
            return (
              <tr key={i}>
                <td colSpan={6} style={{ padding: 0, borderBottom: isExpanded ? 'none' : '1px solid #eee' }}>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '30px 1fr 1fr 1fr 1fr 1fr',
                      backgroundColor: getStatusColor(field.comparison_status),
                      cursor: hasEvidence ? 'pointer' : 'default',
                    }}
                    onClick={() => hasEvidence && toggleFieldExpand(fieldKey)}
                  >
                    <div style={{ padding: '10px', fontSize: '12px', color: '#666' }}>
                      {hasEvidence ? (isExpanded ? '▼' : '▶') : ''}
                    </div>
                    <div style={{ padding: '10px', fontWeight: 500 }}>{field.field}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.value_a)}</div>
                    <div style={{ padding: '10px', textAlign: 'right' }}>{formatValue(field.value_b)}</div>
                    <div style={{ padding: '10px' }}>
                      {field.comparison_status === 'match' && 'Values match'}
                      {field.comparison_status === 'differ' && 'Values differ'}
                      {field.comparison_status === 'insufficient' && 'Insufficient information'}
                    </div>
                    <div style={{ padding: '10px' }}>
                      {getProvenanceBadge(field.provenance_a)}
                      {field.provenance_a !== field.provenance_b && getProvenanceBadge(field.provenance_b)}
                    </div>
                  </div>
                  {isExpanded && hasEvidence && (
                    <div style={{ padding: '8px 10px 12px 40px', background: '#f8f9fa', fontSize: '13px', borderBottom: '1px solid #eee' }}>
                      {renderEvidenceDetail(field.evidence_detail?.run_a, 'Run A')}
                      {renderEvidenceDetail(field.evidence_detail?.run_b, 'Run B')}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* Disclaimer */}
      <div style={{
        background: '#fff3cd',
        padding: '12px',
        borderRadius: '4px',
        fontSize: '14px',
      }}>
        <strong>提醒：</strong>以上比較基於已記錄嘅資料。部分欄位可能缺失或僅反映當前 metadata。本系統係研究工具，比較結果唔構成投資建議。
      </div>
    </div>
  );
}
