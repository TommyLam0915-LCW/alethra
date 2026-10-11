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
    <div className="container" style={{ maxWidth: '400px', paddingTop: '80px' }}>
      <div className="card">
        <h1 style={{ marginBottom: '24px', textAlign: 'center' }}>
          {isLogin ? brand.ui.auth.loginZh : brand.ui.auth.registerZh}
        </h1>

        {error && <div className="alert alert-warning">{error}</div>}

        <form onSubmit={handleSubmit}>
          {!isLogin && (
            <div className="form-group">
              <label>顯示名稱</label>
              <input
                type="text"
                className="input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="選填"
              />
            </div>
          )}

          <div className="form-group">
            <label>電子郵件</label>
            <input
              type="email"
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label>密碼</label>
            <input
              type="password"
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
            />
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%' }}
            disabled={loading}
          >
            {loading ? '處理中...' : isLogin ? brand.ui.auth.loginZh : brand.ui.auth.registerZh}
          </button>
        </form>

        <div style={{ textAlign: 'center', marginTop: '20px' }}>
          <button
            onClick={() => setIsLogin(!isLogin)}
            style={{ background: 'none', border: 'none', color: '#0070f3', cursor: 'pointer' }}
          >
            {isLogin ? '沒有帳號？註冊' : '已有帳號？登入'}
          </button>
        </div>
      </div>
    </div>
  );
}
