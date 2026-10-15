'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';

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
  key: string;
  key_prefix: string;
  scopes?: string[];
}

const SCOPE_OPTIONS = [
  { value: 'research_run', label: '研究執行' },
  { value: 'execution', label: '交易執行' },
  { value: 'inference', label: '模型推論' },
  { value: 'training', label: '模型訓練' },
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
      await navigator.clipboard.writeText(justCreated.key);
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
            返回主控台
          </Link>
          <button onClick={handleLogout} className="btn btn-secondary">
            登出
          </button>
        </div>
      </div>

      <div className="disclaimer">
        <strong>說明：</strong>API 金鑰提供對 Alethra 演示環境的程式化存取。
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
              {justCreated.key}
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
          <div style={{ display: 'flex', gap: '20px', flexWrap: 'wrap' }}>
            {SCOPE_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}
              >
                <input
                  type="checkbox"
                  checked={selectedScopes.includes(opt.value)}
                  onChange={() => toggleScope(opt.value)}
                />
                {opt.label}
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
            <div key={key.id} className="card">
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
