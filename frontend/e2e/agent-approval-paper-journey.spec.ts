import { test, expect, type Page, type Request } from '@playwright/test';
import { loadRuntime, runtimeAvailable, TIER_B_SKIP, type Runtime } from './runtime';
import { approvalScopeOf, type ActivationStateLike } from '../src/lib/paperExecutionPanel';

/**
 * TIER B — journey 3: 本人在瀏覽器裡把一個已批准的產物，在自己的技能帳號上紙上執行一次。
 *
 * WHY THIS FILE EXISTS
 *   Round 21 shipped the approval door (`PUT .../activation`) and round 22 shipped the
 *   paper door (`POST .../paper-execution`), and both were tested where they live. What
 *   no layer covered is the middle: the screen between them. A reader of
 *   `/agent-strategy` could not tell that a job-only approval — `skill_unit_id: null`,
 *   which is exactly what round 21 sent — authorises *no* skill account, could not pick
 *   the account they meant, and could not see what the ledger said after the press.
 *   `tests/paperExecutionPanel.test.mjs` proves those rules in a Node harness; it is
 *   not proof that the mounted page reaches them. This file is that missing layer, and
 *   it is a *separate* approval item: nothing in cards A–D covers this journey, and no
 *   earlier browser result may stand in for it.
 *
 * TWO GROUPS, DIFFERENT COSTS (and the second one is off by default)
 *   READ group — one GET of the activation state, one GET of the ledger, and clicks that
 *     move only this browser's own state (which job is on screen, which radio is ticked).
 *     It writes nothing anywhere, so it can run against the owner's real account.
 *   WRITE group — it puts a row into one paper book, so it needs the owner to name the
 *     account allowed to receive it: E2E_PAPER_EXECUTION_SKILL. Unset → the write tests
 *     skip rather than pick an account for the owner. E2E_ACTIVATION_JOB names the
 *     artifact; unset, the READ group reviews the first job the page lists and the WRITE
 *     group skips, because a press needs a job the owner actually chose.
 *
 *   The write group executes inside a scope the owner already granted with their own
 *   hands: the paper door consumes the live approval's recorded scope and refuses
 *   anything it does not cover (`POST .../paper-execution`, backend/app/api/
 *   agent_strategy.py:444; body is `{skill_unit_id}` only, src/lib/api.ts:1181). What an
 *   automation must never do is *widen* that scope, so it never presses approve, never
 *   presses revoke, and never revokes by API either.
 *
 * WHAT THIS FILE NEVER DOES
 *   - It does not record an approval. `activation-approve` (page.tsx:1940) is the
 *     owner's own statement about one artifact; this file fills the form up to that
 *     button — ticks 「I have read…」, types the acknowledgement — and asserts the button
 *     is disabled before and enabled after, then leaves it unpressed and requires that no
 *     PUT left the browser.
 *   - It does not withdraw one. `activation-revoke` / `-revoke-confirm` (page.tsx:1962,
 *     :1991) and `POST .../activation/revoke` (api.ts:1164) are asserted to exist only.
 *     Withdrawing destroys the state that makes the write group meaningful, and it is
 *     the owner's act in both directions.
 *   - It does not touch the platform-wide consent (`consent-accept`, `consent-revoke`)
 *     or any other panel on the page.
 *   - No route is intercepted, stubbed or mocked. Every wait is `waitForResponse` or an
 *     expect-poll; there is no `waitForTimeout` in this file.
 *   - It does not create a skill account, a snapshot or a training job. If the account
 *     has none of those, the nodes skip with the reason the screen itself shows.
 *
 * WHAT THIS DOES NOT PROVE
 *   - that a paper decision is a good one; only that the row the ledger recorded is the
 *     row the screen prints, and that a press the page could not authorise never became a
 *     request.
 *   - anything about live trading. This lane is paper by construction
 *     (`runs_live_trading: False`, backend/app/services/agent_paper_execution.py:319).
 *   - PostgreSQL concurrency: one browser, one writer. Two simultaneous consent intents
 *     and the withdraw-vs-submit race are separate nodes on the PG lane and stay
 *     UNVERIFIED until they run there.
 *   - the sixth refusal code, `paper_execution_approval_revoked_midflight`. The panel
 *     translates it (`src/lib/paperExecutionPanel.ts`) and the door raises it
 *     (`services/agent_paper_execution.py:698-706`), but it needs a withdrawal to land
 *     between the re-check at :718 and the commit at :721 — one click cannot open that
 *     window. No green group in this file covers it; `TestRevokeVsSubmitOrdering` does.
 *   - the R21 job-only case is *not* covered by a green write group: the write group
 *     requires a scoped approval to exist, and skips otherwise. Job-only is asserted by
 *     the read group, which is where that state is visible.
 *
 * STATUS: 本輪授權「不啟動任何服務或瀏覽器」，這支 spec 尚未執行過一次。
 *   每個 selector 都摘自下列檔案的實際行號，不是猜的；第一次真正跑起來仍可能需要
 *   一次 selector 校正，在那之前它是一份待執行的驗收腳本，不是 browser 證據。
 *     the mount: src/app/agent-strategy/page.tsx:1244 `agent-strategy-page`
 *     jobs table: :2219 `select-job-<job_id>`（「On screen」/「Review this job」:2221）、
 *       :1723 `training-job`、:1720 `training-jobs-error`
 *     activation: :1755 `activation-panel`、:1759 `activation-notice`、
 *       :1761 `activation-loading`、:1765 `activation-read-error`、
 *       :1790 `activation-scope`、:1834 `activation-approve-form`、
 *       :1851 `approval-scope-choice`、:1860 `approval-scope-job-only`、
 *       :1871 `approval-scope-skill-unit`、:1879 `approval-skill-unit`、
 *       :1883 `approval-scope-sentence`、:1889 `approval-scope-error`、
 *       :1895 `activation-ack-text`、:1918 `activation-ack-checkbox`、
 *       :1930 `activation-ack-entry`、:1940 `activation-approve`、
 *       :1947 `activation-no-artifact`、:1955 `activation-revoke-block`、
 *       :1962 `activation-revoke`
 *     paper:      :2078 `paper-execution-panel`、:2098 `paper-skill-unit`、
 *       :2104 `paper-scope-notice`、:2106 `paper-notice`、:2108 `paper-read-error`、
 *       :2111 `paper-block-reason`、:2121 `paper-execute`、:2130 `paper-reload`、
 *       :2136 `paper-ledger-summary`、:2141 `paper-ledger-empty`、
 *       :2159 `paper-ledger-row-<execution_id>`
 *     the shared account picker: :481 `<select data-testid>`、:486 `-option-<id>`、
 *       :492 `-error`、:496 `-empty`
 *     the routes: src/lib/api.ts:1150 GET activation、:1158 PUT、:1164 POST revoke、
 *       :1181 POST paper-execution、:1187 GET paper-executions、
 *       :455 GET /api/v1/paper-portfolio（技能帳號清單）
 *     the payloads: backend/app/api/agent_strategy.py:306-321（回
 *       `{requirements, state}`）、:483-502（轉呼 services 的
 *       `list_paper_executions`，回 `{lane, count, executions,
 *       runs_live_trading}`＝agent_paper_execution.py:298-320）、app/models/agent_paper_execution.py:147-189（一列的欄位名）、
 *       app/services/model_activation.py:36-37（APPROVED／NOT_APPROVED）、
 *       :408-409（skill_unit_scope 只有 specific／none 兩種寫法）
 */

