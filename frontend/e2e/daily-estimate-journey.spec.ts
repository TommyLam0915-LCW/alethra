import { test, expect, type Page } from '@playwright/test';
import { loadRuntime, runtimeAvailable, TIER_B_SKIP, type Runtime } from './runtime';
import {
  ESTIMATE_STATE_LABELS_ZH,
  estimateState,
} from '../src/lib/dailyEstimate';

/**
 * TIER B — journey 2: 每日模型估計在瀏覽器裡讀得到、點得動、而且同一個視窗不付兩次。
 *
 * WHY THIS FILE EXISTS
 *   Every layer of this journey had evidence except the screen. The route and the
 *   ledger are covered by the backend lane
 *   (`tests/test_daily_model_estimate.py`, `tests/test_daily_estimate_two_workers.py`),
 *   the reducer and the display states by the node unit (`tests/dailyEstimate.test.mjs`),
 *   and the panel's wiring by `DailyEstimatePanel.tsx` itself — but no spec anywhere
 *   navigated to `/training` and required the rendered page to say what
 *   `GET /api/v1/ml/personalization/daily-estimate/status` actually answered. That is
 *   the gap this file closes, and it is the gap the round-12 claim「面板 unit 已通過，
 *   整體只差 browser」glossed over: the panel unit proves the reducer, not the mount.
 *
 * TWO GROUPS, DIFFERENT COSTS (and the second one is off by default)
 *   READ group — no state change, no quota, nothing to consent to. It is the part a
 *     reviewer can run against any stack that has the account.
 *   WRITE group — it can spend real quota, so it is gated behind an explicit
 *     environment choice: E2E_DAILY_ESTIMATE_SKILL must name the one skill account
 *     allowed to pay. Unset → the write tests skip, they do not pick an account on
 *     the owner's behalf.
 *     That gate is a billing guard, not a consent rule. Pressing 「立即估計」 is an
 *     execution inside a scope the owner already granted (`MANUAL_SCOPE = "manual"`,
 *     daily_estimates.py:71, is the only thing a hand press changes about the clock's
 *     tick — `trigger_daily_estimate`, app/services/daily_estimates.py:1280, passes it
 *     at :1309), so an agent running this spec does not
 *     need a per-press owner signature; what it must not do is widen the consent,
 *     and the route refuses a skill the owner never declared
 *     (personalization.py:459-478), and the route refuses a skill the owner never
 *     declared before the tick opens a ledger row
 *     (daily_estimates.py:1304-1306 → `no_skills_enabled`). Granting and revoking
 *     stay the owner's own hands; see WHAT THIS FILE NEVER DOES.
 *   What one press costs, stated by the durable business key and not by the click
 *     count (the round-15 copy called this「一次點擊一席位」; that is wrong in both
 *     directions — a second press costs nothing, and one press naming two
 *     instruments can bill twice). The three cases, each read off the code:
 *       - a NEW estimate settles one `ai_usage` row: `_estimate_one` hands the work
 *         to `_run_personal_prediction` (app/services/daily_estimates.py:862 →
 *         app/api/personal_models.py:593), which reserves at
 *         `app/auth/entitlements.py:252` `reserve_quota` (personal_models.py:656)
 *         and settles it (personal_models.py:688, `settle_reservation`
 *         entitlements.py:357) in the same commit that writes the billing row;
 *       - a press whose window is already billed never reaches that reserve:
 *         `claim_estimate` (daily_estimates.py:509) answers `True` for the key
 *         `f"{owner_id}|{skill_unit_id}|{instrument_id}|{window}"`
 *         (daily_estimates.py:601), the tick records
 *         `already_estimated_this_window` and `continue`s
 *         (daily_estimates.py:1069-1082) — so a second press on the same day is
 *         accepted and spends nothing, which is what the last test below measures;
 *       - a press that FAILS releases the reservation before the error surfaces
 *         (personal_models.py:660, 670, 674, 678 → `release_reservation`
 *         entitlements.py:452), so a refused or errored run leaves no seat behind.
 *     Reconciliation therefore counts billing rows per (owner, skill account,
 *     instrument, UTC day) — never per click.
 *
 * WHAT THIS FILE NEVER DOES
 *   - It does not grant or revoke the daily-estimate consent. The acknowledge box
 *     (`daily-estimate-acknowledge`, DailyEstimatePanel.tsx:676) is the owner's own
 *     statement; an automation clicking it would be consenting for a person, and a
 *     revoke would destroy state another run made. Both buttons are asserted to
 *     exist and to be labelled, and neither is ever pressed.
 *   - No route is intercepted, stubbed or mocked. Waits are waitForResponse /
 *     expect-poll conditions, never sleeps.
 *   - The API calls below are read-only oracles
 *     (`/daily-estimate/consent`, `/daily-estimate/status`); the one write is the
 *     same POST the 「立即估計」 button itself makes, and it is triggered by the click.
 *
 * WHAT THIS DOES NOT PROVE
 *   - that an estimate is a good forecast; only that the row the server billed is
 *     the row the screen prints, and that a second press inside the same (skill,
 *     instrument, UTC day) key settles no second billing row.
 *   - anything about the scheduler's own tick. The cron half is
 *     `tests/test_daily_model_estimate.py`; a browser cannot wait for a UTC 06:00 job.
 *   - PostgreSQL behaviour: one browser, one writer. The two-worker race stays where
 *     it is measured (`tests/test_daily_estimate_two_workers.py`, which is
 *     PostgreSQL-only and skips on the sqlite lane).
 *
 * STATUS: 本輪授權「不啟動任何服務或瀏覽器」，所以這支 spec 尚未執行過一次。
 *   selector 全部摘自下面三個來源檔案，不是猜的；第一次真正跑起來仍可能需要一次
 *   selector 校正，在那之前它是一份待執行的驗收腳本，不是 browser 證據。
 *     `daily-estimate-panel`／`-state`／`-error`／`-notice`／`-status-list`／
 *       `-no-skills`／`-skill-<id>` — DailyEstimatePanel.tsx:271,291,305,310,316,320,337
 *     `-last-<id>`（內含 `inference_id <id>`，:465）／`-reused-<id>`／
 *       `-last-run-<id>`／`-refusal-<id>`／`-failure-<id>`／`-version-drift-<id>`／
 *       `-none-<id>`／`-unconfirmed-<id>`／`-state-<id>`（狀態徽章）
 *       — :436,465,452,425,491,503,480,513,529,367
 *     `daily-estimate-run-<id>`／`-withdraw-<id>` — :390,:402
 *     `daily-estimate-scope-editor`／`-consent-provenance`／`-acknowledge`／`-grant`
 *       ／`-revoke-all` — :553,:568,:676,:685,:695
 *     the mount: src/app/training/page.tsx:557 renders `<DailyEstimatePanel />`
 *     the routes: src/lib/api.ts:891（consent）,911（status）,913-917（run，body 只有
 *       `{skill_unit_id}`）；計費讀取入口 GET /api/v1/ml/usage/quota
 *       （backend/app/api/billing.py:287，回 `used`／`reserved`／`limit`）
 */

