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
    <div className="login-root">
      {/* Left zone: brand identity */}
      <div className="login-brand-zone">
        <div className="login-brand-content">
          <div className="login-brand-mark">{brand.name}</div>
          <h1 className="login-brand-heading">
            研究策略，<br />追溯每次交易。
          </h1>
          <p className="login-brand-tagline">
            在歷史資料中測試規則，保存模擬交易與研究結果。
          </p>
          <div className="login-evidence">
            <div className="login-evidence-label">示例交易紀錄（已平倉）</div>
            <div className="login-evidence-line">AAPL · 買入 100 股 @ $185.40 · 2026-10-15 09:31</div>
            <div className="login-evidence-line">AAPL · 賣出 100 股 @ $198.11 · 2026-10-24 14:35 · <span className="g">+$1,271</span></div>
            <div className="login-evidence-reason">訊號：10-14 · 退出原因：SMA(5) 下穿 SMA(20)</div>
          </div>
        </div>
      </div>

      {/* Right zone: form */}
      <div className="login-form-zone">
        <div className="login-form-container">
          <h2 className="login-form-title">
            {isLogin ? brand.ui.auth.loginZh : brand.ui.auth.registerZh}
          </h2>

          {error && (
            <div className="login-error">
              <strong>錯誤：</strong>{error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="login-form">
            {!isLogin && (
              <div className="login-field">
                <label className="login-label">顯示名稱</label>
                <input
                  type="text"
                  className="login-input"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="選填"
                />
              </div>
            )}

            <div className="login-field">
              <label className="login-label">電子郵件</label>
              <input
                type="email"
                className="login-input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="your@email.com"
              />
            </div>

            <div className="login-field">
              <label className="login-label">密碼</label>
              <input
                type="password"
                className="login-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                placeholder="至少 8 個字元"
              />
            </div>

            <button
              type="submit"
              className="login-submit"
              disabled={loading}
            >
              {loading ? '處理中...' : isLogin ? brand.ui.auth.loginZh : brand.ui.auth.registerZh}
            </button>
          </form>

          <div className="login-switch">
            <button
              onClick={() => setIsLogin(!isLogin)}
              className="login-switch-btn"
            >
              {isLogin ? '沒有帳號？註冊' : '已有帳號？登入'}
            </button>
          </div>

          <div className="login-footer">
            <p>{brand.footer.textZh}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
