'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import { classifyLoginError, type ClassifiedFailure } from '@/lib/userErrors';
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
  const [error, setError] = useState<ClassifiedFailure | null>(null);
  const [loading, setLoading] = useState(false);
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
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
    } catch {
      setCandidateError('Password-free entry isn’t available right now. Try again.');
      setCandidatePending(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    // The submit button is disabled until this is true; guarding here as well
    // keeps the request from leaving if the button state is bypassed.
    if (!isLogin && !riskAcknowledged) return;
    setError(null);
    setLoading(true);

    try {
      if (isLogin) {
        const response = await api.auth.login({ email, password });
        localStorage.setItem('token', response.access_token);
        router.push('/dashboard');
      } else {
        await api.auth.register({
          email,
          password,
          display_name: displayName || undefined,
          risk_acknowledged: riskAcknowledged,
        });
        const response = await api.auth.login({ email, password });
        localStorage.setItem('token', response.access_token);
        router.push('/dashboard');
      }
    } catch (err) {
      setError(classifyLoginError(err));
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
            Research the rule.
            <br />
            Trace the decision.
          </h1>
          <p className="login-brand-tagline">
            Test ideas against historical data, then keep the paper trade and its
            reasoning in the same record.
          </p>
          <div className="login-evidence">
            <div className="login-evidence-label">Sample closed trade</div>
            <div className="login-evidence-line">AAPL · Buy 100 @ $185.40 · 2026-10-15 09:31</div>
            <div className="login-evidence-line">AAPL · Sell 100 @ $198.11 · 2026-10-24 14:35 · <span className="g">+$1,271</span></div>
            <div className="login-evidence-reason">Signal 10-14 · exit: SMA(5) crossed below SMA(20)</div>
          </div>
        </div>
      </div>

      {/* Right zone: form */}
      <div className="login-form-zone">
        <div className="login-form-container">
          <h2 className="login-form-title">
            {isLogin ? 'Sign in' : 'Create account'}
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
                {candidate.label_zh || CANDIDATE_MODE_FALLBACK_LABEL}
              </div>
              <p
                data-testid="candidate-entry-scope"
                style={{ margin: '0 0 8px', fontSize: '13px', lineHeight: 1.6, color: 'var(--text-secondary)' }}
              >
                Development-only test entry. This is not a customer sign-in method and
                must not be presented to customers as one.
              </p>
              <p style={{ margin: '0 0 12px', fontSize: '13px', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                No password needed. Only the synthetic accounts configured for this
                candidate environment are available; identity, consent, usage and
                data permissions still apply.
              </p>
              {candidateError && (
                <p
                  role="alert"
                  data-testid="candidate-entry-error"
                  style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-secondary)' }}
                >
                  {candidateError}
                </p>
              )}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {candidate.accounts.map((account) => (
                  <button
                    key={account.role}
                    type="button"
                    data-testid={`candidate-enter-${account.role}`}
                    disabled={candidatePending !== null || loading}
                    onClick={() => enterCandidate(account.role)}
                    style={{
                      padding: '9px 14px',
                      borderRadius: '8px',
                      border: '1px solid var(--border)',
                      background: 'transparent',
                      color: 'var(--text-white)',
                      fontSize: '13px',
                      cursor: candidatePending !== null || loading ? 'default' : 'pointer',
                      opacity:
                        candidatePending !== null && candidatePending !== account.role
                          ? 0.5
                          : 1,
                    }}
                  >
                    {candidatePending === account.role
                      ? 'Entering…'
                      : `Enter as ${account.label_zh}${
                          account.role === preferredCandidateRole(candidate.accounts)
                            ? ' (default)'
                            : ''
                        }`}
                  </button>
                ))}
              </div>
              <p style={{ margin: '10px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
                The sign-in form below still works; this entry only opens in a local
                candidate environment.
              </p>
            </div>
          )}

          {error && (
            <div
              className="login-error"
              role="alert"
              data-testid="login-error"
              data-error-kind={error.kind}
            >
              <strong>
                {error.kind === 'invalid_credentials' ? '登入失敗：' : '無法登入：'}
              </strong>{' '}
              {error.message}
              {error.reference && (
                <div
                  data-testid="login-error-reference"
                  style={{
                    marginTop: '8px',
                    fontSize: '12px',
                    lineHeight: 1.6,
                    color: 'var(--text-secondary)',
                  }}
                >
                  錯誤參考編號{' '}
                  <code
                    style={{
                      userSelect: 'all',
                      fontFamily:
                        'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                      color: 'var(--text-white)',
                    }}
                  >
                    {error.reference}
                  </code>
                  <br />
                  回報時請附上此編號，讓工程人員對應到這次請求的伺服器紀錄。系統不會自動重試登入。
                </div>
              )}
            </div>
          )}

          <form onSubmit={handleSubmit} className="login-form">
            {!isLogin && (
              <div className="login-field">
                <label className="login-label">Display name</label>
                <input
                  type="text"
                  className="login-input"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Optional"
                />
              </div>
            )}

            <div className="login-field">
              <label className="login-label">Email</label>
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
              <label className="login-label">Password</label>
              <input
                type="password"
                className="login-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                placeholder="At least 8 characters"
              />
            </div>

            {!isLogin && (
              <label
                style={{
                  display: 'flex',
                  gap: '10px',
                  alignItems: 'flex-start',
                  fontSize: '12px',
                  lineHeight: 1.6,
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={riskAcknowledged}
                  onChange={(e) => setRiskAcknowledged(e.target.checked)}
                  data-testid="risk-acknowledgement"
                  required
                  style={{ marginTop: '3px', flex: '0 0 auto' }}
                />
                <span>
                  I have read the{' '}
                  <Link href="/legal" target="_blank" rel="noreferrer" style={{ color: 'var(--accent-blue)' }}>
                    risk disclosure
                  </Link>
                  . I understand Alethra is a research tool: backtest and paper results are not
                  investment advice, are not live trades, and do not predict future returns.
                </span>
              </label>
            )}

            <button
              type="submit"
              className="login-submit"
              disabled={loading || (!isLogin && !riskAcknowledged)}
            >
              {loading ? 'Signing in…' : isLogin ? 'Sign in' : 'Create account'}
            </button>
          </form>

          <div className="login-switch">
            <button
              type="button"
              onClick={() => {
                setIsLogin(!isLogin);
                setError(null);
                setRiskAcknowledged(false);
              }}
              className="login-switch-btn"
              disabled={loading}
            >
              {isLogin ? 'No account? Create one' : 'Already have an account? Sign in'}
            </button>
          </div>

          <div className="login-footer">
            <p>{brand.footer.text}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