let runtime: Runtime;
test.skip(!runtimeAvailable(), TIER_B_SKIP);
test.beforeAll(() => { runtime = loadRuntime(); });

/** The one skill account this run is allowed to settle a billing row for, if any. */
const PAYING_SKILL = process.env.E2E_DAILY_ESTIMATE_SKILL ?? '';
const WRITE_SKIP =
  '寫入組需要 owner 親自在環境裡点名一個可以被計費的技能帳號：' +
  'export E2E_DAILY_ESTIMATE_SKILL=<skill_unit_id>。' +
  '計費按持久業務鍵 owner_id|skill_unit_id|instrument_id|UTC 視窗（app/services/daily_estimates.py:601）' +
  '而不是點擊次數：新估計結算一筆 ai_usage（app/api/personal_models.py:688）、' +
  '同一視窗沿用不進 reserve_quota（daily_estimates.py:1069）、失敗釋放預訂（personal_models.py:660,670,674,678）。' +
  '未点名就整組跳過——自動化不替本人決定誰可以被扣額。';

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

interface EstimateState {
  skill_unit_id: string;
  enabled?: boolean;
  declared?: boolean;
  skill_status?: string | null;
  disabled_reason?: string | null;
  current_model_version_id?: string | null;
  instrument_ids?: string[];
  last_run?: { window?: string | null; scope?: string | null; status?: string | null } | null;
  last_estimate?: {
    inference_id?: string | null;
    instrument_id?: string | null;
    model_version_id?: string | null;
    verdict_durable?: boolean;
  } | null;
  last_refusal?: { reason?: string | null; instrument_id?: string | null } | null;
  last_failure?: { reason?: string | null; count?: number | null } | null;
  /** The UTC day this card describes, and whether a run opened it without leaving
   *  a bill, an estimate or a refusal behind (DailyEstimatePanel.tsx:92-93). */
  window?: string;
  window_claimed?: boolean;
  estimate_matches_current_selection?: boolean;
  last_run_at?: string | null;
}

