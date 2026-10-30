'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import {
  CANDIDATE_MODE_FALLBACK_LABEL,
  preferredCandidateRole,
  writeCandidateMode,
  type CandidateStatus,
} from '@/lib/candidate';

export default function LoginPage() {
  const router = useRouter();
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [candidate, setCandidate] = useState<CandidateStatus | null>(null);
  const [candidatePending, setCandidatePending] = useState<string | null>(null);
  const [candidateError, setCandidateError] = useState('');

  useEffect(() => {
    let cancelled = false;
    api.candidateSession
      .status()
      .then((status) => {
        if (!cancelled) setCandidate(status.enabled ? status : null);
      })
      .catch(() => {
        // A missing candidate endpoint is the normal production shape; the
        // ordinary email/password form below stays fully usable.
        if (!cancelled) setCandidate(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const enterCandidate = async (role: string) => {
    if (!candidate) return;
    setCandidateError('');
    setCandidatePending(role);
    try {
      const session = await api.candidateSession.start(role);
      localStorage.setItem('token', session.access_token);
      writeCandidateMode({
        label_zh: session.mode_label_zh || candidate.label_zh || CANDIDATE_MODE_FALLBACK_LABEL,
        session_minutes: candidate.session_minutes ?? Math.round(session.expires_in / 60),
        role: session.role,
        accounts: candidate.accounts,
      });
      router.push('/dashboard');
    } catch (err) {
      setCandidateError(err instanceof Error ? err.message : '本機測試模式暫時不可用');
      setCandidatePending(null);
    }
  };

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

          {candidate && (
            <div
              data-testid="candidate-entry-panel"
              style={{
                marginBottom: '24px',
                padding: '16px 18px',
                border: '1px solid var(--border)',
                borderRadius: '10px',
                background: 'rgba(255,255,255,0.02)',
              }}
            >
              <div
                data-testid="candidate-entry-label"
                style={{
                  fontSize: '13px',
                  fontWeight: 600,
                  letterSpacing: '0.04em',
                  color: 'var(--text-white)',
                  marginBottom: '6px',
                }}
              >
                ◈ {candidate.label_zh || CANDIDATE_MODE_FALLBACK_LABEL}
              </div>
              <p style={{ margin: '0 0 12px', fontSize: '13px', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                毋須輸入密碼。只可使用此候選環境已設定的合成帳戶；身份、同意、用量與資料權限仍照常套用。
              </p>
              {candidateError && (
                <p
                  role="alert"
                  data-testid="candidate-entry-error"
                  style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}
                >
                  進入失敗：{candidateError}
                </p>
              )}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {candidate.accounts.map((account) => (
                  <button
                    key={account.role}
                    type="button"
                    data-testid={`candidate-enter-${account.role}`}
                    disabled={candidatePending !== null}
                    onClick={() => enterCandidate(account.role)}
                    style={{
                      padding: '9px 14px',
                      borderRadius: '8px',
                      border: '1px solid var(--border)',
                      background: 'transparent',
                      color: 'var(--text-white)',
                      fontSize: '13px',
                      cursor: candidatePending !== null ? 'default' : 'pointer',
                      opacity: candidatePending !== null && candidatePending !== account.role ? 0.5 : 1,
                    }}
                  >
                    {candidatePending === account.role
                      ? '正在進入…'
                      : `以${account.label_zh}${
                          account.role === preferredCandidateRole(candidate.accounts) ? '（預設）' : ''
                        }進入`}
                  </button>
                ))}
              </div>
              <p style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                下方「登入」表單仍可使用；此入口只在本機候選環境開啟。
              </p>
            </div>
          )}

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
