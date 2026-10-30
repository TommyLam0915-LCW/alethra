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

/**
 * Public landing page. States what the product does and where to start.
 * Limitations are kept in one place (Legal & disclosures) rather than repeated
 * as warning paragraphs; the preview is labelled as illustrative sample data.
 */
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
    // — or the status call is unavailable — this page stays a normal landing
    // page with its sign-in link, without surfacing transport errors.
    let cancelled = false;
    (async () => {
      let status;
      try {
        status = await api.candidateSession.status();
      } catch {
        return;
      }
      if (cancelled || !status.enabled) return;
      const role = preferredCandidateRole(status.accounts);
      if (!role) return;
      setEntering(true);
      try {
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
      } catch {
        if (!cancelled) {
          setEntering(false);
          setEntryError('Password-free entry isn’t available right now. Sign in to continue.');
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="landing">
      <header className="landing-bar">
        <Link href="/" className="landing-brand">
          {brand.name}
        </Link>
        <nav className="landing-bar-nav" aria-label="Account">
          {isLoggedIn ? (
            <Link href="/workspace" className="landing-bar-link">
              Open workspace
            </Link>
          ) : (
            <Link href="/login" className="landing-bar-link">
              Sign in
            </Link>
          )}
        </nav>
      </header>

      <main className="landing-main">
        <section className="landing-hero">
          <p className="landing-eyebrow">Investment research workspace</p>
          <h1 className="landing-title">
            Ideas, tested.
            <br />
            Decisions, yours.
          </h1>
          <p className="landing-lead">
            Develop strategies, test your assumptions, and review every decision
            in one connected workspace.
          </p>
          <div className="landing-ctas">
            <Link
              href={isLoggedIn ? '/workspace' : '/login'}
              className="landing-cta landing-cta-primary"
              data-testid="landing-primary-cta"
            >
              Open workspace
            </Link>
            <a href="#workflow" className="landing-cta landing-cta-secondary">
              Explore the workflow
            </a>
          </div>
          <p className="landing-support">Research. Paper trading. Personal models.</p>

          {entering && (
            <p className="landing-note" data-testid="candidate-entry-pending">
              Opening your workspace…
            </p>
          )}
          {entryError && (
            <p className="landing-note" role="alert" data-testid="candidate-entry-error">
              {entryError}
            </p>
          )}
        </section>

        <section className="landing-preview" aria-label="Workspace preview">
          <div className="landing-preview-head">
            <span className="landing-preview-title">Workspace</span>
            <span className="landing-preview-note">
              Illustrative layout · sample data, not live
            </span>
          </div>
          <div className="landing-preview-grid" aria-hidden="true">
            <div className="landing-preview-watch">
              <div className="landing-preview-label">Watchlist</div>
              {['AAPL', 'MSFT', 'BTC/USD'].map((symbol) => (
                <div key={symbol} className="landing-preview-row">
                  <span>{symbol}</span>
                  <span className="landing-preview-dash">—</span>
                </div>
              ))}
            </div>
            <div className="landing-preview-chart">
              <div className="landing-preview-label">1 min · sample session</div>
              <div className="landing-preview-bars">
                {[38, 54, 44, 62, 50, 70, 58, 76, 66, 82, 72, 88].map((height, index) => (
                  <span
                    key={index}
                    className={`landing-preview-bar${index % 3 === 1 ? ' landing-preview-bar-down' : ''}`}
                    style={{ height: `${height}%` }}
                  />
                ))}
              </div>
            </div>
            <div className="landing-preview-order">
              <div className="landing-preview-label">Paper order</div>
              <div className="landing-preview-field">Side · Buy</div>
              <div className="landing-preview-field">Quantity · 100</div>
              <div className="landing-preview-field">Type · Market</div>
              <div className="landing-preview-field landing-preview-field-action">
                Review paper order
              </div>
            </div>
          </div>
        </section>

        <section id="workflow" className="landing-workflow">
          <article className="landing-workflow-item">
            <span className="landing-workflow-index">01 · Research</span>
            <h2>Give every idea a clear starting point.</h2>
            <p>Define your rules, preserve each revision, and compare what changed.</p>
          </article>
          <article className="landing-workflow-item">
            <span className="landing-workflow-index">02 · Practice</span>
            <h2>Put your process into practice.</h2>
            <p>
              Explore the chart, place paper orders, and keep your reasoning
              alongside the outcome.
            </p>
          </article>
          <article className="landing-workflow-item">
            <span className="landing-workflow-index">03 · Learn</span>
            <h2>Build models around your decisions.</h2>
            <p>
              Review labels, train a personal model, and compare versions against
              a baseline.
            </p>
          </article>
        </section>
      </main>

      <footer className="landing-footer">
        <span>Alethra — research tool, not investment advice.</span>
        <nav className="landing-footer-links" aria-label="Legal">
          <Link href="/legal">Legal & disclosures</Link>
          <Link href="/login">Sign in</Link>
        </nav>
      </footer>
    </div>
  );
}