// ── the badge oracle, kept out of the panel's own hands ──────────────────
//
// The panel answers with `estimateState()` and prints
// `ESTIMATE_STATE_LABELS_ZH[windowState]` (DailyEstimatePanel.tsx:328-333,381). A
// spec that imports those two and requires the DOM to equal their output proves
// only that a module returns what it returns: edit the rule and the expectation
// moves with it, so the assertion cannot fail and never had a chance to. Round 18
// named exactly this (「現有 spec 不得只用產品相同 helper 算答案、再拿答案驗證產
// 品」), so this file keeps its own five words and its own reading of the four raw
// status fields, and holds the screen against *that*. The two copies are tied
// together at the end of the badge node below — a rename fails there with its own
// sentence rather than quietly turning the DOM check into a tautology.
const WINDOW_BADGE_ZH = {
  estimated: '本次估計',
  carried_over: '已沿用',
  refused: '本次拒絕',
  unconfirmed: '執行狀態未確認',
  never_estimated: '沒有估計',
} as const;

type WindowBadge = keyof typeof WINDOW_BADGE_ZH;

/** The badge one status row earns, read off the route's JSON and nothing else. */
function badgeOfLedger(row: EstimateState): WindowBadge {
  // A row the run produced and a row rebuilt from the bill are both estimates; what
  // separates them is whether a verdict travelled with the charge. `verdict_durable:
  // false` is written only by the ledger re-read, so a row carrying it holds a
  // payment and no answer — that is 已沿用, neither a failure nor a blank.
  if (row.last_estimate != null) {
    return row.last_estimate.verdict_durable === false ? 'carried_over' : 'estimated';
  }
  // A refusal outranks a claim. The service named this skill and said why it would
  // not price it; the tick's silence about a window is a weaker account than that.
  if (row.last_refusal != null) return 'refused';
  return row.window_claimed === true ? 'unconfirmed' : 'never_estimated';
}

/** Every combination the four raw fields can take, for the table below. */
function allLedgerShapes(): Array<{ key: string; row: EstimateState }> {
  // Key is `has_estimate` + `verdict_durable` + `last_refusal` + `window_claimed`,
  // each 1/0, with `-` where an absent estimate leaves nothing durable to read.
  const shapes: Array<{ key: string; row: EstimateState }> = [];
  for (const withEstimate of [true, false]) {
    for (const durable of withEstimate ? [true, false] : [false]) {
      for (const withRefusal of [true, false]) {
        for (const claimed of [true, false]) {
          shapes.push({
            key: `${withEstimate ? 1 : 0}${withEstimate ? (durable ? 1 : 0) : '-'}${
              withRefusal ? 1 : 0
            }${claimed ? 1 : 0}`,
            row: {
              skill_unit_id: 'shape',
              last_estimate: withEstimate
                ? { inference_id: 'inf-1', verdict_durable: durable }
                : null,
              last_refusal: withRefusal ? { reason: 'stale_market_data' } : null,
              window_claimed: claimed,
            },
          });
        }
      }
    }
  }
  return shapes;
}

/**
 * The twelve answers, written by hand and not derived by the function above.
 *
 * This is where the precedence the badge is claimed to keep becomes checkable even
 * on a stack that happens to hold one row of one kind: an estimate outranks a
 * refusal and a claim (a charge happened, and no later silence erases it), a refusal
 * outranks a claim (the service named the skill and gave a reason), and only a bare
 * claim says 執行狀態未確認. A shape with no row here is an untested shape.
 */
