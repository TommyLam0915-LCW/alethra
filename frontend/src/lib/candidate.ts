// Local-only password-free entry for the disposable candidate stack.
//
// The browser never chooses who it is: it can only ask the backend to mint a
// short-lived session for one of the roles the *server* allowlists. What is
// stored here is purely display state so the shell can show the test-mode
// label and the configured scenario switcher. Clearing or forging this value
// cannot widen access -- the POST still needs the backend's local candidate
// mode, a loopback peer and an allowlisted role.

export const CANDIDATE_MODE_STORAGE_KEY = 'candidate_mode';
export const CANDIDATE_MODE_FALLBACK_LABEL = '本機測試模式 · 合成資料';

export interface CandidateAccount {
  role: string;
  label_zh: string;
}

export interface CandidateStatus {
  enabled: boolean;
  synthetic: boolean;
  label_zh: string | null;
  session_minutes: number | null;
  accounts: CandidateAccount[];
}

export interface CandidateSession {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: string;
  mode_label_zh: string;
  user: { id: string; email: string; display_name?: string | null };
}

export interface CandidateModeState {
  label_zh: string;
  session_minutes: number | null;
  role: string;
  accounts: CandidateAccount[];
}

function isAccount(value: unknown): value is CandidateAccount {
  if (!value || typeof value !== 'object') return false;
  const account = value as Record<string, unknown>;
  return typeof account.role === 'string' && typeof account.label_zh === 'string';
}

export function readCandidateMode(): CandidateModeState | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(CANDIDATE_MODE_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (typeof parsed.role !== 'string' || !parsed.role) return null;
    if (!Array.isArray(parsed.accounts) || !parsed.accounts.every(isAccount)) return null;
    if (!parsed.accounts.some((account) => account.role === parsed.role)) return null;
    return {
      label_zh:
        typeof parsed.label_zh === 'string' && parsed.label_zh
          ? parsed.label_zh
          : CANDIDATE_MODE_FALLBACK_LABEL,
      session_minutes:
        typeof parsed.session_minutes === 'number' ? parsed.session_minutes : null,
      role: parsed.role,
      accounts: parsed.accounts,
    };
  } catch {
    return null;
  }
}

export function writeCandidateMode(state: CandidateModeState): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(CANDIDATE_MODE_STORAGE_KEY, JSON.stringify(state));
}

export function clearCandidateMode(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(CANDIDATE_MODE_STORAGE_KEY);
}

/** The ordinary test account is the default entry when the profile lists it. */
export function preferredCandidateRole(accounts: CandidateAccount[]): string | null {
  if (accounts.some((account) => account.role === 'owner')) return 'owner';
  return accounts[0]?.role ?? null;
}