let runtime: Runtime;
test.skip(!runtimeAvailable(), TIER_B_SKIP);
test.beforeAll(() => {
  runtime = loadRuntime();
});

/** The disclosed reason a form can be visible yet unarmable (page.tsx:1911-1914). */
const ScreeningOff = /Screening is switched off in this process/;

/** The one account this run may write a paper row for, if the owner named one. */
const WRITING_SKILL = process.env.E2E_PAPER_EXECUTION_SKILL ?? '';
const TARGET_JOB = process.env.E2E_ACTIVATION_JOB ?? '';
const WRITE_SKIP =
  '寫入組需要 owner 親自指定：E2E_PAPER_EXECUTION_SKILL=<技能帳號 id> 與 E2E_ACTIVATION_JOB=<job id>。' +
  '兩者缺一就整組跳過——自動化不替本人決定哪一個帳號的紙上書本會被寫入，' +
  '也不替本人決定要對哪一個產物提執行。';

const ACTIVATION_PATH = (jobId: string) =>
  `/api/v1/agent-strategy/training/jobs/${jobId}/activation`;
const LEDGER_PATH = (jobId: string) =>
  `/api/v1/agent-strategy/training/jobs/${jobId}/paper-executions`;

/** Read-only oracle. Supplements the screen; never performs a product step. */
async function api(page: Page, path: string) {
  const token = await page.evaluate(() => localStorage.getItem('token'));
  if (!token) throw new Error('the UI session holds no token; login did not persist');
  const res = await page.request.fetch(`${runtime.backendBase}${path}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  });
  const text = await res.text();
  return { status: res.status(), json: text ? JSON.parse(text) : null, text };
}

/** Any non-GET that crosses the wire while a node is watching. */
function watchWrites(page: Page, pattern: RegExp) {
  const seen: string[] = [];
  const handler = (request: Request) => {
    if (request.method() !== 'GET' && pattern.test(request.url())) {
      seen.push(`${request.method()} ${request.url()}`);
    }
  };
  page.on('request', handler);
  return { seen, stop: () => page.off('request', handler) };
}

interface ActivationState extends ActivationStateLike {
  reason_code?: string | null;
  job_id?: string | null;
  bindings?: Record<string, boolean>;
  approved_at?: string | null;
}

interface Requirements {
  job_id?: string;
  artifact_checksum?: string | null;
  dataset_checksum?: string | null;
  approval_policy_version?: string | null;
  acknowledgement_text_version?: string | null;
  acknowledgement_text_zh?: string | null;
}

interface LedgerRow {
  execution_id: string;
  job_id: string;
  skill_unit_id: string;
  artifact_checksum: string;
  candidate_id?: string | null;
  execution_status: string;
  model_decision?: string | null;
  decision_record_id?: string | null;
  order_id?: string | null;
  fill_id?: string | null;
  blocked_reason?: string | null;
  signal_date?: string | null;
  quantity?: number | null;
  execution_price?: number | null;
  duplicate?: boolean;
}

// ── the scope oracle, kept out of the panel's own hands ────────────────────
//
// The panel answers with `approvalScopeOf()` and prints one of four sentences
// (page.tsx:1790-1797, 2102-2105). A spec that imported that function and required
// the DOM to equal its output would prove only that a module returns what it
// returns — round 18 named exactly this (`現有 spec 不得只用產品相同 helper
// 算答案、再拿答案驗證產品`). So the four categories below are derived here from the
// raw route fields, by hand, and the last node ties the two copies together on the
// seven shapes in `SHAPES` — a rename in the product then fails with its own
// sentence instead of quietly turning the DOM check into a tautology.

type Scope = 'no_approval' | 'job_only' | 'specific' | 'unreadable';

function scopeOfRawRead(state: ActivationState | null): Scope {
  if (!state) return 'unreadable';
  if (state.activation_status !== 'APPROVED') return 'no_approval';
  // APPROVED but no row named: nothing to point at, and the panel must not call
  // that a scope. `reason_code` is left out on purpose — it explains a refusal, it
  // does not widen a grant.
  if (!state.approval_id) return 'unreadable';
  if (state.skill_unit_scope === 'specific') {
    return state.skill_unit_id ? 'specific' : 'unreadable';
  }
  if (state.skill_unit_scope === 'none') return 'job_only';
  return 'unreadable';
}

/** What `activation-scope` is required to say for each category (page.tsx:1787-1797). */
const SCOPE_FINGERPRINT: Record<Scope, RegExp> = {
  specific: /綁定技能帳號 [^：]+：只有這個帳號可以拿這個產物紙上執行/,
  job_only: /job-only（skill_unit_id 為 NULL）：綁這個產物本身，不綁任何技能帳號/,
  no_approval: /沒有生效中的同意/,
  unreadable: /讀不到綁定範圍（讀不到不等於已綁定）/,
};

// What `paper-scope-notice` says for the three categories whose sentence does not
// depend on the account picker (paperExecutionPanel.ts:128-158). The `specific`
// category splits three ways over the current selection, so its fingerprint is
// spelled at the node that knows what is selected — a single regex that accepted
// all three would not be an oracle.
const SCOPE_NOTICE: Record<Exclude<Scope, 'specific'>, RegExp> = {
  no_approval: /沒有生效中的本人啟用同意，紙上執行會被整筆拒絕/,
  job_only: /生效中的同意是 job-only（skill_unit_id 為 NULL）/,
  unreadable: /讀不到這份同意的綁定範圍/,
};

/** The three `specific` sentences, keyed by what the picker holds (paperExecutionPanel.ts:160-182). */
const SPECIFIC_NOTICE = {
  nothingSelected: /還沒有在這個面板上明選要執行哪一個/,
  matches: /生效中的同意綁定的正是你選取的這個技能帳號/,
  otherAccount: /綁的是另一個技能帳號（id 後 8 碼/,
} as const;

/** The seven shapes the recorded approval can take, plus what each one means. */
const SHAPES: Array<{ key: string; state: ActivationState; scope: Scope }> = [
  { key: 'nothing read', state: null as unknown as ActivationState, scope: 'unreadable' },
  {
    key: 'no approval',
    state: { activation_status: 'NOT_APPROVED', reason_code: 'no_recorded_approval', approval_id: null },
    scope: 'no_approval',
  },
  {
    key: 'job-only',
    state: { activation_status: 'APPROVED', approval_id: 'a'.repeat(36), skill_unit_id: null, skill_unit_scope: 'none' },
    scope: 'job_only',
  },
  {
    key: 'scoped',
    state: { activation_status: 'APPROVED', approval_id: 'b'.repeat(36), skill_unit_id: '3'.repeat(36), skill_unit_scope: 'specific' },
    scope: 'specific',
  },
  {
    key: 'approved, no row',
    state: { activation_status: 'APPROVED', approval_id: null, skill_unit_scope: 'none' },
    scope: 'unreadable',
  },
  {
    key: 'specific without id',
    state: { activation_status: 'APPROVED', approval_id: 'c'.repeat(36), skill_unit_id: null, skill_unit_scope: 'specific' },
    scope: 'unreadable',
  },
  {
    key: 'unknown scope token',
    state: { activation_status: 'APPROVED', approval_id: 'd'.repeat(36), skill_unit_id: '4'.repeat(36), skill_unit_scope: 'all' },
    scope: 'unreadable',
  },
];

/** Open the page and put one job on screen, waiting for that job's own read. */
async function reviewJob(page: Page, jobId: string): Promise<ActivationState> {
  const button = page.getByTestId(`select-job-${jobId}`);
  await expect(button, `job ${jobId} is not listed for review`).toBeVisible({ timeout: 30000 });
  const [resp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(ACTIVATION_PATH(jobId)) && r.request().method() === 'GET',
      { timeout: 30000 },
    ),
    button.click(),
  ]);
  expect(resp.status(), 'the activation read answers for the job under review').toBe(200);
  const body = (await resp.json()) as { state?: ActivationState; requirements?: Requirements };
  await expect(page.getByTestId('activation-panel')).toBeVisible({ timeout: 30000 });
  await expect(page.getByTestId('activation-loading')).toHaveCount(0);
  return body.state as ActivationState;
}