const HAND_WRITTEN_BADGES: Record<string, WindowBadge> = {
  '1100': 'estimated',
  '1101': 'estimated',
  '1110': 'estimated',
  '1111': 'estimated',
  '1000': 'carried_over',
  '1001': 'carried_over',
  '1010': 'carried_over',
  '1011': 'carried_over',
  '0-00': 'never_estimated',
  '0-01': 'unconfirmed',
  '0-10': 'refused',
  '0-11': 'refused',
};

async function openPanel(page: Page): Promise<void> {
  await page.goto(`${runtime.frontendBase}/training`);
  await page.getByTestId('daily-estimate-panel').waitFor({ state: 'visible', timeout: 30000 });
}

/** The ids the status route reports, which is what the list must be built from. */
async function statusSkills(page: Page): Promise<EstimateState[]> {
  const { status, json } = await api(page, '/api/v1/ml/personalization/daily-estimate/status');
  expect(status, 'status must answer for the oracle').toBe(200);
  return (json?.skills ?? []) as EstimateState[];
}

/**
 * The owner-wide `ai_usage` counter, read through the customer's own billing route.
 *
 * This is the only spending figure a customer can read without database access, so
 * it is what the write group reconciles against. Its one weakness has to be said
 * out loud: `used` counts every `ai_usage` settlement this owner made, not just
 * this window's estimates, so attributing a delta to one press is only valid while
 * nothing else on this account runs — one browser, one tab, no cron tick. The rule
 * being tested is the shape `delta === job.estimated`, not the absolute number.
 */
async function quotaUsed(page: Page): Promise<number> {
  const { status, json } = await api(page, '/api/v1/ml/usage/quota?quota_type=ai_usage');
  expect(status, 'quota must answer for the oracle').toBe(200);
  const used = Number(json?.used);
  expect(Number.isFinite(used), `quota 的 used 必須是數字，實際 ${json?.used}`).toBe(true);
  return used;
}

/**
 * Press 「立即估計」 and hand back what actually crossed the wire.
 *
 * The click is the only product step; the request and the response are read off
 * that click's own network exchange, so nothing here can pass on a stubbed route.
 */
async function pressRun(page: Page, skillUnitId: string) {
  const [resp] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().includes('/api/v1/ml/personalization/daily-estimate/run') &&
        r.request().method() === 'POST',
      { timeout: 180000 },
    ),
    page.getByTestId(`daily-estimate-run-${skillUnitId}`).click(),
  ]);
  const request = resp.request();
  return {
    status: resp.status(),
    url: request.url(),
    method: request.method(),
    // What the panel sent — asserted, not assumed, in every caller below.
    sent: request.postDataJSON() as { skill_unit_id?: string } | null,
    body: (await resp.json().catch(() => null)) as {
      job?: {
        job_id?: string;
        scope?: string;
        status?: string;
        estimated?: number;
        refused?: number;
        failed?: number;
      };
      status?: EstimateState;
      detail?: { code?: string };
    } | null,
  };
}

/** The status codes this route can answer, each with what it means. */
const RUN_ACCEPTED = 201; // personalization.py:459 status_code=HTTP_201_CREATED
const RUN_REFUSED = [400, 403, 409, 503]; // _refuse_daily_estimate / scope / kill switch

