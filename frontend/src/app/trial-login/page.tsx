'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { API_BASE_URL } from '@/lib/api';
import { clearCandidateMode } from '@/lib/candidate';
import { brand } from '@/lib/brand';

type State = 'reading' | 'opening' | 'error';

// The link puts the single-use token in the fragment, so it never reaches the
// server's access log or a Referer header. The fragment is stripped from the
// address bar before the exchange request is sent.
function readTokenFromHash(): string | null {
  if (typeof window === 'undefined') return null;
  const raw = window.location.hash.replace(/^#/, '');
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  const token = params.get('token');
  return token && token.trim() ? token.trim() : null;
}

export default function TrialLoginPage() {
  const router = useRouter();
  const [state, setState] = useState<State>('reading');
  const [message, setMessage] = useState('');
  const started = useRef(false);

  useEffect(() => {
    // React's development double-invoke would otherwise redeem the link twice
    // and make the second exchange fail on an already-rotated token.
    if (started.current) return;
    started.current = true;

    const token = readTokenFromHash();
    window.history.replaceState(null, '', window.location.pathname);
    if (!token) {
      setState('error');
      setMessage('This address is missing its trial token.');
      return;
    }

    setState('opening');
    fetch(`${API_BASE_URL}/api/auth/trial-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 401) {
            throw new Error('This trial link has already been used or is no longer valid.');
          }
          if (response.status === 404) {
            throw new Error('Trial sign-in is not available on this build.');
          }
          if (response.status === 403) {
            throw new Error('This trial link only works from the trial machine.');
          }
          throw new Error('The trial session could not be opened. Try again.');
        }
        return response.json() as Promise<{ access_token: string }>;
      })
      .then((body) => {
        localStorage.setItem('token', body.access_token);
        // A leftover candidate-session marker would mislabel this ordinary
        // trial identity as the synthetic test mode.
        clearCandidateMode();
        router.replace('/dashboard');
      })
      .catch((err: unknown) => {
        setState('error');
        setMessage(err instanceof Error ? err.message : 'The trial session could not be opened.');
      });
  }, [router]);

  return (
    <div className="login-root">
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
        </div>
      </div>

      <div className="login-form-zone">
        <div className="login-form-container">
          <h2 className="login-form-title">Trial sign-in</h2>
          <p
            data-testid="trial-login-status"
            style={{ margin: '0 0 20px', fontSize: '14px', lineHeight: 1.6, color: 'var(--text-secondary)' }}
          >
            {state === 'reading' && 'Reading the trial link…'}
            {state === 'opening' && 'Opening your trial session…'}
            {state === 'error' && message}
          </p>
          <p style={{ margin: '0 0 20px', fontSize: '13px', lineHeight: 1.6, color: 'var(--text-muted)' }}>
            This one-time link opens the local trial account on this machine. It is
            not a customer sign-in method; the ordinary sign-in page is unchanged.
          </p>
          {state === 'error' && (
            <Link
              href="/login"
              data-testid="trial-login-back"
              className="login-submit"
              style={{
                display: 'block',
                textAlign: 'center',
                textDecoration: 'none',
                boxSizing: 'border-box',
              }}
            >
              Go to sign in
            </Link>
          )}
          <div className="login-footer">
            <p>{brand.footer.text}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