/** The first job the page offers, or null when this account has trained nothing. */
async function firstJobOnScreen(page: Page): Promise<string | null> {
  const first = page.locator('[data-testid^="select-job-"]').first();
  if ((await page.locator('[data-testid^="select-job-"]').count()) === 0) return null;
  const testId = (await first.getAttribute('data-testid')) ?? '';
  return testId.replace('select-job-', '') || null;
}

/** The job under review: the owner's pick when one was named, else the first listed. */
async function jobToReview(page: Page): Promise<string | null> {
  return TARGET_JOB || (await firstJobOnScreen(page));
}

/** The rows one context is entitled to show, spelled out here from the raw payload. */
function rowsOfTriple(
  rows: LedgerRow[],
  jobId: string,
  skillUnitId: string,
  artifactChecksum: string
): LedgerRow[] {
  // A row missing any leg is unidentifiable and drops out of the count. The key is
  // the three legs joined, which is what the panel says it filters on
  // (page.tsx:2136-2139) — restated by hand so the DOM check is not the lib's echo.
  const want = `${jobId}|${skillUnitId}|${artifactChecksum}`;
  return rows.filter(
    (row) =>
      !!row.job_id &&
      !!row.skill_unit_id &&
      !!row.artifact_checksum &&
      `${row.job_id}|${row.skill_unit_id}|${row.artifact_checksum}` === want,
  );
}

