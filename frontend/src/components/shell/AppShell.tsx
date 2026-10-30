'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { brand } from '@/lib/brand';
import { api, ApiError } from '@/lib/api';
import {
  clearCandidateMode,
  readCandidateMode,
  writeCandidateMode,
  type CandidateModeState,
} from '@/lib/candidate';

export type NavKey =
  | 'market'
  | 'workspace'
  | 'strategies'
  | 'models'
  | 'training'
  | 'portfolios'
  | 'journal'
  | 'pilot';

interface NavItem {
  key: NavKey;
  href: string;
  label: string;
  /** Short label for the mobile bottom bar, where the long form truncates. */
  labelMobile: string;
}

/**
 * Primary navigation follows the product's own task order — find a market,
 * do the work, keep the record — rather than internal backend module names.
 * Hrefs keep the existing routes so bookmarks and deep links still resolve.
 */
const PRIMARY_NAV: NavItem[] = [
  { key: 'market', href: '/market-data', label: 'Markets', labelMobile: 'Markets' },
  { key: 'workspace', href: '/workspace', label: 'Workspace', labelMobile: 'Workspace' },
  { key: 'strategies', href: '/dashboard', label: 'Research', labelMobile: 'Research' },
  { key: 'models', href: '/models', label: 'Models', labelMobile: 'Models' },
  { key: 'journal', href: '/paper-portfolio?view=journal', label: 'Journal', labelMobile: 'Journal' },
];

const TOOL_LINKS = [
  { href: '/training', label: 'Training jobs' },
  { href: '/inference', label: 'Inference runs' },
  { href: '/research-drafts', label: 'Research drafts' },
  { href: '/execution', label: 'Execution intents' },
  { href: '/paper-portfolio', label: 'Paper portfolios' },
  { href: '/pilot', label: 'Pilot program' },
];

const SETTINGS_LINKS = [
  { href: '/api-keys', label: 'API access' },
  { href: '/personal-memory', label: 'Personal research memory' },
  { href: '/subscription', label: 'Subscription & usage' },
  { href: '/legal', label: 'Legal & disclosures' },
];

interface MenuLink {
  href: string;
  label: string;
}

/**
 * A single dropdown used for both the tools and settings menus. Sharing it
 * keeps the click-outside / Escape / aria-expanded behaviour identical, and it
 * lives in the always-visible app-bar-right so mobile users (whose top nav is
 * hidden) still have a path to every secondary page.
 */
