'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';

export default function LoginPage() {
  const router = useRouter();
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      if (isLogin) {
        const response = await api.auth.login({ email, password });
        localStorage.setItem('token', response.access_token);
        router.push('/dashboard');
      } else {
        await api.auth.register({ email, password, display_name: displayName || undefined });
        const response = await api.auth.login({ email, password });
        localStorage.setItem('token', response.access_token);
        router.push('/dashboard');
      }
    } catch (err: any) {
      setError(err.message || '操作失敗');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dash-root" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
      <div style={{ maxWidth: '440px', width: '100%', padding: '0 24px' }}>
        <div className="dash-panel" style={{ marginBottom: '24px' }}>
          <div className="dash-panel-body" style={{ padding: '40px' }}>
            {/* Brand Header */}
            <div style={{ textAlign: 'center', marginBottom: '32px' }}>
              <div style={{
                fontSize: '12px',
                letterSpacing: '2px',
                color: 'var(--accent-blue)',
                textTransform: 'uppercase',
                marginBottom: '12px',
                fontWeight: '600'
              }}>
                {brand.name}
              </div>
              <h1 style={{
                fontSize: '28px',
                color: 'var(--text-white)',
                fontWeight: '700',
                marginBottom: '8px'
              }}>
                {isLogin ? brand.ui.auth.loginZh : brand.ui.auth.registerZh}
              </h1>
              <p style={{ fontSize: '14px', color: 'var(--text-muted)' }}>
                {isLogin ? '歡迎回來' : '建立你的帳戶'}
              </p>
            </div>

            {error && (
              <div className="dash-disclaimer" style={{ marginBottom: '24px' }}>
                <strong>錯誤：</strong>{error}
              </div>
            )}

            <form onSubmit={handleSubmit}>
              {!isLogin && (
                <div className="form-group" style={{ marginBottom: '20px' }}>
                  <label style={{ color: 'var(--text-secondary)', marginBottom: '8px', display: 'block', fontSize: '13px', fontWeight: '500' }}>
                    顯示名稱
                  </label>
                  <input
                    type="text"
                    className="input"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="選填"
                    style={{
                      background: 'var(--bg-tertiary)',
                      border: '1px solid var(--border-light)',
                      color: 'var(--text-primary)',
                      padding: '12px'
                    }}
                  />
                </div>
              )}

              <div className="form-group" style={{ marginBottom: '20px' }}>
                <label style={{ color: 'var(--text-secondary)', marginBottom: '8px', display: 'block', fontSize: '13px', fontWeight: '500' }}>
                  電子郵件
                </label>
                <input
                  type="email"
                  className="input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  style={{
                    background: 'var(--bg-tertiary)',
                    border: '1px solid var(--border-light)',
                    color: 'var(--text-primary)',
                    padding: '12px'
                  }}
                />
              </div>

              <div className="form-group" style={{ marginBottom: '24px' }}>
                <label style={{ color: 'var(--text-secondary)', marginBottom: '8px', display: 'block', fontSize: '13px', fontWeight: '500' }}>
                  密碼
                </label>
                <input
                  type="password"
                  className="input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                  style={{
                    background: 'var(--bg-tertiary)',
                    border: '1px solid var(--border-light)',
                    color: 'var(--text-primary)',
                    padding: '12px'
                  }}
                />
              </div>

              <button
                type="submit"
                className="btn btn-primary"
                style={{
                  width: '100%',
                  padding: '14px',
                  fontSize: '15px',
                  fontWeight: '600'
                }}
                disabled={loading}
              >
                {loading ? '處理中...' : isLogin ? brand.ui.auth.loginZh : brand.ui.auth.registerZh}
              </button>
            </form>

            <div style={{ textAlign: 'center', marginTop: '24px' }}>
              <button
                onClick={() => setIsLogin(!isLogin)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--accent-blue)',
                  cursor: 'pointer',
                  fontSize: '14px',
                  fontWeight: '500'
                }}
              >
                {isLogin ? '沒有帳號？註冊' : '已有帳號？登入'}
              </button>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{ textAlign: 'center', fontSize: '12px', color: 'var(--text-muted)' }}>
          <p>{brand.footer.textZh}</p>
        </div>
      </div>
    </div>
  );
}
