'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

type TabKey = 'ai' | 'workspace' | 'usage';

interface AIStatus {
  provider: string;
  model: string;
  mode: string;
  api_key_configured: boolean;
  base_url: string | null;
  timeout_seconds: number;
  max_output_tokens: number;
  advisory_notice: string;
}

interface WorkspacePreferences {
  default_symbol?: string;
  default_range?: string;
  panel_defaults?: {
    left_collapsed?: boolean;
    bottom_collapsed?: boolean;
    right_collapsed?: boolean;
  };
}

interface UsageSummary {
  total_api_calls: number;
  by_endpoint: Record<string, number>;
}

export default function SettingsPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>('ai');

  // AI Settings state
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [aiLoading, setAiLoading] = useState(true);
  const [aiError, setAiError] = useState<string | null>(null);
  const [testConnectionLoading, setTestConnectionLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; detail?: string } | null>(null);

  // Workspace preferences state
  const [workspacePrefs, setWorkspacePrefs] = useState<WorkspacePreferences | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(true);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceSaving, setWorkspaceSaving] = useState(false);
  const [workspaceSaved, setWorkspaceSaved] = useState(false);
  const [defaultSymbol, setDefaultSymbol] = useState('');
  const [defaultRange, setDefaultRange] = useState('6M');

  // Usage state
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [usageLoading, setUsageLoading] = useState(true);
  const [usageError, setUsageError] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    setAuthed(true);
  }, [router]);

  useEffect(() => {
    if (!authed) return;
    loadAIStatus();
    loadWorkspacePreferences();
    loadUsage();
  }, [authed]);

  const loadAIStatus = async () => {
    try {
      setAiLoading(true);
      setAiError(null);
      const status = await api.aiAssistant.status();
      setAiStatus(status);
    } catch (err: any) {
      setAiError(err.message || '無法載入 AI 設定');
    } finally {
      setAiLoading(false);
    }
  };

  const handleTestConnection = async () => {
    try {
      setTestConnectionLoading(true);
      setTestResult(null);
      const result = await api.aiAssistant.testConnection();
      setTestResult(result);
    } catch (err: any) {
      setTestResult({ ok: false, detail: err.message || '連線測試失敗' });
    } finally {
      setTestConnectionLoading(false);
    }
  };

  const loadWorkspacePreferences = async () => {
    try {
      setWorkspaceLoading(true);
      setWorkspaceError(null);
      const data = await api.layouts.getWorkspacePreferences();
      const prefs = data.preferences || {};
      setWorkspacePrefs(prefs);
      setDefaultSymbol(prefs.default_symbol || '');
      setDefaultRange(prefs.default_range || '6M');
    } catch (err: any) {
      setWorkspaceError(err.message || '無法載入工作台偏好');
    } finally {
      setWorkspaceLoading(false);
    }
  };

  const handleSaveWorkspacePreferences = async () => {
    try {
      setWorkspaceSaving(true);
      setWorkspaceSaved(false);
      setWorkspaceError(null);
      const prefs: WorkspacePreferences = {
        default_symbol: defaultSymbol.trim() || undefined,
        default_range: defaultRange,
      };
      await api.layouts.putWorkspacePreferences(prefs);
      setWorkspacePrefs(prefs);
      setWorkspaceSaved(true);
      setTimeout(() => setWorkspaceSaved(false), 2000);
    } catch (err: any) {
      setWorkspaceError(err.message || '儲存失敗');
    } finally {
      setWorkspaceSaving(false);
    }
  };

  const handleResetWorkspacePreferences = async () => {
    if (!confirm('確定要重設工作台偏好嗎？這不會影響 AI 設定或 API 金鑰。')) return;
    try {
      setWorkspaceSaving(true);
      setWorkspaceError(null);
      await api.layouts.putWorkspacePreferences({});
      setWorkspacePrefs({});
      setDefaultSymbol('');
      setDefaultRange('6M');
      setWorkspaceSaved(true);
      setTimeout(() => setWorkspaceSaved(false), 2000);
    } catch (err: any) {
      setWorkspaceError(err.message || '重設失敗');
    } finally {
      setWorkspaceSaving(false);
    }
  };

  const loadUsage = async () => {
    try {
      setUsageLoading(true);
      setUsageError(null);
      const data = await api.billing.getUsageSummary(30);
      setUsage(data.usage);
    } catch (err: any) {
      setUsageError(err.message || '無法載入用量資訊');
    } finally {
      setUsageLoading(false);
    }
  };

  if (!authed) {
    return (
      <div style={{ padding: 20, textAlign: 'center' }}>
        載入中…
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 20 }}>
      <div style={{ marginBottom: 20 }}>
        <Link href="/workspace" style={{ color: '#4a90e2', textDecoration: 'none' }}>
          ← 返回工作台
        </Link>
      </div>

      <h1 style={{ fontSize: 24, marginBottom: 20 }}>設定</h1>

      <div style={{ display: 'flex', gap: 10, marginBottom: 20, borderBottom: '1px solid #2a2e39' }}>
        <button
          onClick={() => setActiveTab('ai')}
          style={{
            padding: '10px 20px',
            background: activeTab === 'ai' ? '#4a90e2' : 'transparent',
            color: activeTab === 'ai' ? '#fff' : '#999',
            border: 'none',
            borderBottom: activeTab === 'ai' ? '2px solid #4a90e2' : '2px solid transparent',
            cursor: 'pointer',
            fontSize: 14,
          }}
        >
          AI 設定
        </button>
        <button
          onClick={() => setActiveTab('workspace')}
          style={{
            padding: '10px 20px',
            background: activeTab === 'workspace' ? '#4a90e2' : 'transparent',
            color: activeTab === 'workspace' ? '#fff' : '#999',
            border: 'none',
            borderBottom: activeTab === 'workspace' ? '2px solid #4a90e2' : '2px solid transparent',
            cursor: 'pointer',
            fontSize: 14,
          }}
        >
          工作台偏好
        </button>
        <button
          onClick={() => setActiveTab('usage')}
          style={{
            padding: '10px 20px',
            background: activeTab === 'usage' ? '#4a90e2' : 'transparent',
            color: activeTab === 'usage' ? '#fff' : '#999',
            border: 'none',
            borderBottom: activeTab === 'usage' ? '2px solid #4a90e2' : '2px solid transparent',
            cursor: 'pointer',
            fontSize: 14,
          }}
        >
          用量資訊
        </button>
      </div>

      {activeTab === 'ai' && (
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 15 }}>AI 設定</h2>
          {aiLoading && <div>載入中…</div>}
          {aiError && <div style={{ color: '#f44336', marginBottom: 15 }}>{aiError}</div>}
          {aiStatus && (
            <div style={{ background: '#1e222d', padding: 15, borderRadius: 4, marginBottom: 15 }}>
              <div style={{ marginBottom: 10 }}>
                <strong>供應商：</strong> {aiStatus.provider}
              </div>
              <div style={{ marginBottom: 10 }}>
                <strong>模型：</strong> {aiStatus.model}
              </div>
              <div style={{ marginBottom: 10 }}>
                <strong>模式：</strong>{' '}
                <span style={{ color: aiStatus.mode === 'mock' ? '#ff9800' : '#4caf50' }}>
                  {aiStatus.mode === 'mock' ? '模擬模式' : '正式模式'}
                </span>
              </div>
              <div style={{ marginBottom: 10 }}>
                <strong>API 金鑰：</strong>{' '}
                {aiStatus.api_key_configured ? (
                  <span style={{ color: '#4caf50' }}>已設定</span>
                ) : (
                  <span style={{ color: '#f44336' }}>未設定</span>
                )}
              </div>
              {aiStatus.base_url && (
                <div style={{ marginBottom: 10 }}>
                  <strong>Base URL：</strong> {aiStatus.base_url}
                </div>
              )}
              <div style={{ marginBottom: 10 }}>
                <strong>Timeout：</strong> {aiStatus.timeout_seconds} 秒
              </div>
              <div style={{ marginBottom: 10 }}>
                <strong>最大輸出 Tokens：</strong> {aiStatus.max_output_tokens}
              </div>
              <div style={{ marginTop: 15, padding: 10, background: '#2a2e39', borderRadius: 4, fontSize: 12, color: '#999' }}>
                {aiStatus.advisory_notice}
              </div>
              <div style={{ marginTop: 15 }}>
                <strong>連線狀態：</strong>{' '}
                {aiStatus.mode === 'mock' ? (
                  <span style={{ color: '#ff9800' }}>模擬模式（無需連線）</span>
                ) : aiStatus.api_key_configured ? (
                  <span style={{ color: '#ff9800' }}>已設定但未驗證</span>
                ) : (
                  <span style={{ color: '#f44336' }}>無法使用</span>
                )}
              </div>
              <button
                onClick={handleTestConnection}
                disabled={testConnectionLoading}
                style={{
                  marginTop: 10,
                  padding: '8px 16px',
                  background: testConnectionLoading ? '#666' : '#4a90e2',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 4,
                  cursor: testConnectionLoading ? 'not-allowed' : 'pointer',
                }}
              >
                {testConnectionLoading ? '測試中…' : '測試連線'}
              </button>
              {testResult && (
                <div style={{ marginTop: 10, padding: 10, background: testResult.ok ? '#1e4620' : '#4a1c1c', borderRadius: 4 }}>
                  {testResult.ok ? (
                    <span style={{ color: '#4caf50' }}>✓ 連線成功</span>
                  ) : (
                    <span style={{ color: '#f44336' }}>✗ {testResult.detail || '連線失敗'}</span>
                  )}
                </div>
              )}
            </div>
          )}
          <div style={{ padding: 10, background: '#2a2e39', borderRadius: 4, fontSize: 12, color: '#999' }}>
            注意：AI 供應商設定由平台管理。如需變更，請聯絡管理員。
          </div>
        </div>
      )}

      {activeTab === 'workspace' && (
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 15 }}>工作台偏好</h2>
          {workspaceLoading && <div>載入中…</div>}
          {workspaceError && <div style={{ color: '#f44336', marginBottom: 15 }}>{workspaceError}</div>}
          {!workspaceLoading && (
            <div style={{ background: '#1e222d', padding: 15, borderRadius: 4, marginBottom: 15 }}>
              <div style={{ marginBottom: 15 }}>
                <label htmlFor="default-symbol" style={{ display: 'block', marginBottom: 5, fontSize: 14 }}>
                  預設標的
                </label>
                <input
                  id="default-symbol"
                  type="text"
                  value={defaultSymbol}
                  onChange={(e) => setDefaultSymbol(e.target.value)}
                  placeholder="例如：US:AAPL"
                  style={{
                    width: '100%',
                    padding: 8,
                    background: '#2a2e39',
                    border: '1px solid #3a3e49',
                    borderRadius: 4,
                    color: '#fff',
                  }}
                />
              </div>
              <div style={{ marginBottom: 15 }}>
                <label htmlFor="default-range" style={{ display: 'block', marginBottom: 5, fontSize: 14 }}>
                  預設時間範圍
                </label>
                <select
                  id="default-range"
                  value={defaultRange}
                  onChange={(e) => setDefaultRange(e.target.value)}
                  style={{
                    width: '100%',
                    padding: 8,
                    background: '#2a2e39',
                    border: '1px solid #3a3e49',
                    borderRadius: 4,
                    color: '#fff',
                  }}
                >
                  <option value="1M">1 個月</option>
                  <option value="3M">3 個月</option>
                  <option value="6M">6 個月</option>
                  <option value="1Y">1 年</option>
                  <option value="YTD">今年至今</option>
                </select>
              </div>
              <div style={{ display: 'flex', gap: 10 }}>
                <button
                  onClick={handleSaveWorkspacePreferences}
                  disabled={workspaceSaving}
                  style={{
                    padding: '8px 16px',
                    background: workspaceSaving ? '#666' : '#4a90e2',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 4,
                    cursor: workspaceSaving ? 'not-allowed' : 'pointer',
                  }}
                >
                  {workspaceSaving ? '儲存中…' : '儲存偏好'}
                </button>
                <button
                  onClick={handleResetWorkspacePreferences}
                  disabled={workspaceSaving}
                  style={{
                    padding: '8px 16px',
                    background: '#f44336',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 4,
                    cursor: workspaceSaving ? 'not-allowed' : 'pointer',
                  }}
                >
                  重設偏好
                </button>
              </div>
              {workspaceSaved && (
                <div style={{ marginTop: 10, color: '#4caf50' }}>✓ 已儲存</div>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === 'usage' && (
        <div>
          <h2 style={{ fontSize: 18, marginBottom: 15 }}>用量資訊</h2>
          {usageLoading && <div>載入中…</div>}
          {usageError && <div style={{ color: '#f44336', marginBottom: 15 }}>{usageError}</div>}
          {usage && (
            <div style={{ background: '#1e222d', padding: 15, borderRadius: 4, marginBottom: 15 }}>
              <div style={{ marginBottom: 15 }}>
                <strong>過去 30 天 API 呼叫次數：</strong> {usage.total_api_calls}
              </div>
              {Object.keys(usage.by_endpoint).length > 0 && (
                <div>
                  <strong>按端點分類：</strong>
                  <ul style={{ marginTop: 10, paddingLeft: 20 }}>
                    {Object.entries(usage.by_endpoint).map(([endpoint, count]) => (
                      <li key={endpoint} style={{ marginBottom: 5 }}>
                        {endpoint}: {count}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {usage.total_api_calls === 0 && (
                <div style={{ color: '#999', fontStyle: 'italic' }}>
                  尚無使用記錄
                </div>
              )}
            </div>
          )}
          <button
            onClick={loadUsage}
            disabled={usageLoading}
            style={{
              padding: '8px 16px',
              background: usageLoading ? '#666' : '#4a90e2',
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: usageLoading ? 'not-allowed' : 'pointer',
            }}
          >
            重新整理
          </button>
        </div>
      )}
    </div>
  );
}