function AppMenu({
  label,
  testId,
  items,
}: {
  label: string;
  testId: string;
  items: MenuLink[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="app-settings" ref={ref}>
      <button
        type="button"
        className="app-settings-toggle"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={`${testId}-menu`}
        onClick={() => setOpen((v) => !v)}
        data-testid={testId}
      >
        {label}
      </button>
      {open && (
        <div
          id={`${testId}-menu`}
          role="menu"
          className="app-settings-menu"
          data-testid={`${testId}-menu`}
        >
          {items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              role="menuitem"
              className="app-settings-item"
              onClick={() => setOpen(false)}
            >
              {item.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * One environment marker for the whole app. Everything test-only, sample or
 * paper-execution related lives behind this single control instead of being
 * repeated as a warning paragraph on every panel.
 */
function EnvBadge() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="app-settings" ref={ref}>
      <button
        type="button"
        className="app-env-badge"
        data-env="demo"
        data-testid="env-badge"
        aria-expanded={open}
        aria-controls="env-popover"
        onClick={() => setOpen((v) => !v)}
        title="Environment and execution scope"
      >
        Demo
      </button>
      {open && (
        <div id="env-popover" className="app-env-popover" role="dialog" data-testid="env-popover">
          <strong>Demo environment</strong>
          Market data may be sample, historical or delayed. Orders are paper
          orders only — nothing is sent to a broker or exchange. Review the
          source and as-of label on each data surface.
          <div style={{ marginTop: 8 }}>
            <Link href="/legal" className="app-settings-item" style={{ padding: 0 }}>
              Legal & disclosures
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The one shared application shell for every authenticated page. Owns the
 * auth guard, the persistent top app bar (brand + primary nav + environment +
 * tools + settings + logout) and an optional persistent context bar slot, so
 * pages no longer render their own ad-hoc headers.
 */
export default function AppShell({
  active,
  contextBar,
  fullHeight = false,
  children,
}: {
  /** Which primary nav item is the current page (drives aria-current + styling). */
  active?: NavKey;
  /** Persistent context bar rendered directly under the app bar. */
  contextBar?: ReactNode;
  /** True for full-bleed layouts (workspace) that manage their own internal scroll. */
  fullHeight?: boolean;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const [authed, setAuthed] = useState(false);
  const [candidate, setCandidate] = useState<CandidateModeState | null>(null);
  const [candidateSwitching, setCandidateSwitching] = useState(false);
  const [candidateError, setCandidateError] = useState('');

  useEffect(() => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
    if (!token) {
      window.location.href = '/login';
      return;
    }
    setAuthed(true);
    setCandidate(readCandidateMode());
  }, []);

  useEffect(() => {
    if (!candidate) return;
    let cancelled = false;
    // The profile mode may have been turned off after this session started.
    // Issuance stops, but the token already issued stays valid until its own
    // expiry, so the session is kept and only the test-scenario banner drops.
    api.candidateSession
      .status()
      .then((status) => {
        if (cancelled || status.enabled) return;
        clearCandidateMode();
        setCandidate(null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [candidate]);

  const handleLogout = () => {
    localStorage.removeItem('token');
    clearCandidateMode();
    window.location.href = '/login';
  };

  const handleCandidateRole = async (role: string) => {
    if (!candidate || role === candidate.role) return;
    setCandidateError('');
    setCandidateSwitching(true);
    try {
      const session = await api.candidateSession.start(role);
      localStorage.setItem('token', session.access_token);
      writeCandidateMode({
        ...candidate,
        role: session.role,
        label_zh: session.mode_label_zh || candidate.label_zh,
      });
      window.location.reload();
    } catch (err) {
      // The operator may have switched the local mode off since this session
      // started. Drop the test-mode marker so the shell stops advertising a
      // scenario switch, but keep the still-valid session rather than ejecting
      // the user mid-research.
      if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
        clearCandidateMode();
        setCandidate(null);
      } else {
        setCandidateError(err instanceof Error ? err.message : 'Could not switch scenario');
      }
      setCandidateSwitching(false);
    }
  };

  if (!authed) {
    return (
      <div className="app-shell app-shell-loading">
        <span className="app-shell-loading-text">Loading…</span>
      </div>
    );
  }

  return (
    <div className={`app-shell${fullHeight ? ' app-shell-fullheight' : ''}`}>
      <header className="app-bar" data-testid="app-bar">
        <Link href="/workspace" className="app-bar-brand" data-testid="app-brand">
          {brand.name}
        </Link>

        <nav className="app-nav" aria-label="Primary">
          {PRIMARY_NAV.map((item) => {
            const isActive = active === item.key;
            return (
              <Link
                key={item.key}
                href={item.href}
                className={`app-nav-link${isActive ? ' app-nav-link-active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                data-testid={`nav-${item.key}`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="app-bar-right">
          <EnvBadge />
          <AppMenu label="Tools" testId="tools-toggle" items={TOOL_LINKS} />
          <AppMenu label="Settings" testId="settings-toggle" items={SETTINGS_LINKS} />
          <button
            type="button"
            className="app-logout"
            onClick={handleLogout}
            data-testid="app-logout"
          >
            Log out
          </button>
        </div>
      </header>

      {candidate && (
        <div
          data-testid="candidate-mode-bar"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '10px',
            padding: '5px 16px',
            borderBottom: '1px solid var(--border-subtle)',
            background: 'var(--surface-1)',
            fontSize: '12px',
            color: 'var(--text-secondary)',
          }}
        >
          <span data-testid="candidate-mode-label" style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {candidate.label_zh}
          </span>
          {candidate.accounts.length > 1 && (
            <>
              <label htmlFor="candidate-role-select">Test scenario</label>
              <select
                id="candidate-role-select"
                data-testid="candidate-role-select"
                value={candidate.role}
                disabled={candidateSwitching}
                onChange={(e) => handleCandidateRole(e.target.value)}
                style={{
                  background: 'transparent',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: '4px',
                  padding: '2px 8px',
                  fontSize: '12px',
                }}
              >
                {candidate.accounts.map((account) => (
                  <option key={account.role} value={account.role}>
                    {account.label_zh}
                  </option>
                ))}
              </select>
            </>
          )}
          {candidateSwitching && <span>Switching…</span>}
          {candidateError && (
            <span role="alert" data-testid="candidate-switch-error">
              {candidateError}
            </span>
          )}
          <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
            Sample data · paper execution · local environment
          </span>
        </div>
      )}

      {contextBar && (
        <div className="app-contextbar" data-testid="app-contextbar">
          {contextBar}
        </div>
      )}

      <main className="app-shell-main" data-pathname={pathname}>
        {children}
      </main>

      <nav className="app-bottom-nav" aria-label="Primary (mobile)" data-testid="app-bottom-nav">
        {PRIMARY_NAV.map((item) => {
          const isActive = active === item.key;
          return (
            <Link
              key={item.key}
              href={item.href}
              className={`app-bottom-nav-item${isActive ? ' app-bottom-nav-item-active' : ''}`}
              aria-current={isActive ? 'page' : undefined}
              data-testid={`bottom-nav-${item.key}`}
            >
              {item.labelMobile}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
