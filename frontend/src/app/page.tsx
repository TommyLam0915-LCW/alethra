'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { brand } from '@/lib/brand';
import {
  CANDIDATE_MODE_FALLBACK_LABEL,
  preferredCandidateRole,
  writeCandidateMode,
} from '@/lib/candidate';

export default function HomePage() {
  const router = useRouter();
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [entering, setEntering] = useState(false);
  const [entryError, setEntryError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (token) {
      setIsLoggedIn(true);
      return;
    }

    // Password-free entry exists only for an explicitly enabled local
    // candidate profile. The backend stays the authority: when the mode is off
    // the status call reports enabled=false and this page stays a normal
    // landing page with its login form.
    let cancelled = false;
    (async () => {
      try {
        const status = await api.candidateSession.status();
        if (cancelled || !status.enabled) return;
        const role = preferredCandidateRole(status.accounts);
        if (!role) return;
        setEntering(true);
        const session = await api.candidateSession.start(role);
        if (cancelled) return;
        localStorage.setItem('token', session.access_token);
        writeCandidateMode({
          label_zh: session.mode_label_zh || status.label_zh || CANDIDATE_MODE_FALLBACK_LABEL,
          session_minutes: status.session_minutes ?? Math.round(session.expires_in / 60),
          role: session.role,
          accounts: status.accounts,
        });
        router.replace('/dashboard');
      } catch (err) {
        if (!cancelled) {
          setEntering(false);
          setEntryError(err instanceof Error ? err.message : '本機測試模式暫時不可用');
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="dash-root">
      <div className="container" style={{ paddingTop: '80px', paddingBottom: '80px' }}>
        {/* Hero Section */}
        <div style={{ textAlign: 'center', marginBottom: '80px' }}>
          <p style={{
            fontSize: '14px',
            letterSpacing: '3px',
            color: 'var(--accent-blue)',
            textTransform: 'uppercase',
            marginBottom: '20px',
            fontWeight: '600'
          }}>
            {brand.name} — {brand.productDescriptor.zh}
          </p>
          <h1 style={{
            fontSize: '48px',
            marginBottom: '28px',
            lineHeight: '1.2',
            color: 'var(--text-white)',
            fontWeight: '700'
          }}>
            {brand.ui.landing.heroTitle}
          </h1>
          <p style={{
            fontSize: '20px',
            color: 'var(--text-secondary)',
            maxWidth: '700px',
            margin: '0 auto 48px',
            lineHeight: '1.7'
          }}>
            {brand.ui.landing.heroSubtitle}
          </p>

          {entering && (
            <p
              data-testid="candidate-entry-pending"
              style={{
                display: 'inline-block',
                marginBottom: '32px',
                padding: '10px 18px',
                borderRadius: '999px',
                border: '1px solid var(--border)',
                color: 'var(--text-secondary)',
                fontSize: '14px',
              }}
            >
              {CANDIDATE_MODE_FALLBACK_LABEL} · 正在進入測試工作區…
            </p>
          )}

          {entryError && (
            <p
              role="alert"
              data-testid="candidate-entry-error"
              style={{
                maxWidth: '640px',
                margin: '0 auto 32px',
                color: 'var(--text-secondary)',
                fontSize: '14px',
              }}
            >
              未能自動進入本機測試模式（{entryError}）。請使用下方登入，或重新載入此頁。
            </p>
          )}

          <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
            {isLoggedIn ? (
              <Link href="/dashboard" className="btn btn-primary" style={{ padding: '14px 32px', fontSize: '16px' }}>
                前往控制台
              </Link>
            ) : (
              <>
                <Link href="/login" className="btn btn-primary" style={{ padding: '14px 32px', fontSize: '16px' }}>
                  登入
                </Link>
                <Link href="/login" className="btn btn-secondary" style={{ padding: '14px 32px', fontSize: '16px' }}>
                  註冊
                </Link>
              </>
            )}
          </div>
        </div>

        {/* Disclaimer */}
        <div className="dash-disclaimer" style={{ maxWidth: '800px', margin: '0 auto 60px' }}>
          <strong>重要聲明：</strong>{brand.disclaimer.zhFull}
        </div>

        {/* Features Grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: '24px',
          marginTop: '60px'
        }}>
          {brand.ui.landing.features.map((feature, idx) => (
            <div key={idx} className="dash-panel" style={{ transition: 'all 0.2s' }}>
              <div className="dash-panel-body" style={{ padding: '32px' }}>
                <div style={{
                  fontSize: '48px',
                  marginBottom: '20px',
                  filter: 'grayscale(0.2)'
                }}>
                  {idx === 0 && '📊'}
                  {idx === 1 && '🔬'}
                  {idx === 2 && '⚠️'}
                </div>
                <h3 style={{
                  marginBottom: '16px',
                  fontSize: '22px',
                  color: 'var(--text-white)',
                  fontWeight: '600'
                }}>
                  {feature.titleZh}
                </h3>
                <p style={{
                  color: 'var(--text-secondary)',
                  lineHeight: '1.7',
                  fontSize: '15px'
                }}>
                  {feature.descriptionZh}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{
          marginTop: '80px',
          textAlign: 'center',
          color: 'var(--text-muted)',
          fontSize: '14px',
          paddingTop: '40px',
          borderTop: '1px solid var(--border)'
        }}>
          <p>{brand.footer.textZh}</p>
        </div>
      </div>
    </div>
  );
}