/** Every ledger row currently on screen, whatever context it claims to belong to. */
function renderedRows(page: Page) {
  return page.locator('[data-testid^="paper-ledger-row-"]');
}

/** The ledger as the formal read gives it, filtered the way the screen claims to. */
async function ledgerOf(page: Page, jobId: string) {
  const { status, json } = await api(page, LEDGER_PATH(jobId));
  expect(status, 'the ledger read must answer for the oracle').toBe(200);
  return {
    count: Number(json?.count ?? -1),
    rows: (json?.executions ?? []) as LedgerRow[],
  };
}

test.describe('journey 3: the agent paper lane shows the approval it is standing on', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;
  let jobId = '';
  let state: ActivationState;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
    await page.goto(`${runtime.frontendBase}/agent-strategy`);
    await page.getByTestId('agent-strategy-page').waitFor({ state: 'visible', timeout: 30000 });
  });

  test.afterAll(async () => {
    await page?.close();
  });

  test.describe('READ group — nothing leaves the browser', () => {
    test('the panel prints the approval record the activation route returned', async () => {
      const listed = await jobToReview(page);
      test.skip(listed === null, '這個帳號目前沒有任何已記錄的訓練 job：沒有產物可審閱，也沒有同意可讀。');
      jobId = listed as string;
      state = await reviewJob(page, jobId);

      const panel = page.getByTestId('activation-panel');
      await expect(panel, 'the status the route named is the status on screen').toContainText(
        String(state.activation_status),
      );
      await expect(panel).toContainText(String(state.reason_code ?? '—'));
      if (state.approval_id) {
        await expect(panel).toContainText(state.approval_id);
      } else {
        await expect(panel).toContainText('none recorded for this artifact');
      }

      const oracle = scopeOfRawRead(state);
      await expect(page.getByTestId('activation-scope')).toContainText(SCOPE_FINGERPRINT[oracle]);
      // A job-only grant must never be worded as permission for the selected account.
      if (oracle === 'job_only') {
        const scopeText = await page.getByTestId('activation-scope').innerText();
        expect(scopeText).not.toMatch(/只有這個帳號可以拿這個產物紙上執行/);
      }

      // The read is scoped to the artifact on screen: a foreign job id in the panel
      // would mean a late answer about another job overwrote this one.
      const { json } = await api(page, ACTIVATION_PATH(jobId));
      expect((json?.state as ActivationState)?.job_id ?? jobId).toBe(jobId);
    });

    test('the approval form binds the checksum the route named and no PUT leaves while it is filled', async () => {
      test.skip(!jobId, 'no job under review');
      const { json } = await api(page, ACTIVATION_PATH(jobId));
      const requirements = (json?.requirements ?? {}) as Requirements;
      const current = (json?.state ?? null) as ActivationState | null;
      test.skip(
        current?.activation_status === 'APPROVED',
        '這個產物已有生效中的同意：表單不在畫面上，本節不重複讀它。',
      );
      test.skip(
        !requirements.artifact_checksum,
        '這個 job 沒有記錄產物雜湊，頁面不開放批准（`activation-no-artifact` 是它自己的節點）。',
      );

      const writes = watchWrites(page, /\/activation(\?|$)/);
      const form = page.getByTestId('activation-approve-form');
      await expect(form).toBeVisible({ timeout: 30000 });
      await expect(form).toContainText(String(requirements.artifact_checksum));
      await expect(page.getByTestId('activation-ack-text')).toContainText(
        String(requirements.acknowledgement_text_zh),
      );

      const approve = page.getByTestId('activation-approve');
      // The gate, in both directions: nothing read, nothing typed → disabled; the
      // owner's own tick and the owner's own typing → enabled. Stopping there.
      await expect(approve).toBeDisabled();
      await page.getByTestId('activation-ack-checkbox').check();
      await expect(approve, 'reading the box alone must not arm the button').toBeDisabled();
      await page
        .getByTestId('activation-ack-entry')
        .fill(String(requirements.acknowledgement_text_zh));
      const armed = await approve.isEnabled();
      if (!armed) {
        // Screening off in this process is a real, disclosed reason (page.tsx:1911-1914).
        const panel = await form.innerText();
        expect(panel).toMatch(ScreeningOff);
      } else {
        await expect(approve).toBeEnabled();
      }
      expect(writes.seen, `no approval may be recorded by this spec: ${writes.seen.join(', ')}`).toEqual([]);
      writes.stop();
    });

    test('choosing 「bind this skill account」 without an account is refused on the spot', async () => {
      test.skip(!jobId, 'no job under review');
      const { json } = await api(page, ACTIVATION_PATH(jobId));
      const current = (json?.state ?? null) as ActivationState | null;
      const requirements = (json?.requirements ?? {}) as Requirements;
      test.skip(
        current?.activation_status === 'APPROVED' || !requirements.artifact_checksum,
        '批准表單不在畫面上（已有同意，或沒有可綁定的產物雜湊）。',
      );

      const writes = watchWrites(page, /\/activation(\?|$)/);
      await page.getByTestId('approval-scope-skill-unit').check();
      await expect(page.getByTestId('approval-scope-choice')).toBeVisible();
      const options = await page.getByTestId('approval-skill-unit').locator('option').count();
      if (options <= 1) {
        // No account of their own to bind: the picker says so and does not invent one.
        await expect(page.getByTestId('approval-skill-unit-empty')).toBeVisible();
      } else {
        await expect(page.getByTestId('approval-scope-error')).toBeVisible();
        await expect(page.getByTestId('approval-scope-error')).toContainText(
          '這頁不會替你補一個',
        );
        await expect(page.getByTestId('activation-approve')).toBeDisabled();
        await page.getByTestId('approval-skill-unit').selectOption({ index: 1 });
        await expect(page.getByTestId('approval-scope-error')).toHaveCount(0);
        await expect(page.getByTestId('approval-scope-sentence')).toContainText(
          '只有這個帳號可以拿這個產物做紙上執行',
        );
      }
      await page.getByTestId('approval-scope-job-only').check();
      await expect(page.getByTestId('approval-scope-sentence')).toContainText(
        '不授權任何技能帳號做紙上執行',
      );
      expect(writes.seen, 'a scope statement is not a request: ' + writes.seen.join(', ')).toEqual([]);
      writes.stop();
    });

    test('the execute control agrees with the recorded scope, sentence and gate together', async () => {
      test.skip(!jobId, 'no job under review');
      const { json } = await api(page, ACTIVATION_PATH(jobId));
      const current = (json?.state ?? null) as ActivationState | null;
      const oracle = scopeOfRawRead(current);

      const panel = page.getByTestId('paper-execution-panel');
      test.skip(
        (await panel.count()) === 0,
        `這個 job 的紙上執行面板不在畫面上（需要 activation 讀到、且 job.status 為 completed）。`,
      );
      const notice = page.getByTestId('paper-scope-notice');
      const execute = page.getByTestId('paper-execute');
      if (oracle !== 'specific') {
        await expect(notice).toContainText(SCOPE_NOTICE[oracle]);
        // The round-23 gap, asserted on the screen: a job-only approval — and every
        // other non-scoped reading — must not be pressable for any account.
        await expect(execute, `scope ${oracle} must not offer a press`).toBeDisabled();
        await expect(page.getByTestId('paper-block-reason')).toBeVisible();
        if (oracle === 'job_only') {
          await expect(page.getByTestId('paper-block-reason')).toContainText('job-only');
          await expect(notice).toContainText('不代表任何技能帳號可以據此紙上執行');
          // The one legal disposal, and it is the owner's two presses, not this page's.
          await expect(notice).toContainText('由你本人先撤回');
          await expect(notice).toContainText('這頁不會代你撤回、不會替換、也不會把範圍放大');
        }
        if (oracle === 'no_approval') {
          await expect(page.getByTestId('paper-block-reason')).toContainText(
            '還沒有生效中的本人啟用同意',
          );
          await expect(notice).toContainText('請先在上方讀過啟用全文');
        }
        if (oracle === 'unreadable') {
          await expect(page.getByTestId('paper-block-reason')).toContainText('讀不到');
          await expect(notice).toContainText('讀不到不等於已綁定這個技能帳號');
        }
        return;
      }
      // A scoped approval: the gate is the account picker, and it starts empty.
      const options = await page.getByTestId('paper-skill-unit').locator('option').count();
      const selected = await page.getByTestId('paper-skill-unit').inputValue();
      if (selected === '') {
        await expect(notice).toContainText(SPECIFIC_NOTICE.nothingSelected);
        await expect(execute).toBeDisabled();
        await expect(page.getByTestId('paper-block-reason')).toContainText('還沒有明選');
        test.skip(
          options < 2,
          '這個帳號底下沒有任何技能帳號可選：面板不會代本人建立一個，本節無從繼續對照。',
        );
        await page.getByTestId('paper-skill-unit').selectOption({ index: 1 });
      }
      const nowSelected = await page.getByTestId('paper-skill-unit').inputValue();
      if (nowSelected === current?.skill_unit_id) {
        await expect(notice).toContainText(SPECIFIC_NOTICE.matches);
        await expect(execute).toBeEnabled();
      } else {
        await expect(notice).toContainText(SPECIFIC_NOTICE.otherAccount);
        await expect(execute, 'an account the approval does not cover stays shut').toBeDisabled();
        await expect(page.getByTestId('paper-block-reason')).toContainText('另一個技能帳號');
      }
    });

    test('the ledger table is the filtered read, with the route’s own two numbers', async () => {
      test.skip(!jobId, 'no job under review');
      test.skip(
        (await page.getByTestId('paper-execution-panel').count()) === 0,
        '紙上執行面板不在畫面上，沒有 ledger 可對。',
      );
      const { count, rows } = await ledgerOf(page, jobId);
      const selected = await page.getByTestId('paper-skill-unit').inputValue().catch(() => '');
      const { json } = await api(page, ACTIVATION_PATH(jobId));
      // The screen builds its context from the requirements the route returned
      // (page.tsx:829-836), not from the approval row, so the oracle reads the same leg.
      const requirements = (json?.requirements ?? {}) as Requirements;
      const artifact = requirements.artifact_checksum ?? '';

      // The triple the summary says it filters on, written out here from the raw
      // rows rather than taken from the panel's own helper.
      const mine = rowsOfTriple(rows, jobId, selected, artifact);
      const rendered = renderedRows(page);
      await expect(rendered, 'rows outside this triple must not render').toHaveCount(mine.length);
      for (const row of mine) {
        await expect(page.getByTestId(`paper-ledger-row-${row.execution_id}`)).toBeVisible();
      }
      if (mine.length === 0) {
        await expect(page.getByTestId('paper-ledger-empty')).toContainText('0 列');
      }
      const summary = await page.getByTestId('paper-ledger-summary').innerText();
      // The first number is the page's own `paperRows.length`; the route's `count`
      // is checked against the array it was computed from, not against the DOM.
      expect(count, 'the route counts the rows it returned').toBe(rows.length);
      expect(summary).toContain(`共 ${rows.length} 列`);
      expect(summary).toContain(`${mine.length} 列`);
      // Foreign rows are counted out loud rather than hidden inside the same number.
      if (rows.length !== mine.length) {
        expect(summary).toContain('其他帳號或其他產物的列不列入下方');
      }
      for (const row of mine) {
        const cell = page.getByTestId(`paper-ledger-row-${row.execution_id}`);
        if (row.execution_status === 'executed') {
          await expect(cell).toContainText('已執行');
        } else if (row.execution_status === 'skipped') {
          // A recorded skip names the legs that are absent, instead of hiding them.
          await expect(cell).toContainText('沒有進場、沒有 order、沒有 fill');
        } else if (row.execution_status === 'blocked') {
          await expect(cell).toContainText('被擋下');
          if (row.blocked_reason) {
            await expect(cell).toContainText(`blocked_reason：${row.blocked_reason}`);
          }
        } else {
          await expect(cell).toContainText('執行狀態讀不懂');
        }
        // Every leg is named as present or absent, in the row itself.
        await expect(cell).toContainText('order：');
        await expect(cell).toContainText('fill：');
      }
    });

    test('the two readings of a scope agree on every shape the record can take', () => {
      // The tie-back node: the panel's function and this file's hand-written table
      // must not drift. A disagreement is a finding in itself, whichever side moved.
      for (const shape of SHAPES) {
        expect(scopeOfRawRead(shape.state), shape.key).toBe(shape.scope);
        expect(approvalScopeOf(shape.state as ActivationStateLike), `product copy on ${shape.key}`).toBe(
          shape.scope,
        );
      }
    });
  });

  test.describe('WRITE group — one press, one row, named by the owner', () => {
    // Skip only while the owner has not named the account and the artifact. An
    // unconditional `test.skip(true, …)` here would make this group unrunnable
    // even after both are set, which is a placeholder, not a gate.
    test.skip(WRITING_SKILL === '' || TARGET_JOB === '', WRITE_SKIP);

    test('one press sends the named account and the screen prints the row the ledger read back', async () => {
      test.skip(!TARGET_JOB, '沒有指定 E2E_ACTIVATION_JOB：不替你選一個產物去執行。');
      state = await reviewJob(page, TARGET_JOB);
      const scope = scopeOfRawRead(state);
      test.skip(
        scope !== 'specific' || state.skill_unit_id !== WRITING_SKILL,
        `這個產物的生效同意不是綁定 ${WRITING_SKILL} 的 scoped 同意（實際判讀：${scope}）。` +
          '要先由本人在螢幕前提交一次綁定這個帳號的同意——那是本檔案永遠不會按的按鈕。',
      );

      await page.getByTestId('paper-skill-unit').selectOption(WRITING_SKILL);
      const { json: act } = await api(page, ACTIVATION_PATH(TARGET_JOB));
      const artifactOf = ((act?.requirements ?? {}) as Requirements).artifact_checksum ?? '';
      const before = await ledgerOf(page, TARGET_JOB);
      // The gate the press has to pass on its way out, checked before it is pressed:
      // a disabled control means the click below never reaches the network. This is
      // the positive control for 「合法 scoped 可執行」.
      await expect(page.getByTestId('paper-execute')).toBeEnabled();
      const [resp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().endsWith('/paper-execution') && r.request().method() === 'POST',
          { timeout: 120000 },
        ),
        page.getByTestId('paper-execute').click(),
      ]);
      const sent = resp.request().postDataJSON() as Record<string, unknown>;
      expect(sent, 'the body names the account and nothing else').toEqual({
        skill_unit_id: WRITING_SKILL,
      });

      const notice = page.getByTestId('paper-notice');
      await expect(notice).toBeVisible({ timeout: 60000 });
      const status = resp.status();
      if (status === 409 || status === 404) {
        // A refusal keeps the server's own code on screen; it is never softened here.
        const detail = (await resp.json().catch(() => null)) as {
          detail?: { code?: string; details?: { execution_id?: string | null } };
        } | null;
        const code = detail?.detail?.code ?? '';
        expect(code, 'a refusal must arrive with its code').not.toBe('');
        await expect(notice).toContainText(code);
        const after = await ledgerOf(page, TARGET_JOB);
        // Two different refusals, and the door tells them apart by itself: one that
        // committed a durable `blocked` row before raising — services/agent_paper_execution.py:721
        // is the commit, :742-755 the raise (the cash / risk / fill legs) — names its
        // `execution_id` in `details`; a refusal raised at the scope door (:357-368,
        // through the :150 helper) happens before any row is added, so it writes
        // nothing at all.
        const booked = detail?.detail?.details?.execution_id ?? null;
        if (booked) {
          expect(after.rows.length, 'a durable refusal books exactly one row').toBe(
            before.rows.length + 1,
          );
          const blocked = after.rows.find((r) => r.execution_id === booked);
          expect(blocked, 'the refusal names a row the formal read returns').toBeTruthy();
          expect(blocked?.execution_status).toBe('blocked');
          await expect(page.getByTestId(`paper-ledger-row-${booked}`)).toBeVisible();
        } else {
          expect(after.rows.length, 'a refusal that names no execution_id booked nothing').toBe(
            before.rows.length,
          );
          const bookedRows = rowsOfTriple(after.rows, TARGET_JOB, WRITING_SKILL, artifactOf);
          await expect(
            renderedRows(page),
            'the screen shows the rows this context has, and no row this press wrote',
          ).toHaveCount(bookedRows.length);
        }
        return;
      }
      expect([200, 201]).toContain(status);

      const body = (await resp.json()) as { execution_id?: string } & LedgerRow;
      const executionId = body.execution_id ?? null;
      expect(executionId, 'the door names the row it wrote').toBeTruthy();
      // The sentence on screen comes from the formal GET, not from this echo: the row
      // must be in the read, and the read is what the node below re-checks.
      const after = await ledgerOf(page, TARGET_JOB);
      const row = after.rows.find((r) => r.execution_id === executionId);
      expect(row, 'the press is only evidence of an intent until the ledger answers').toBeTruthy();
      await expect(page.getByTestId(`paper-ledger-row-${executionId}`)).toBeVisible();
      const text = await notice.innerText();
      if (row?.execution_status === 'executed') {
        expect(text).toContain('已執行');
      } else if (row?.execution_status === 'skipped') {
        expect(text).toContain('沒有進場、沒有 order、沒有 fill');
      } else if (row?.execution_status === 'blocked') {
        expect(text).toContain('被擋下');
      } else {
        expect(text).toContain('執行狀態讀不懂');
      }
      // The echo and the read are the same row, or the screen narrates the wrong one.
      expect(row?.candidate_id, 'the read names the candidate the echo named').toBe(
        body.candidate_id,
      );
      expect(row?.job_id).toBe(TARGET_JOB);
      expect(row?.skill_unit_id).toBe(WRITING_SKILL);
      // Whatever the outcome, the legs are named as present or absent — never implied.
      expect(text).toMatch(/order：(無|[0-9a-f-]{8})/);
      expect(text).toMatch(/fill：(無|[0-9a-f-]{8})/);
    });

    test('a second press of the same intent adds no second row', async () => {
      test.skip(!TARGET_JOB, '沒有指定 E2E_ACTIVATION_JOB。');
      const { rows } = await ledgerOf(page, TARGET_JOB);
      const mine = rows.filter(
        (row) => row.job_id === TARGET_JOB && row.skill_unit_id === WRITING_SKILL,
      );
      test.skip(mine.length === 0, '上一節沒有留下任何本上下文的列（被拒或跳過），本節無從比較。');
      // The durable intent is (skill_unit_id, job_id, candidate_id), which is what
      // the unique constraint and the pre-insert lookup both use
      // (models/agent_paper_execution.py:135-141, services/agent_paper_execution.py:409-422).
      const seen = new Set(mine.map((row) => row.execution_id));
      const before = mine.length;

      const [resp] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().endsWith('/paper-execution') && r.request().method() === 'POST',
          { timeout: 120000 },
        ),
        page.getByTestId('paper-execute').click(),
      ]);
      expect(resp.request().postDataJSON()).toEqual({ skill_unit_id: WRITING_SKILL });
      await expect(page.getByTestId('paper-notice')).toBeVisible({ timeout: 60000 });

      const after = (await ledgerOf(page, TARGET_JOB)).rows.filter(
        (row) => row.job_id === TARGET_JOB && row.skill_unit_id === WRITING_SKILL,
      );
      const status = resp.status();
      if (status !== 200 && status !== 201) {
        const detail = (await resp.json().catch(() => null)) as {
          detail?: { code?: string };
        } | null;
        expect(detail?.detail?.code ?? '', 'a refusal arrives with its code').not.toBe('');
        await expect(page.getByTestId('paper-notice')).toContainText(String(detail?.detail?.code));
        // Whatever the door answered, the invariant holds: no intent holds two rows.
        const candidateIds = after.map((row) => String(row.candidate_id));
        expect(
          candidateIds.filter((id, index) => candidateIds.indexOf(id) !== index),
          'uq_agent_paper_execution_intent: 同一 candidate 在本上下文只容一列',
        ).toEqual([]);
        return;
      }
      const echo = (await resp.json()) as { duplicate?: boolean } & LedgerRow;
      const sameIntent = after.filter((row) => row.candidate_id === echo.candidate_id);
      expect(
        sameIntent.length,
        '一筆 candidate 在帳本裡只容一列（uq_agent_paper_execution_intent）',
      ).toBe(1);
      if (seen.has(sameIntent[0].execution_id)) {
        // The repeat was answered by the row that was already there.
        expect(echo.duplicate, 'the door calls a repeat a repeat').toBe(true);
        expect(after.length, '重送已結算的意圖，帳本列數不變').toBe(before);
        await expect(page.getByTestId('paper-notice')).toContainText('這次沒有多記一筆');
      } else {
        // A newer candidate landed between the two reads: that is a different intent,
        // and the honest answer is one more row — not a second row for the old one.
        expect(echo.duplicate, 'a row this press wrote is not a repeat').toBe(false);
        expect(after.length, 'a fresh candidate is a different intent').toBe(before + 1);
        expect(sameIntent[0].execution_id, 'the new row is the one this press opened').toBe(
          echo.execution_id,
        );
      }
    });

    test('a press on an account the approval does not cover never becomes a request', async () => {
      test.skip(!TARGET_JOB, '沒有指定 E2E_ACTIVATION_JOB。');
      state = await reviewJob(page, TARGET_JOB);
      test.skip(scopeOfRawRead(state) !== 'specific', '生效同意不是 scoped 的，本節要的是兩個帳號的對照。');
      const picker = page.getByTestId('paper-skill-unit');
      const values = await picker.locator('option').evaluateAll((nodes) =>
        nodes.map((node) => (node as HTMLOptionElement).value)
      );
      const other = values.find((value) => value && value !== state.skill_unit_id);
      test.skip(
        !other,
        '這個帳號名下除了被綁定的那一個，沒有其他技能帳號可明選，無從做不被覆蓋的對照。',
      );

      const writes = watchWrites(page, /\/paper-execution(\?|$)/);
      await picker.selectOption(other as string);
      await expect(page.getByTestId('paper-execute')).toBeDisabled();
      await expect(page.getByTestId('paper-block-reason')).toContainText('另一個技能帳號');
      await expect(page.getByTestId('paper-scope-notice')).toContainText(SPECIFIC_NOTICE.otherAccount);
      // Changing the account invalidates the previous press's narration: what the
      // other account did is not this account's result.
      await expect(
        page.getByTestId('paper-notice'),
        '換帳號後，舊結果不再出現在這頁',
      ).toHaveCount(0);
      // Even a forced click cannot get out: the control is disabled, and the
      // handler re-checks the gate before it builds a request.
      await page.getByTestId('paper-execute').click({ force: true }).catch(() => undefined);
      expect(writes.seen, 'nothing may reach the door: ' + writes.seen.join(', ')).toEqual([]);
      await expect(page.getByTestId('paper-notice')).toHaveCount(0);
      writes.stop();
      // Put the covered account back so the ledger nodes below read the same context.
      await picker.selectOption(state.skill_unit_id as string);
    });

    test('重新讀回 ledger 不重送意圖', async () => {
      test.skip(!TARGET_JOB, '沒有指定 E2E_ACTIVATION_JOB。');
      const writes = watchWrites(page, /\/paper-execution/);
      const before = await ledgerOf(page, TARGET_JOB);
      await page.getByTestId('paper-reload').click();
      await page
        .waitForResponse(
          (r) => r.url().includes(LEDGER_PATH(TARGET_JOB)) && r.request().method() === 'GET',
          { timeout: 30000 },
        )
        .catch(() => undefined);
      const after = await ledgerOf(page, TARGET_JOB);
      expect(after.rows.length, 'a reload reads, it does not run').toBe(before.rows.length);
      expect(writes.seen, 'the reload button opens no intent: ' + writes.seen.join(', ')).toEqual([]);
      writes.stop();
    });
  });
});
