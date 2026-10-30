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
  | 'workspace'
  | 'strategies'
  | 'portfolios'
  | 'journal'
  | 'training'
  | 'market'
  | 'pilot';

interface NavItem {
  key: NavKey;
  href: string;
  label: string;
  labelEn: string;
  /** Short label for the mobile bottom bar, where the long form truncates. */
  labelMobile: string;
}

const PRIMARY_NAV: NavItem[] = [
  { key: 'workspace', href: '/workspace', label: '工作台', labelEn: 'Workspace', labelMobile: '工作台' },
  { key: 'strategies', href: '/dashboard', label: '策略與實驗', labelEn: 'Strategies & Experiments', labelMobile: '策略' },
  { key: 'portfolios', href: '/paper-portfolio', label: '投資組合', labelEn: 'Portfolios', labelMobile: '組合' },
  { key: 'journal', href: '/paper-portfolio?view=journal', label: '紀錄與結果', labelEn: 'Journal & Results', labelMobile: '日誌' },
];

const TOOL_LINKS = [
  { key: 'training' as NavKey, href: '/training', label: '訓練與模型', labelEn: 'Training & Models' },
  { href: '/models', label: '模型研究記錄', labelEn: 'Model research records' },
  { key: 'market' as NavKey, href: '/market-data', label: '市場資料', labelEn: 'Market data' },
  { key: 'pilot' as NavKey, href: '/pilot', label: 'Pilot 計劃', labelEn: 'Pilot program' },
];

const SETTINGS_LINKS = [
  { href: '/api-keys', label: 'API 金鑰', labelEn: 'API access' },
  { href: '/subscription', label: '訂閱與帳單', labelEn: 'Subscription' },
];

interface MenuLink {
  href: string;
  label: string;
  labelEn: string;
  key?: NavKey;
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
  activeKey,
}: {
  label: string;
  testId: string;
  items: MenuLink[];
  activeKey?: NavKey;
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
              aria-current={
                activeKey !== undefined &&
                (item as MenuLink & { key?: NavKey }).key === activeKey
                  ? 'page'
                  : undefined
              }
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
 * The one shared application shell for every authenticated page. Owns the
 * auth guard, the persistent top app bar (brand + primary nav + settings +
 * logout) and an optional persistent context bar slot, so pages no longer
 * render their own ad-hoc headers. Additive: pages opt in by wrapping their
 * content in <AppShell>.
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
        setCandidateError(err instanceof Error ? err.message : '切換情境失敗');
      }
      setCandidateSwitching(false);
    }
  };

  if (!authed) {
    return (
      <div className="app-shell app-shell-loading">
        <span className="app-shell-loading-text">載入中…</span>
      </div>
    );
  }

  return (
    <div className={`app-shell${fullHeight ? ' app-shell-fullheight' : ''}`}>
      <header className="app-bar" data-testid="app-bar">
        <Link href="/workspace" className="app-bar-brand" data-testid="app-brand">
          {brand.name}
        </Link>

        <nav className="app-nav" aria-label="主要導覽">
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
          <AppMenu label="工具" testId="tools-toggle" items={TOOL_LINKS} activeKey={active} />
          <AppMenu label="設定" testId="settings-toggle" items={SETTINGS_LINKS} />
          <button
            type="button"
            className="app-logout"
            onClick={handleLogout}
            data-testid="app-logout"
          >
            登出
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
            padding: '6px 16px',
            borderBottom: '1px solid var(--border)',
            background: 'rgba(255,255,255,0.02)',
            fontSize: '12px',
            color: 'var(--text-secondary)',
          }}
        >
          <span data-testid="candidate-mode-label" style={{ fontWeight: 600, color: 'var(--text-white)' }}>
            ◈ {candidate.label_zh}
          </span>
          {candidate.accounts.length > 1 && (
            <>
              <label htmlFor="candidate-role-select" style={{ color: 'var(--text-muted)' }}>
                測試情境
              </label>
              <select
                id="candidate-role-select"
                data-testid="candidate-role-select"
                value={candidate.role}
                disabled={candidateSwitching}
                onChange={(e) => handleCandidateRole(e.target.value)}
                style={{
                  background: 'transparent',
                  color: 'var(--text-white)',
                  border: '1px solid var(--border)',
                  borderRadius: '6px',
                  padding: '3px 8px',
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
          {candidateSwitching && <span>切換中…</span>}
          {candidateError && (
            <span role="alert" data-testid="candidate-switch-error">
              {candidateError}
            </span>
          )}
          <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
            合成資料 · 僅供本機測試
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

      <nav className="app-bottom-nav" aria-label="主要導覽（行動裝置）" data-testid="app-bottom-nav">
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
