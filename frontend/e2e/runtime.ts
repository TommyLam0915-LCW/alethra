import { readFileSync } from 'fs';

/**
 * One tier-B runtime loader instead of eleven copies of it.
 *
 * WHY IT EXISTS: each tier-B spec used to call its own `loadRuntime()` at
 * module scope, and that call threw when `E2E_EMAIL` / `E2E_PASSWORD` were
 * absent. Playwright aborts *listing* the whole suite if any spec throws while
 * loading, so `playwright test --list` reported "0 tests in 0 files" for all
 * 32 specs — the 11 tier-B files made the other 21 invisible, and the e2e lane
 * could not be enumerated on a machine without the Docker stack, let alone run.
 *
 * Splitting "is the runtime there?" (collection time, non-fatal) from "give me
 * the runtime" (run time, fatal) keeps the refusal strict: a tier-B test that
 * actually executes still refuses to invent credentials, it just reports a skip
 * with a reason when the stack is not configured.
 */

export interface Runtime {
  frontendBase: string;
  backendBase: string;
  email: string;
  password: string;
}

export const TIER_B_SKIP =
  'tier B needs the real stack: E2E_EMAIL / E2E_PASSWORD are absent. ' +
  'Start it with start_e2e_research_flow.sh (Docker) or the wheel stack in ' +
  'docs/approval-package-2026-11-16-full-stack-execution.md and pass ' +
  'E2E_CRED_FILE=.local/e2e-credentials.env.';

function credentialMap(): Record<string, string> {
  const fromFile: Record<string, string> = {};
  const credFile = process.env.E2E_CRED_FILE;
  if (credFile) {
    for (const line of readFileSync(credFile, 'utf-8').split('\n')) {
      const match = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
      if (match) fromFile[match[1]] = match[2];
    }
  }
  return fromFile;
}

function pickFrom(fromFile: Record<string, string>, key: string): string | undefined {
  return process.env[key] ?? fromFile[key];
}

/** Collection-time question: never throws, so a missing stack cannot hide other specs. */
export function runtimeAvailable(): boolean {
  const fromFile = credentialMap();
  return Boolean(pickFrom(fromFile, 'E2E_EMAIL') && pickFrom(fromFile, 'E2E_PASSWORD'));
}

/** Run-time question: the refusal stays fatal, because a tier-B test that runs
 *  without credentials would be asserting against a stack nobody started. */
export function loadRuntime(): Runtime {
  const fromFile = credentialMap();
  const email = pickFrom(fromFile, 'E2E_EMAIL');
  const password = pickFrom(fromFile, 'E2E_PASSWORD');
  if (!email || !password) {
    throw new Error('Missing E2E_EMAIL / E2E_PASSWORD. Set E2E_CRED_FILE to .local/e2e-credentials.env.');
  }
  return {
    frontendBase: pickFrom(fromFile, 'E2E_FRONTEND_BASE') ?? 'http://localhost:3099',
    backendBase: pickFrom(fromFile, 'E2E_BACKEND_BASE') ?? 'http://127.0.0.1:8099',
    email,
    password,
  };
}
