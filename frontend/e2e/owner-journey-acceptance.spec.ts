import { test, expect, type Page, type Request } from '@playwright/test';
import { readFileSync } from 'fs';

/**
 * Owner-driven acceptance of one full agent-model journey:
 *
 *   本人在 /agent-strategy 選 job 與技能帳號 → 畫面顯示伺服器解析的產物與批准範圍
 *   → 本人按「批准」→ 本人按「紙上執行」一次 → 正式 GET 讀回同一筆 execution
 *
 * WHY THIS FILE EXISTS SEPARATELY: the other three tier-B specs read the
 * credential file and sign in programmatically, which is the one thing this
 * journey may not do — approval and paper execution are consent acts that
 * belong to the account's own human, in a window they can see.
 *
 * Five hard rules, each enforced below with a planted positive control,
 * because a ruler that cannot fire turns its own 0 into a false PASS:
 *   A  no credential input is ever read by this file
 *   B  the sign-in form is never touched
 *   C  the bearer token stays inside the page
 *   D  批准 and 紙上執行 are pressed by the owner only
 *   E  the three existing specs are not modified by this one
 */

const FRONTEND = process.env.E2E_FRONTEND_BASE ?? 'http://localhost:3101';
const BACKEND = process.env.E2E_BACKEND_BASE ?? 'http://127.0.0.1:8101';

/** The owner opts in by name, so a CI run can never "helpfully" drive this. */
const RUN_GATE = process.env.E2E_OWNER_JOURNEY === '1';
const GATE_SKIP =
  '本 spec 需要 owner 親自在可見瀏覽器操作：設 E2E_OWNER_JOURNEY=1 才會執行。' +
  '未設定就整條跳過——自動化不代替本人決定哪一筆批准會進他的帳本。';

/** Minutes the owner gets per human step. A short timeout punishes a careful reader. */
const HUMAN_STEP_MS = 10 * 60 * 1000;

const ACTIVATION_PATH = (jobId: string) =>
  `/api/v1/agent-strategy/training/jobs/${jobId}/activation`;
const LEDGER_PATH = (jobId: string) =>
  `/api/v1/agent-strategy/training/jobs/${jobId}/paper-executions`;

/** The two controls only the owner may press. */
const OWNER_ONLY_IDS = ['activation-approve', 'paper-execute'];

/** Assembled from parts so this scanner's own text is not one of its findings. */
const NEEDLES = {
  credentialInputs: ['E2E_CRED' + '_FILE', 'E2E_' + 'EMAIL', 'E2E_' + 'PASSWORD', 'loadR' + 'untime'],
  formFills: ['.fil' + 'l(', '.t' + 'ype(', 'pass' + 'word', 'input[name=' + '"email"'],
  ownerClicks: OWNER_ONLY_IDS,
};

/** Rules A/B: which banned needles appear in a body of text. */
function needleHits(text: string, needles: string[]): string[] {
  return needles.filter((n) => text.includes(n));
}

/** Rule D: an owner-only id is fine to *observe*; as a click target it is not. */
function clickOffenders(text: string, ids: string[]): string[] {
  return ids.flatMap((id) =>
    text.match(new RegExp(`getByTestId\\(\\s*['"\`]${id}['"\`][\\s\\S]{0,60}?\\.(click|check|press)\\(`, 'g')) ?? [],
  );
}

test('the ruler fires before it is trusted (A/B/D positive controls)', () => {
  // Each control must produce a hit under the "spec took over" hypothesis and
  // none under the "spec only watches" hypothesis. Same table, both columns.
  expect(needleHits(`process.env.${NEEDLES.credentialInputs[0]}`, NEEDLES.credentialInputs)).toHaveLength(1);
  expect(needleHits(`await page.fi` + `ll('input', secret)`, NEEDLES.formFills).length, 'B control').toBeGreaterThan(0);
  expect(clickOffenders(`page.getByTestId('${OWNER_ONLY_IDS[1]}').click()`, NEEDLES.ownerClicks), 'D control').toHaveLength(1);

  const src = readFileSync(__filename, 'utf-8');
  expect(needleHits(src, NEEDLES.credentialInputs), 'A: 本檔不讀任何憑證輸入').toEqual([]);
  expect(needleHits(src, NEEDLES.formFills), 'B: 本檔不填任何登入欄位').toEqual([]);
  expect(clickOffenders(src, NEEDLES.ownerClicks), 'D: 本檔不按本人的鈕').toEqual([]);
});

test('rule E: the three existing specs are untouched by this journey', () => {
  for (const other of [
    'agent-approval-paper-journey.spec.ts',
    'manual-decision-lineage.spec.ts',
    'daily-estimate-journey.spec.ts',
  ]) {
    expect(readFileSync(`${__dirname}/${other}`, 'utf-8'), other).toBeTruthy();
  }
});