test.describe('journey 2: the daily-estimate panel prints the ledger it is given', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await page.goto(`${runtime.frontendBase}/login`);
    await page.fill('input[type="email"]', runtime.email);
    await page.fill('input[type="password"]', runtime.password);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test.afterAll(async () => {
    await page?.close();
  });

  test('the state pill says what the consent route says, not what the page hopes', async () => {
    await openPanel(page);
    expect(await page.getByTestId('daily-estimate-error').count(), '載入失敗時不該假裝是一筆估計').toBe(0);

    const { status, json } = await api(page, '/api/v1/ml/personalization/daily-estimate/consent');
    expect(status, 'consent must answer for the oracle').toBe(200);
    const enabled = Boolean(json?.enabled);
    const named = Array.isArray(json?.skill_units) ? json.skill_units.length : 0;
    const pill = (await page.getByTestId('daily-estimate-state').innerText()).trim();

    // The two strings the component can produce (DailyEstimatePanel.tsx:300), and
    // nothing else may satisfy this assertion.
    if (enabled) {
      expect(pill, '已啟用時計數必須等於 consent.skill_units 的長度').toBe(
        `每日服務已啟用（${named} 個技能帳號）`,
      );
    } else {
      expect(pill).toBe('每日服務未啟用');
    }
  });

  test('the rendered rows are exactly the accounts the status route returned', async () => {
    await openPanel(page);
    const oracle = await statusSkills(page);
    const shown = await page
      .getByTestId(/^daily-estimate-skill-/)
      .count();
    const shownIds = oracle.map((s) => s.skill_unit_id);
    const matched = await Promise.all(
      shownIds.map(async (id) => (await page.getByTestId(`daily-estimate-skill-${id}`).count()) === 1),
    );
    if (shownIds.length === 0) {
      await expect(page.getByTestId('daily-estimate-no-skills')).toBeVisible();
      expect(shown, '沒有技能帳號時不該憑空列出任何一列').toBe(0);
      test.skip(true, '此帳號還沒有技能帳號；逐列比對需要至少一個（建立它是另一條旅程，不在本檔範圍）');
    }
    expect(shown, `列出的列數必須等於 status 回傳的 ${shownIds.length} 列`).toBe(shownIds.length);
    expect(matched.every(Boolean), '每一列都要認得自己的 skill_unit_id').toBe(true);

    // A row that has an estimate must show the id the server billed, and a row
    // that has none must say so rather than showing a stale card.
    for (const state of oracle) {
      const billed = state.last_estimate?.inference_id ?? '';
      const lastCell = page.getByTestId(`daily-estimate-last-${state.skill_unit_id}`);
      if (billed) {
        // Not merely visible: the card prints `inference_id <id>`
        // (DailyEstimatePanel.tsx:465), so the id the ledger charged for must be
        // readable in the DOM, and the 最後估計 line must name the same
        // instrument the same ledger row names (:447).
        await expect(lastCell, '有估計的列必須印出該筆 inference').toBeVisible();
        await expect(lastCell).toContainText(`inference_id ${billed}`);
        if (state.last_estimate?.instrument_id) {
          await expect(lastCell).toContainText(
            `最後估計：${state.last_estimate.instrument_id}`,
          );
        }
        if (state.last_run?.scope) {
          await expect(
            page.getByTestId(`daily-estimate-last-run-${state.skill_unit_id}`),
            '「最近一次」必須把來源（manual／scheduled）呈現出來，否則客戶分不清是誰按的',
          ).toContainText(state.last_run.scope);
        }
      } else {
        await expect(
          page.getByTestId(`daily-estimate-none-${state.skill_unit_id}`),
          '這個視窗还没有估計時，必須由「無估計」那個元素說話',
        ).toBeVisible();
      }
    }
  });

  test('每一列的狀態徽章由帳本原始欄位獨立推導，而這些標籤之外不發明終態', async () => {
    await openPanel(page);
    const oracle = await statusSkills(page);
    test.skip(oracle.length === 0, '此帳號還沒有技能帳號，逐列狀態比對無從做起');

    const ledgerSays = new Set<WindowBadge>();
    const screenSays = new Set<string>();
    const allowed = Object.values(WINDOW_BADGE_ZH);
    for (const state of oracle) {
      const id = state.skill_unit_id;
      // Four raw fields of the route's own JSON, read here rather than through the
      // panel's helper: an estimate row exists or does not, that row is durable or
      // is not, a refusal exists or does not, the window is claimed or is not. A
      // badge chosen by anything else — how old the run row is, the word in its
      // status column, a guess about what probably happened — disagrees with this
      // line.
      const expected = badgeOfLedger(state);
      ledgerSays.add(expected);
      const badge = page.getByTestId(`daily-estimate-state-${id}`);
      await expect(
        badge,
        `${id} 的徽章必須等於帳本原始欄位推導出的「${WINDOW_BADGE_ZH[expected]}」`,
      ).toHaveText(WINDOW_BADGE_ZH[expected]);
      screenSays.add((await badge.innerText()).trim());
      // 「不得按 job 年齡改終態」read as a gate on the string itself: an invented
      // terminal (已逾時／已死／已退款) fails here whatever it is derived from, and
      // it fails against this file's own five words rather than against the module
      // that rendered them.
      expect(allowed, `${id} 的徽章只能是這五個視窗狀態之一`)
        .toContain(await badge.innerText());

      // The badge is not the only thing the data owns. Each state has exactly one
      // block, so a block shown beside the wrong state is the same defect wearing a
      // different label.
      const blocks = {
        none: await page.getByTestId(`daily-estimate-none-${id}`).count(),
        unconfirmed: await page.getByTestId(`daily-estimate-unconfirmed-${id}`).count(),
        reused: await page.getByTestId(`daily-estimate-reused-${id}`).count(),
        refusal: await page.getByTestId(`daily-estimate-refusal-${id}`).count(),
        failure: await page.getByTestId(`daily-estimate-failure-${id}`).count(),
        drift: await page.getByTestId(`daily-estimate-version-drift-${id}`).count(),
      };
      expect(blocks.none, `${id}：「沒有估計」那一段只能出現在 never_estimated`)
        .toBe(expected === 'never_estimated' ? 1 : 0);
      expect(blocks.unconfirmed, `${id}：「執行狀態未確認」只能出現在 window_claimed 而無帳本列的這一態`)
        .toBe(expected === 'unconfirmed' ? 1 : 0);
      expect(blocks.reused, `${id}：沿用說明只能出現在 carried_over`)
        .toBe(expected === 'carried_over' ? 1 : 0);
      // Two notices, not two more terminal states. A saved failure and a version
      // that has since been replaced each ride along with whatever the badge says,
      // so their presence is keyed to their own raw field and never to the state —
      // 一筆已結算的估計可以同時帶著版本漂移警示，那不是另一種終態。
      expect(blocks.refusal, `${id}：拒絕列必須與 status 的 last_refusal 同有同無`)
        .toBe(state.last_refusal ? 1 : 0);
      expect(blocks.failure, `${id}：失敗列必須與 status 的 last_failure 同有同無`)
        .toBe(state.last_failure ? 1 : 0);
      expect(blocks.drift, `${id}：版本漂移警示必須跟著 estimate_matches_current_selection`)
        .toBe(state.last_estimate && state.estimate_matches_current_selection !== true ? 1 : 0);

      if (state.last_refusal?.reason) {
        await expect(
          page.getByTestId(`daily-estimate-refusal-${id}`),
          '拒絕列要印出帳本給的 reason code，否則客戶無從對照文件',
        ).toContainText(state.last_refusal.reason);
      }
    }

    // The oracle itself, held to a table written before it ran. Twelve shapes, each
    // with a hand-written answer: an expectation that cannot vary with the data is
    // not an oracle, and no live stack is obliged to contain a row of every kind.
    const shapes = allLedgerShapes();
    expect(shapes.length, '狀態表格必須涵蓋十二種可建構的欄位組合').toBe(12);
    for (const { key, row } of shapes) {
      const hand = HAND_WRITTEN_BADGES[key];
      expect(hand, `欄位組合 ${key} 沒有手寫的预期，表格等於沒有約束`).toBeTruthy();
      expect(
        badgeOfLedger(row),
        `${key}（估計=${key[0]} durable=${key[1]} 拒絕=${key[2]} 開窗=${key[3]}）的推導結果`,
      ).toBe(hand);
      // The product's own helper, checked against the same hand-written answer. This
      // is a cross-examination and not the oracle: if the panel's rule and this
      // file's rule ever mean different things, the divergence is named here, for
      // shapes the running stack may never have produced.
      expect(
        estimateState({
          has_estimate: row.last_estimate != null,
          reused: row.last_estimate?.verdict_durable === false,
          refused: row.last_refusal != null,
          window_claimed: row.window_claimed === true,
        }),
        `產品 estimateState() 與本檔的獨立推導在 ${key} 上不同：其中一方的優先順序改了`,
      ).toBe(hand);
    }
    // And the five words. The screen is compared to this file's literals above; if
    // the product module rephrases one, that is a copy edit that has to be agreed
    // here rather than a badge assertion that quietly stops being falsifiable.
    for (const badge of Object.keys(WINDOW_BADGE_ZH) as WindowBadge[]) {
      expect(
        ESTIMATE_STATE_LABELS_ZH[badge],
        `「${badge}」的產品措辭與本檔規格抄件不同，先確認是哪一方要改措辭`,
      ).toBe(WINDOW_BADGE_ZH[badge]);
    }

    // What this stack could and could not exercise, printed rather than claimed: a
    // state no card happened to be in stays unverified for this run, and the run
    // output says so instead of the file's prose. Both columns are printed because
    // the point of the node is that they agree row by row, not that either one is
    // populated.
    const missed = (Object.keys(WINDOW_BADGE_ZH) as WindowBadge[]).filter(
      (s) => !ledgerSays.has(s),
    );
    console.log(
      `[daily-estimate-states] cards=${oracle.length} ` +
        `ledger_derived=${Array.from(ledgerSays).sort().join(',')} ` +
        `screen_rendered=${Array.from(screenSays).sort().join(',')} ` +
        `not_exercised_by_this_stack=${missed.sort().join(',') || 'none'} ` +
        `table_checked=12/12(hand_written_badges)`,
    );
  });

  test('the scope editor lists the same accounts and keeps its consent language on screen', async () => {
    await openPanel(page);
    const editor = page.getByTestId('daily-estimate-scope-editor');
    await expect(editor, '範圍編輯器必須在頁面上，否則客戶看不到自己点名了誰').toBeVisible();
    await expect(page.getByTestId('daily-estimate-consent-provenance')).toBeVisible();
    await expect(page.getByTestId('daily-estimate-acknowledge')).toBeVisible();
    // Present, never pressed: granting and revoking are the owner's own statements.
    await expect(page.getByTestId('daily-estimate-grant')).toBeVisible();
  });

  test('a press sends this skill id, settles exactly job.estimated units, and the screen prints that row', async () => {
    test.skip(!PAYING_SKILL, WRITE_SKIP);
    await openPanel(page);

    const before = await statusSkills(page);
    const prior = before.find((s) => s.skill_unit_id === PAYING_SKILL);
    test.skip(!prior, `status 回傳的帳號裡沒有 ${PAYING_SKILL}；先在每日估計範圍裡点名它（owner 本人動作）`);
    test.skip(
      !prior!.declared,
      `${PAYING_SKILL} 的 declared=false：「立即估計」按鈕只為本人聲明過的帳號渲染（DailyEstimatePanel.tsx:386），而聲明是 owner 本人的動作，本檔不代按。`,
    );
    const beforeId = prior!.last_estimate?.inference_id ?? '';
    const usedBefore = await quotaUsed(page);

    const press = await pressRun(page, PAYING_SKILL);

    // 1. The request the click produced, not the one this file hopes for.
    expect(press.method, '按鈕必須用 POST 送 run').toBe('POST');
    expect(press.url, 'run 必須打在這條正式路由上').toContain(
      '/api/v1/ml/personalization/daily-estimate/run',
    );
    expect(press.sent?.skill_unit_id, '送出的 body 必須只点名這一個技能帳號').toBe(PAYING_SKILL);

    // 2. The response is either an accepted job or a refusal with a code — a 200
    //    with no shape, or a 500, fails here rather than further down.
    if (press.status === RUN_ACCEPTED) {
      const job = press.body?.job;
      expect(job, '201 必須帶著 job 回來').not.toBeNull();
      expect(job!.scope, '手動入口的 job 必須自己說是 manual').toBe('manual');
      expect(
        ['completed', 'partial', 'failed'].includes(String(job!.status)),
        `job.status 必須是三個終態之一，實際 ${job!.status}`,
      ).toBe(true);
      for (const key of ['estimated', 'refused', 'failed'] as const) {
        expect(
          Number.isInteger(Number(job![key])),
          `job.${key} 必須是整數，否則「扣了幾筆」無從對帳（實際 ${String(job![key])}）`,
        ).toBe(true);
      }

      // 3. The reconciliation itself: settled units == items this press billed.
      //    A press that reused the window bills nothing, so `used` must not move.
      const usedAfter = await quotaUsed(page);
      expect(
        usedAfter - usedBefore,
        `計費必須等于 job.estimated（新估計 ${job!.estimated} 筆），實際 used 從 ${usedBefore} 變成 ${usedAfter}`,
      ).toBe(Number(job!.estimated));

      await openPanel(page);
      const after = (await statusSkills(page)).find((s) => s.skill_unit_id === PAYING_SKILL)!;
      const afterId = after?.last_estimate?.inference_id ?? '';

      if (Number(job!.estimated) > 0) {
        expect(afterId, ' billed 就必須讀得到 inference_id').not.toBe('');
        // The route's own read and the independent oracle must agree, and the DOM
        // must show that id — three spellings of one row, not three guesses.
        expect(
          press.body?.status?.last_estimate?.inference_id,
          'run 回覆裡的 inference_id 必須等於 status 讀到的',
        ).toBe(afterId);
      } else {
        expect(afterId, '沿用視窗時，已有估計不能不見').toBe(beforeId);
      }
      await expect(
        page.getByTestId(`daily-estimate-last-${PAYING_SKILL}`),
        '面板必須把帳本那一列的 inference_id 印在 DOM 上',
      ).toContainText(`inference_id ${afterId}`);
      await expect(
        page.getByTestId(`daily-estimate-last-run-${PAYING_SKILL}`),
        '「最近一次」必須讀回這次手動要求（scope=manual）',
      ).toContainText('manual');
    } else {
      expect(
        RUN_REFUSED.includes(press.status),
        `run 只能回 201 或已定義的拒絕碼，實際 ${press.status}`,
      ).toBe(true);
      expect(
        typeof press.body?.detail?.code,
        '拒絕必須帶 machine code，否則文案無法對照',
      ).toBe('string');

      // A refusal spends nothing: no settle, and the window's billed row (if any)
      // survives untouched.
      const usedAfter = await quotaUsed(page);
      expect(usedAfter, `被拒絕的這一次（${press.body?.detail?.code}）不能結算額度`).toBe(usedBefore);
      await openPanel(page);
      const after = (await statusSkills(page)).find((s) => s.skill_unit_id === PAYING_SKILL);
      expect(after?.last_estimate?.inference_id ?? '', '拒絕不能把已有估計弄不见').toBe(beforeId);
    }
  });

  test('a second press inside the same UTC window bills only keys the window has not paid for', async () => {
    test.skip(!PAYING_SKILL, WRITE_SKIP);
    await openPanel(page);

    const first = (await statusSkills(page)).find((s) => s.skill_unit_id === PAYING_SKILL);
    const firstId = first?.last_estimate?.inference_id ?? '';
    test.skip(!firstId, '這個視窗還沒有已計費的估計可沿用；先讓上一支測試跑成');
    test.skip(!first!.declared, `${PAYING_SKILL} 的 declared=false：撤回後沒有按鈕可按，本檔不代為聲明。`);
    const usedBefore = await quotaUsed(page);

    const press = await pressRun(page, PAYING_SKILL);
    test.skip(
      press.status !== RUN_ACCEPTED,
      `第二次點擊未被接受（${press.status} ${press.body?.detail?.code ?? ''}）；去重契約需要一次 201`,
    );
    const job = press.body!.job!;

    // The durable key is (owner, skill, instrument, day). A press is only a
    // reuse when every instrument this skill declared is already billed for the
    // day; with a second instrument still unpaid, billing it is the correct
    // answer, not a double charge — so that case cannot test the reuse claim.
    test.skip(
      Number(job.estimated) > 0,
      `這一次結算了 ${job.estimated} 筆新估計（視窗內還有未計費的標的）；沿用契約需要一個已完整計費的視窗`,
    );

    // Same key, second press: nothing settled, and the answer says so itself.
    expect(
      (await quotaUsed(page)) - usedBefore,
      '沿用同一個 (skill, instrument, UTC 日) 不能結算任何額度',
    ).toBe(0);
    expect(Number(job.refused) >= 1, '沿用必須以一条拒絕記錄說明原因').toBe(true);

    await openPanel(page);
    const again = (await statusSkills(page)).find((s) => s.skill_unit_id === PAYING_SKILL)!;
    expect(
      again.last_estimate?.inference_id,
      '沿用人必須還是同一筆 inference — 換 id 就是又結算了一次',
    ).toBe(firstId);
    await expect(
      page.getByTestId(`daily-estimate-reused-${PAYING_SKILL}`),
      '面板必須自己說出這一是沿用的，而不是讓客戶自己比 id',
    ).toBeVisible();
    await expect(
      page.getByTestId(`daily-estimate-last-${PAYING_SKILL}`),
    ).toContainText(`inference_id ${firstId}`);
  });
});
