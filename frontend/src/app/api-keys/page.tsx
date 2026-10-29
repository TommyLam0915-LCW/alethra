'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, API_BASE_URL } from '@/lib/api';

interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  is_active: boolean;
  last_used_at: string | null;
  scopes?: string[];
}

interface CreatedKey {
  id: string;
  name: string;
  raw_key: string;
  key_prefix: string;
  scopes?: string[];
}

// Every scope below is enforced somewhere in the API today. An unchecked scope
// grants nothing: a key with no scopes is refused on the model and paper routes.
const SCOPE_OPTIONS = [
  { value: 'read', label: '讀取', description: '列出與查看你的模型、訓練任務及 AI 額度' },
  { value: 'write', label: '寫入', description: '訓練、推論與模擬交易等所有寫入操作' },
  { value: 'inference', label: '模型推論', description: '只做模型推論與讀取，不能排程訓練' },
  { value: 'training', label: '模型訓練', description: '排程、查詢、取消訓練並管理模型，不能推論' },
];

const ENDPOINTS = [
  { action: '列出我的模型', method: 'GET', path: '/api/v1/ml/models', scopes: 'read、inference 或 training' },
  { action: '模型詳情', method: 'GET', path: '/api/v1/ml/models/{model_id}', scopes: 'read、inference 或 training' },
  { action: '最新推論', method: 'POST', path: '/api/v1/ml/models/{model_id}/predict-latest', scopes: 'write 或 inference' },
  { action: '指定期間推論', method: 'POST', path: '/api/v1/ml/models/{model_id}/predict?start_date=…&end_date=…', scopes: 'write 或 inference' },
  { action: '排程訓練', method: 'POST', path: '/api/v1/ml/train?instrument_id=US:AAPL', scopes: 'write 或 training' },
  { action: '查詢訓練任務', method: 'GET', path: '/api/v1/training/jobs/{job_id}', scopes: 'read、inference 或 training' },
  { action: '取消訓練任務', method: 'POST', path: '/api/v1/training/jobs/{job_id}/cancel', scopes: 'write 或 training' },
  { action: '查詢 AI 額度', method: 'GET', path: '/api/v1/ml/entitlement', scopes: 'read、inference 或 training' },
];