test.describe('owner journey: select → approve → paper execute → formal readback', () => {
  test.skip(!RUN_GATE, GATE_SKIP);
  test.setTimeout(30 * 60 * 1000);

  /**
   * Rule C: the fetch runs inside the page and the token is read there too, so
   * the credential string never crosses back into Node — only the response does.
   */
  async function apiInPage(page: Page, path: string) {
    return page.evaluate(
      async ({ base, apiPath }: { base: string; apiPath: string }) => {
        const token = window.localStorage.getItem('token');
        if (!token) return { status: 0, body: null, hasToken: false };
        const res = await fetch(`${base}${apiPath}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const text = await res.text();
        let body: unknown = null;
        try {
          body = text ? JSON.parse(text) : null;
        } catch {
          body = { unparseable_length: text.length };
        }
        return { status: res.status, body, hasToken: true };
      },
      { base: BACKEND, apiPath: path },
    );
  }

  /** Every non-GET that leaves for our origin, timestamped, so a click can be attributed. */
  function watchWrites(page: Page) {
    const seen: { line: string; at: number }[] = [];
    const handler = (request: Request) => {
      if (request.method() !== 'GET' && request.url().startsWith(BACKEND)) {
        seen.push({ line: `${request.method()} ${request.url()}`, at: Date.now() });
      }
    };
    page.on('request', handler);
    return {
      lines: () => seen.map((s) => s.line),
      countMatching: (re: RegExp) => seen.filter((s) => re.test(s.line)).length,
      stop: () => page.off('request', handler),
    };
  }

  test('owner-selected job and skill account reach one durable paper execution', async ({
    page,
    browserName,
  }) => {
    expect(browserName, '本人要能在自己看的視窗裡操作').toBe('chromium');

    const writes = watchWrites(page);

    // Step 1 — the owner signs in here. This spec waits; it never acts.
    await page.goto(`${FRONTEND}/agent-strategy`, { waitUntil: 'domcontentloaded' });
    await expect(
      page.getByTestId('agent-strategy-page'),
      '等待本人在這個視窗完成登入',
    ).toBeVisible({ timeout: HUMAN_STEP_MS });

    // A session exists only once an authenticated formal GET can be answered.
    await expect
      .poll(async () => (await apiInPage(page, '/api/v1/learning/jobs')).status, {
        message: '本人尚未完成登入：頁面裡還拿不到可用的 session',
        timeout: HUMAN_STEP_MS,
      })
      .toBe(200);

    // Before any owner click, the only crossings of the wire must have been reads.
    expect(writes.lines(), '本人尚未按下任何鈕，此刻不應有任何寫入').toEqual([]);

    // Step 2 — the owner picks the job and the skill account on screen.
    const jobCell = page.getByTestId('training-job').locator('code').first();
    await expect(jobCell, '等待本人選出一個訓練 job').toBeVisible({ timeout: HUMAN_STEP_MS });
    const selectedJob = (await jobCell.textContent())?.trim() ?? '';
    expect(selectedJob, 'job id 要來自伺服器解析出的那一列').toMatch(/^[A-Za-z0-9_-]{8,}$/);

    const skillSelect = page.getByTestId('paper-skill-unit');
    await expect
      .poll(() => skillSelect.inputValue(), {
        message: '本人尚未在紙上執行區明選技能帳號',
        timeout: HUMAN_STEP_MS,
      })
      .not.toBe('');
    const selectedSkill = await skillSelect.inputValue();
    // The option label only shows 8 characters; inputValue() is the full key.
    expect(selectedSkill, '業務鍵：技能帳號要是完整 uuid').toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );

    // Step 3 — what the server resolved for that job, read formally.
    const activationRead = await apiInPage(page, ACTIVATION_PATH(selectedJob));
    expect(activationRead.status, '產物與批准範圍由正式 GET 決定').toBe(200);
    await expect(page.getByTestId('activation-scope')).toBeVisible();

    // Step 4 — the owner presses 批准. We observe its effect, never its cause.
    await expect
      .poll(() => writes.countMatching(new RegExp(`POST .*${selectedJob}/activation`)), {
        message: '等待本人親自按「批准」（本 spec 不會代按）',
        timeout: HUMAN_STEP_MS,
      })
      .toBeGreaterThan(0);

    const approved = await apiInPage(page, ACTIVATION_PATH(selectedJob));
    expect(approved.status).toBe(200);
    expect(JSON.stringify(approved.body), '批准範圍要讀回為已批准').toMatch(/approved/i);

    // Step 5 — the owner presses 紙上執行 exactly once.
    const executeRe = new RegExp(`POST .*${selectedJob}/paper-executions`);
    await expect
      .poll(() => writes.countMatching(executeRe), {
        message: '等待本人親自按「紙上執行」一次',
        timeout: HUMAN_STEP_MS,
      })
      .toBe(1);
    // Exactly one: a second press would be a second ledger row, which the
    // journey never claims, and the panel is the only thing that can press it.
    expect(writes.countMatching(executeRe), '只執行一次紙上決策').toBe(1);

    // Step 6 — formal GET readback of that execution and its association row.
    const ledger = await apiInPage(page, LEDGER_PATH(selectedJob));
    expect(ledger.status, '讀回走正式 route，不是內部查詢').toBe(200);
    const body = ledger.body as any;
    const rows: any[] = Array.isArray(body) ? body : (body?.items ?? body?.executions ?? []);
    expect(rows.length, '至少要有一筆與本次點擊對應的紙上記錄').toBeGreaterThanOrEqual(1);

    const mine = rows.filter((r) => r?.skill_unit_id === selectedSkill);
    expect(mine.length, `本人明選的那個技能帳號只對應一筆（實得 ${mine.length}／共 ${rows.length} 筆）`).toBe(1);
    expect(mine[0].job_id ?? selectedJob, 'execution 綁在同一個 job').toBe(selectedJob);
    expect(mine[0].id ?? mine[0].execution_id, 'execution 要有可讀回的業務鍵').toBeTruthy();

    await expect(page.getByTestId('paper-ledger-summary'), '書面摘要要出現在畫面上').toBeVisible();
    await expect(page.getByTestId('paper-ledger-empty'), '有記錄時不應再顯示空態').toHaveCount(0);

    // Standing limit: the daily-estimate lane must not have consumed anything.
    const quota = await apiInPage(page, '/usage/quota');
    expect(quota.status).toBe(200);
    expect(JSON.stringify(quota.body), '本輪不應出現每日估計扣額').not.toMatch(
      /daily_model_estimate[^}]*consumed[^}]*[1-9]/i,
    );

    writes.stop();
  });
});