export default function ApiKeysPage() {
  const router = useRouter();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [newName, setNewName] = useState('');
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Newly created key (shown once)
  const [justCreated, setJustCreated] = useState<CreatedKey | null>(null);
  const [copied, setCopied] = useState(false);

  // Revoke confirmation
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      return;
    }
    loadKeys();
  }, [router]);

  const loadKeys = async () => {
    try {
      setError(null);
      const data = await api.apiKeys.list();
      setKeys(data);
    } catch (err: any) {
      console.error('Failed to load API keys:', err);
      setError(err.message || '無法載入 API 金鑰');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const result = await api.apiKeys.create({
        name: newName.trim(),
        scopes: selectedScopes.length > 0 ? selectedScopes : undefined,
      });
      setJustCreated(result);
      setNewName('');
      setSelectedScopes([]);
      await loadKeys();
    } catch (err: any) {
      setCreateError(err.message || '建立金鑰失敗');
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async () => {
    if (!justCreated) return;
    try {
      await navigator.clipboard.writeText(justCreated.raw_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback: select text
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setRevoking(true);
    try {
      await api.apiKeys.revoke(revokeTarget.id);
      setRevokeTarget(null);
      await loadKeys();
    } catch (err: any) {
      console.error('Failed to revoke key:', err);
    } finally {
      setRevoking(false);
    }
  };

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    router.push('/');
  };

  const formatKeyPrefix = (prefix: string) => {
    // Show first 8 chars of the key_prefix
    return prefix.length > 8 ? prefix.slice(0, 8) + '...' : prefix;
  };

  return (
    <div className="container" style={{ paddingTop: '40px', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1>API 金鑰</h1>
        <div style={{ display: 'flex', gap: '12px' }}>
          <Link href="/dashboard" className="btn btn-secondary">
            返回我的實驗
          </Link>
          <button onClick={handleLogout} className="btn btn-secondary">
            登出
          </button>
        </div>
      </div>

      <div className="disclaimer">
        <strong>說明：</strong>API 金鑰提供對 Alethra 演示環境的程式化存取。
        金鑰只會顯示一次；撤銷後立即失效。金鑰不能再用來建立其他金鑰。
      </div>

      <div className="card" data-testid="api-guide" style={{ marginBottom: '24px' }}>
        <h2 style={{ marginBottom: '12px' }}>以程式呼叫你的個人模型</h2>
        <p style={{ color: '#555', fontSize: '14px', marginBottom: '12px' }}>
          在請求標頭帶上 <code>Authorization: Bearer &lt;你的 API 金鑰&gt;</code> 即可呼叫以下端點。
          權限不足時會回傳 403，並列出此端點接受的權限。
        </p>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>
                <th style={{ padding: '6px 8px' }}>動作</th>
                <th style={{ padding: '6px 8px' }}>方法與路徑</th>
                <th style={{ padding: '6px 8px' }}>需要權限</th>
              </tr>
            </thead>
            <tbody>
              {ENDPOINTS.map((ep) => (
                <tr key={`${ep.method}-${ep.path}`} style={{ borderBottom: '1px solid #f0f0f0' }}>
                  <td style={{ padding: '6px 8px' }}>{ep.action}</td>
                  <td style={{ padding: '6px 8px', fontFamily: 'monospace' }}>
                    {ep.method} {ep.path}
                  </td>
                  <td style={{ padding: '6px 8px', color: '#666' }}>{ep.scopes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <pre
          data-testid="api-guide-curl"
          style={{
            marginTop: '16px',
            padding: '12px',
            backgroundColor: '#1a1a2e',
            color: '#e0e0e0',
            borderRadius: '6px',
            fontSize: '12px',
            overflowX: 'auto',
          }}
        >
{`curl -X POST "${API_BASE_URL}/api/v1/ml/models/<MODEL_ID>/predict-latest" \\
  -H "Authorization: Bearer <你的 API 金鑰>"`}
        </pre>
        <ul style={{ marginTop: '12px', paddingLeft: '20px', color: '#555', fontSize: '13px', lineHeight: 1.8 }}>
          <li>每次推論扣 1 個 AI 額度；額度不足時回傳 429。</li>
          <li>排程訓練會先預留 1 個額度，成功後結算，失敗或取消則全額釋放。</li>
          <li>每把金鑰有獨立的請求額度；超出時回傳 429 與 Retry-After。</li>
          <li>模型輸出僅供研究用途，並非交易建議。</li>
        </ul>
      </div>

      {/* Newly created key — shown ONCE */}
      {justCreated && (
        <div
          className="card"
          style={{
            marginBottom: '24px',
            border: '2px solid #f0ad4e',
            backgroundColor: '#fff8e1',
          }}
        >
          <div style={{ marginBottom: '12px' }}>
            <strong style={{ color: '#856404' }}>
              金鑰已成功建立 — 此金鑰只會顯示一次
            </strong>
          </div>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              marginBottom: '12px',
            }}
          >
            <code
              data-testid="created-key-value"
              style={{
                flex: 1,
                padding: '12px 16px',
                backgroundColor: '#1a1a2e',
                color: '#e0e0e0',
                borderRadius: '6px',
                fontSize: '14px',
                fontFamily: 'monospace',
                wordBreak: 'break-all',
              }}
            >
              {justCreated.raw_key}
            </code>
            <button
              onClick={handleCopy}
              className="btn btn-primary"
              style={{ whiteSpace: 'nowrap' }}
            >
              {copied ? '已複製！' : '複製'}
            </button>
          </div>
          <p style={{ color: '#856404', fontSize: '13px', margin: 0 }}>
            請立即複製並妥善保管。關閉此提示後將無法再次查看完整金鑰。
          </p>
          <button
            onClick={() => setJustCreated(null)}
            className="btn btn-secondary"
            style={{ marginTop: '12px' }}
          >
            我已複製，關閉提示
          </button>
        </div>
      )}

      {/* Create new key form */}
      <div className="card" style={{ marginBottom: '24px' }}>
        <h2 style={{ marginBottom: '16px' }}>建立新金鑰</h2>
        <div style={{ marginBottom: '12px' }}>
          <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500 }}>
            金鑰名稱
          </label>
          <input
            type="text"
            data-testid="api-key-name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="例如：我的研究腳本"
            style={{
              width: '100%',
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid #ccc',
              fontSize: '14px',
            }}
          />
        </div>
        <div style={{ marginBottom: '16px' }}>
          <label style={{ display: 'block', marginBottom: '6px', fontWeight: 500 }}>
            權限範圍（選填）
          </label>
          <div style={{ display: 'grid', gap: '8px' }}>
            {SCOPE_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', cursor: 'pointer' }}
              >
                <input
                  type="checkbox"
                  data-testid={`scope-${opt.value}`}
                  checked={selectedScopes.includes(opt.value)}
                  onChange={() => toggleScope(opt.value)}
                  style={{ marginTop: '3px' }}
                />
                <span>
                  <span style={{ fontWeight: 500 }}>{opt.label}</span>
                  <span style={{ color: '#777', fontSize: '13px', marginLeft: '8px' }}>
                    {opt.description}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>
        {createError && (
          <p style={{ color: '#c0392b', marginBottom: '12px' }}>{createError}</p>
        )}
        <button
          onClick={handleCreate}
          disabled={!newName.trim() || creating}
          className="btn btn-primary"
          data-testid="api-key-create"
        >
          {creating ? '建立中...' : '建立金鑰'}
        </button>
      </div>

      {/* Keys list */}
      {loading ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          載入中...
        </div>
      ) : error ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px' }}>
          <p style={{ color: '#c0392b', marginBottom: '16px' }}>{error}</p>
          <button onClick={loadKeys} className="btn btn-secondary">
            重新載入
          </button>
        </div>
      ) : keys.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '60px' }}>
          <p style={{ color: '#666', marginBottom: '8px' }}>尚未建立任何 API 金鑰</p>
          <p style={{ color: '#999', fontSize: '14px' }}>
            在上方表單建立你的第一個金鑰，以程式化方式存取 Alethra 演示環境。
          </p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {keys.map((key) => (
            <div key={key.id} className="card" data-testid={`api-key-${key.key_prefix}`}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                    <h3 style={{ margin: 0 }}>{key.name}</h3>
                    <span
                      style={{
                        padding: '2px 10px',
                        borderRadius: '12px',
                        fontSize: '12px',
                        backgroundColor: key.is_active ? '#d4edda' : '#f5c6cb',
                        color: key.is_active ? '#155724' : '#721c24',
                      }}
                    >
                      {key.is_active ? '啟用中' : '已撤銷'}
                    </span>
                  </div>
                  <div style={{ color: '#666', fontSize: '14px', display: 'grid', gap: '4px' }}>
                    <div>
                      金鑰前綴：<code style={{ backgroundColor: '#f4f4f4', padding: '2px 6px', borderRadius: '4px' }}>{formatKeyPrefix(key.key_prefix)}</code>
                    </div>
                    <div>
                      建立於：{new Date(key.created_at).toLocaleDateString('zh-Hant', { year: 'numeric', month: '2-digit', day: '2-digit' })}
                    </div>
                    <div>
                      最後使用：{key.last_used_at ? new Date(key.last_used_at).toLocaleDateString('zh-Hant', { year: 'numeric', month: '2-digit', day: '2-digit' }) : '從未使用'}
                    </div>
                    {key.scopes && key.scopes.length > 0 && (
                      <div>
                        權限：{key.scopes.map((s) => {
                          const opt = SCOPE_OPTIONS.find((o) => o.value === s);
                          return opt ? opt.label : s;
                        }).join('、')}
                      </div>
                    )}
                  </div>
                </div>
                {key.is_active && (
                  <button
                    onClick={() => setRevokeTarget(key)}
                    className="btn btn-secondary"
                    style={{ color: '#c0392b', borderColor: '#c0392b' }}
                    data-testid={`revoke-${key.key_prefix}`}
                  >
                    撤銷
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Revoke confirmation dialog */}
      {revokeTarget && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={() => !revoking && setRevokeTarget(null)}
        >
          <div
            className="card"
            style={{ maxWidth: '420px', width: '90%', padding: '24px' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: '12px' }}>確認撤銷金鑰</h3>
            <p style={{ marginBottom: '20px', color: '#666' }}>
              確定要撤銷金鑰「<strong>{revokeTarget.name}</strong>」嗎？
              使用此金鑰的所有程式化存取將會立即失效，此操作無法復原。
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setRevokeTarget(null)}
                className="btn btn-secondary"
                disabled={revoking}
              >
                取消
              </button>
              <button
                onClick={handleRevoke}
                className="btn btn-primary"
                disabled={revoking}
                style={{ backgroundColor: '#c0392b', borderColor: '#c0392b' }}
                data-testid="revoke-confirm"
              >
                {revoking ? '撤銷中...' : '確認撤銷'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
