import { test, expect, type Page, type Request, type Route } from '@playwright/test';
import { loadRuntime, runtimeAvailable, TIER_B_SKIP, type Runtime } from './runtime';

/**
 * TIER B — journey 1's one open browser cell: 明選的模型推理血緣走完
 * 面板 → POST → 持久化 → 讀回 → 匯出，而且每一層都是同一個 inference id。
 *
 * WHY THIS FILE EXISTS (and why it is small)
 *   `order-lifecycle-flow.spec.ts` already drives the whole submit path with real
 *   gestures — filling action／quantity／rationale, 「Review paper order」,
 *   「Submit paper order」 — but it submits without naming an inference, so the
 *   lineage column it prints (`order-manual-lineage`) is only ever proven to be
 *   *empty* there. The product contract that matters for the customer is the other
 *   branch: a decision that says which model execution it came from, and keeps
 *   saying so after a reload and in the export. That is the single cell this file
 *   adds, and it deliberately reuses the state the other spec's seed already
 *   creates instead of setting up a session a second time.
 *
 * WHAT IT DOES NOT FABRICATE
 *   No session, portfolio, watchlist row or inference is created here. Every
 *   precondition is *discovered* read-only, and the file skips with a stated
 *   reason when the stack does not already hold one. In particular the panel can
 *   only offer lineage for the current UTC window, so on a stack where nobody has
 *   run the daily estimate yet the answer is a skip, not a stubbed response.
 *
 * WHAT THIS DOES NOT PROVE
 *   - anything about PostgreSQL row-level behaviour: one browser, one writer.
 *   - that the inference was *correct*, only that the id the customer named is the
 *     id the ledger kept.
 *   - the quota accounting behind it: `usage_records` billing is asserted by the
 *     backend lane, not here.
 *
 * STATUS: 本檔在本輪「不啟動任何服務或瀏覽器」的授權下撰寫，尚未執行過任何一次。
 *   每個 selector 與行號都在本輪重新讀過來源檔案核對，不是猜的；但第一次真正跑起來
 *   仍可能需要一次 selector 校正，在那之前這是一份待執行的驗收腳本，不是 browser 證據。
 *   selector 來源 — src/components/workspace/OrderPreviewPanel.tsx（本輪改了這個檔，
 *   下列行號是改完之後重新 grep 出來的，不是沿用上一版）：
 *     表單：`order-target` :878、`order-blocked` :889、manual 分支 `{isManual ? (` :894、
 *       `order-action` :905、`order-quantity-input` :922（只在 action==='buy'
 *       時渲染，:912，所以要選完 action 才填得到数量）、`order-rationale` :935
 *     血緣：`ws-lineage-picker` :941、`ws-lineage-attach` :957（disable 分支 :959）、
 *       `ws-lineage-state` :964、`ws-lineage-source` :969、`ws-lineage-none` :982、
 *       `ws-lineage-hold` :987、`ws-lineage-note` :992
 *     檢視／收據：`order-build-review` :1097、`order-review` :1104、
 *       `order-review-lineage` :1128、`order-review-cursor` :1134、
 *       `order-review-key` :1138（印 key 的前 18 字元加省略號，上面的斷言按同一規則
 *       比對送出的那一個）、`order-review-stale` :1144、`order-submit-manual` :1160、
 *       `order-cancel-review` :1182、`order-error` :769、
 *       `order-manual-lineage` :1217（讀 server 回的
 *       `decision.decision_detail.linked_inference_id`，不是瀏覽器自己送的值）
 *   回放列 — src/components/workspace/ReplayController.tsx：`replay-status` :71、
 *     `replay-advance` :107（disabled 條件 `busy || !canAdvance` 在 :106）、
 *     `replay-pause` :117（只在 `session.status === 'active'` 渲染，:111）、
 *     `replay-resume` :128（:122）。Pause／Resume 由 WorkspaceShell.tsx:916-917 接進
 *     `runReplayAction`，那支函式把回應整个換進 `replaySession`（:356），而面板的
 *     `session` 就是它（:967）、`onSessionChange` 就是 `setReplaySession`（:969）。
 *     所以「畫面自己看到 session 被動過」在這一欄是真的一条路，不需要重新載入。
 *   WHY the action is 「buy」 and not 「hold」 — 上一版在這裡選 hold，那是一条走不通
 *     的路：`lineageState` 對 hold 一律回 `hold_no_lineage`（src/lib/dailyEstimate.ts
 *     :270-274），勾選框因此被停用（OrderPreviewPanel.tsx:959）、檢視上也凍不出 id
 *     （:495-496），而 route 直接拒絕這個組合（reason `inference_needs_decision_row`，
 *     app/api/simulations.py:2488-2505）。hold 仍是合法決策，只是它的血緣走另一個入口
 *     （POST /paper-portfolio/{id}/decisions）；本檔驗的是有來源欄那條路，所以買 1 股。
 *   WHAT THE REQUEST/RESPONSE ASSERTIONS REST ON — `buildReview` 凍結來源、游標版本與
 *     idempotency key（OrderPreviewPanel.tsx:466-498；key :489、版本 :491、来源 :495-496），
 *     任一被凍結的輸入改變就讓舊檢視作廢（印信由 src/lib/reviewCurrency.ts 的
 *     `reviewStamp` 算出，:406-422；`reviewStale` :423），`submitManual` 只從凍結後的
 *     review 組 body，`expected_version` 與 `linked_inference_id` 皆非空才送
 *     （:507-516）。入口是
 *     POST /api/v1/simulations/{session_id}/manual-decision（src/lib/api.ts:433-445
 *     → app/api/simulations.py:2300），回 200 與它寫下的那一列。
 *   409 之後畫面自己做的那件事 — `submitManual` 的 409 分支先把舊印信清掉（:550），
 *     再把 session 重讀一次換進面板（:555-560：重讀 :556、裝回去 :557），然後才設那行
 *     錯（:561）。因為 `setError` 排在那個 await 之後，`order-error` 出現在畫面上就
 *     代表重讀已經落地；第四節等這個訊息，等的其實是「面板現在拿的是新版本」。
 *   WHAT THE LAST THREE NODES WRITE（披露，不是悄悄做）—
 *     第四節在同一個回放 session 上留兩筆「觀望」列：一把由 API 打進去的競爭 key、
 *     一把由原畫面重建後按下去的 key；中間那一筆被擋下（409），零列。
 *     第五節只按 Pause＋Resume 各一次，兩者都不寫決策列、也不動 state_version
 *     （app/api/simulations.py:1882-1897、:1911-1926），只改 status，結束時已回到 active。
 *     第六節先把 GET session 的回應改寫成「沒有 state_version」的舊形態（只改這份
 *     回應的 JSON，不寫任何資料），再按一筆「觀望」（1 列）。
 *     三節加起來共 3 筆 hold：不建立訂單、不動現金、不扣 `ai_usage`（計費只發生在
 *     推論入口）。它需要寫決策列是因為「檢視之後 session 又被動過」這件事沒有別的辦法
 *     造出來：游標日期與 bar 不會變（app/api/simulations.py:2551-2555 說只有決策數與
 *     版本會動），會被推動的只有 state_version（同一個 UPDATE 的
 *     `state_version=current_version+1`，:2563）。
 *   為什麼「reload 之後再提交」不再是這個宣稱的正控 — reload 連面板裡的 session 一起
 *     換掉，所以它無法區分「409 後畫面重讀到了新版本」與「整個畫面重印过一次」；
 *     不刷新、在原畫面重建印信（第四節）才把兩者分開。reload 那條路本檔仍由第三節走
 *     一次（reload + 重選 + 讀回來源，但不提交），所以它是另一個情境，不算樂觀鎖恢復
 *     的證據。
 *   尚未在 browser 證過的兩件事（不假装）—
 *     ①「重讀期間使用者換了 session，舊回應不得覆蓋新 session」：面板用
 *       src/lib/reviewCurrency.ts 的 `isStillViewed` 加上送出前記下的 `viewedSessionId`
 *       （OrderPreviewPanel.tsx:536、:557、reconcile 那一支同形）擋掉，本輪由
 *       tests/reviewCurrency.test.mjs 離線證到那段判別；在 browser 這一欄要穩定地讓
 *       一次換帳號點的回應落在两次讀取之間，需要另一套時序控制，屬 NOT RUN。
 *     ②「legacy：面板讀不到版本時」不算樂觀鎖已驗證 — 第六節證的是那個形態下
 *       `expected_version` 根本不會送出（缺失，不是取最新），伺服器因此不做版本比對；
 *       這條路是「沒有鎖」的證明，不能当成「鎖通過」的證據記帳。
 */

// A missing tier-B stack is a fact about this machine, not about the code: it must
// skip this file rather than throw at load and make the whole suite unlistable
// (the reason e2e/runtime.ts exists). `runtime` stays unassigned until then, so
// every use below sits inside a test or a hook that cannot run while skipped.
let runtime: Runtime;
test.skip(!runtimeAvailable(), TIER_B_SKIP);
test.beforeAll(() => { runtime = loadRuntime(); });
const ASSET = process.env.E2E_LINEAGE_ASSET ?? 'DEMO-ALPHA';

/** Read-only oracle. Supplements the screen; never performs a product step. */
async function api(page: Page, method: 'GET' | 'POST', path: string, body?: unknown) {
  const token = await page.evaluate(() => localStorage.getItem('token'));
  if (!token) throw new Error('the UI session holds no token; login did not persist');
  const res = await page.request.fetch(`${runtime.backendBase}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status(), json: text ? JSON.parse(text) : null, text };
}

/** What the two formal decision entries expose of one row. */
interface DecisionRow {
  id: string;
  linked_inference_id?: string | null;
  rationale?: string | null;
}

/** One row of GET /{portfolio_id}/decision-history?format=json. */
interface DecisionHistoryRow {
  decision_id?: string;
  decision_scope?: string;
  linked_inference_id?: string | null;
  inference_source_state?: string;
}

test.describe('journey 1: a named inference survives submit → readback → export', () => {
  test.describe.configure({ mode: 'serial' });

  let page: Page;
  let portfolioId = '';
  let inferenceId = '';
  let sessionId = '';
  let decisionId = '';
  let recordId = '';

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

  test('the panel offers a lineage source, and it is a real inference row', async () => {
    await page.goto(`${runtime.frontendBase}/workspace`);
    const watchlist = page.getByTestId('watchlist-panel');
    await watchlist.waitFor({ state: 'visible', timeout: 30000 });
    // Nothing here is seeded: the row has to already be plotted, or this stack has
    // no instrument to attach a lineage to. A skip names what is missing instead
    // of failing over a fact about the machine.
    const plotted = watchlist.getByRole('button', { name: `Select ${ASSET}` });
    if ((await plotted.count()) === 0) {
      test.skip(
        true,
        `此帳號的自選股裡沒有「${ASSET}」這列（WatchlistPanel.tsx:120 的 aria-label 找不到）。` +
          `改設 E2E_LINEAGE_ASSET=<已加入自選股的代碼>，或先在頁面上加入它。`,
      );
    }
    await plotted.click();
    await expect(page.getByTestId('order-symbol')).toContainText(ASSET);

    // The account is chosen explicitly, as the product requires; the first enabled
    // option whose decisions the owner can already read is the one under test.
    const select = page.getByTestId('order-preview-panel').locator('#order-portfolio-select');
    await expect(select, '面板必須暴露帳號選擇器').toBeVisible();
    const values = await select
      .locator('option')
      .evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
    test.skip(values.length === 0, 'this account has no skill unit to bind a decision to');
    portfolioId = values[0];
    await select.selectOption(portfolioId);

    // The lineage block is rendered only by the manual door (OrderPreviewPanel.tsx
    // 的 `{isManual ? (` 在 :894 → the picker at :941), so an unbound or finished
    // session has nothing to tick. Ask the panel which door it is standing in before
    // reading its fields.
    const target = page.getByTestId('order-target');
    await expect(target, '面板必須自己說明這一筆會提交到哪裡').toBeVisible();
    const where = (await target.innerText()).trim();
    if (!where.includes('historical replay session')) {
      test.skip(
        true,
        `選定的技能帳號沒有可接收決策的歷史回放 session，明選血緣的這一欄根本不出現` +
          `（面板顯示：「${where}」）。先讓 ${ASSET} 綁上一個 active/pending 的回放 session` +
          '（e2e/order-lifecycle-flow.spec.ts 的 seed 會建一個），才有血緣可接。',
      );
    }

    const state = page.getByTestId('ws-lineage-state');
    await expect(state, '血緣列必須自己說明它的狀態').toBeVisible();
    const shown = (await state.innerText()).trim();
    if (await page.getByTestId('ws-lineage-none').count()) {
      test.skip(
        true,
        `此帳號在目前的 UTC 視窗沒有可被點名的模型估計列（面板顯示：「${shown}」）。` +
          '先跑 e2e/daily-estimate-journey.spec.ts 的寫入組，或等每日服務產生一列，才有血緣可接。',
      );
    }
    const source = page.getByTestId('ws-lineage-source');
    await expect(source, '有來源時必須印出 inference_id').toBeVisible();
    const printed = (await source.innerText()).trim();
    const match = /inference_id:\s*([0-9a-fA-F-]{36})/.exec(printed);
    expect(match, `面板印的必須是一個 UUID，实际是「${printed}」`).not.toBeNull();
    inferenceId = match![1];

    // The screen's claim is checked against the ledger before anything is written:
    // if the panel can name an inference the API will not return, the UI is lying.
    const { status, json } = await api(page, 'GET', `/api/v1/ml/personalization/daily-estimate/status`);
    expect(status, 'status must answer for the oracle').toBe(200);
    const rows = (json?.skills ?? []) as Array<{ last_estimate?: { inference_id?: string } }>;
    const billed = rows
      .map((s) => s?.last_estimate?.inference_id)
      .filter((v): v is string => typeof v === 'string' && v.length > 0);
    expect(
      billed,
      `面板點名的 ${inferenceId} 必須也是 status 回傳的推論之一（status 給的是 ${JSON.stringify(billed)}）`,
    ).toContain(inferenceId);
  });

  test('attaching it survives the submit and is what the server answered with', async () => {
    test.skip(!inferenceId, 'depends on the discovery test above finding a source');

    // Action first, and not 「hold」: a hold records no decision row, so a lineage
    // attached to it is refused by the route (`inference_needs_decision_row`) and
    // the checkbox is disabled for it — LINEAGE_HOLD_NOTE_ZH,
    // src/lib/dailyEstimate.ts:284-289, and the disabled branch at
    // OrderPreviewPanel.tsx:959. A buy of 1 share is the smallest decision that
    // actually has a row for the lineage to live on.
    const ACTION = 'buy';
    const RATIONALE = 'e2e：驗證明選血緣的讀回（buy 1，只在紙上回放，不下真實單）';
    await page.getByTestId('order-action').selectOption(ACTION);
    await page.getByTestId('order-quantity-input').fill('1');
    await page.getByTestId('order-rationale').fill(RATIONALE);

    const attach = page.getByTestId('ws-lineage-attach');
    await expect(attach, '有來源且 action 不是 hold 時勾選框必須可用').toBeEnabled();
    await attach.check();
    await expect(page.getByTestId('ws-lineage-note')).toBeVisible();

    const buildReview = page.getByTestId('order-build-review');
    await expect(buildReview).toBeEnabled();
    await buildReview.click();
    await expect(page.getByTestId('order-review')).toBeVisible();
    // The review prints the claimed origin, not a blank: a blank here is exactly
    // the「不會自動取最新估計」case, and this test is about the named one.
    const reviewLineage = page.getByTestId('order-review-lineage');
    await expect(reviewLineage).toHaveText(inferenceId);
    const printedKey = (await page.getByTestId('order-review-key').innerText()).trim();

    // 「review 時固定」 read off the screen rather than asserted about the code:
    // taking the name back is a different decision, so the earlier review must go
    // void instead of riding on its stamp, and putting the same name back must
    // restore the same review — same key, same lineage, nothing swapped.
    await attach.uncheck();
    await expect(page.getByTestId('order-review-stale')).toBeVisible();
    await expect(page.getByTestId('order-submit-manual'), 'void 的檢視必須不能送出').toBeDisabled();
    await attach.check();
    await expect(page.getByTestId('order-submit-manual')).toBeEnabled();
    await expect(page.getByTestId('order-review-lineage')).toHaveText(inferenceId);
    expect((await page.getByTestId('order-review-key').innerText()).trim()).toBe(printedKey);

    const before = await storedDecisions(page, portfolioId);
    const [resp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/manual-decision') && r.request().method() === 'POST',
        { timeout: 30000 },
      ),
      page.getByTestId('order-submit-manual').click(),
    ]);
    expect(resp.status(), `提交必須被接受，實際 ${resp.status()}`).toBe(200);

    // What actually crossed the wire, taken from this click's own request. This is
    // the half no earlier version of this file checked: the panel could have sent
    // the review and the assertion still passed with no field in it.
    const sent = resp.request().postDataJSON() as Record<string, unknown>;
    expect(sent.linked_inference_id, 'request body 必須帶著被點名那個 id').toBe(inferenceId);
    expect(sent.action, 'request body 的 action 必須就是檢視上那一個').toBe(ACTION);
    expect(sent.quantity, 'request body 的 quantity 必須就是輸入欄那一個').toBe(1);
    expect(sent.rationale).toBe(RATIONALE);
    expect(
      `${String(sent.idempotency_key ?? '').slice(0, 18)}…`,
      '送出的 idempotency key 必須就是檢視表印出來那一個（重試不換 key）',
    ).toBe(printedKey);

    const body = await resp.json().catch(() => null);
    // One press lands in three tables, and each is read back through its own formal
    // entry rather than letting one blob stand for the whole record:
    //   `simulation_decisions` — the replay row the on-screen receipt prints
    //     (ManualDecisionResponse.decision, app/api/simulations.py:2683-2690);
    //   `decision_records` — the account's decision history, written by this same
    //     request inside `_submit_manual_order` (app/api/simulations.py:2207-2215);
    //   `human_decision_events` — the event column added for this identity
    //     (app/api/simulations.py:2588-2611).
    // The one accepted press that does NOT write the second is a buy the account
    // cannot hold twice at this cursor: the route answers 200 with `rejection`
    // reason `already_in_position` and creates no paper-decision row
    // (app/api/simulations.py:2067-2080). That is reported as a skip, not passed off
    // as a lineage that survived.
    sessionId = String(body?.session?.id ?? '');
    decisionId = String(body?.decision?.id ?? '');
    expect(decisionId, 'the response must name the row it wrote').not.toBe('');
    expect(sessionId, 'the response must name the session it wrote into').not.toBe('');
    // The frozen session is the one this request was posted into, not whichever
    // session the panel is now looking at: the URL's segment and the session the
    // answer names have to be the same id.
    expect(
      resp.request().url(),
      `提交必須打在 review 固定那個 session 上（回應為 ${sessionId}）`,
    ).toContain(`/api/v1/simulations/${sessionId}/manual-decision`);
    // The cursor version rides on the review too. A press that records a decision
    // raises the session by exactly one (app/api/simulations.py:2563), so the number
    // that crossed the wire must be the version *before* this write — a panel that
    // re-read the session at submit would send the newer one and answer the server's
    // optimistic lock with the state it was supposed to be checking.
    const frozenVersion = Number(sent.expected_version);
    expect(
      Number.isInteger(frozenVersion),
      `request body 必須帶著 review 固定的 expected_version，實際 ${String(sent.expected_version)}`,
    ).toBe(true);
    expect(
      frozenVersion,
      '固定的版本必須恰好是寫入前那一版（回應裡的 session 已經進了一版）',
    ).toBe(Number(body?.session?.state_version) - 1);
    expect(
      String(body?.decision?.decision_detail?.linked_inference_id ?? ''),
      '回應帶回來的那一列就必須已經寫著血緣',
    ).toBe(inferenceId);

    // The row the browser renders is read back from the server's own field
    // (`decision_detail.linked_inference_id`, OrderPreviewPanel.tsx:1217-1218), so this
    // assertion is about the wire, not about the click.
    await expect(page.getByTestId('order-manual-lineage')).toHaveText(inferenceId);

    // Layer 1: the replay session's own list entry (app/api/simulations.py:1632).
    const replay = await api(page, 'GET', `/api/v1/simulations/${sessionId}/decisions`);
    expect(replay.status, 'session decisions must be readable as an oracle').toBe(200);
    const replayRows = (Array.isArray(replay.json) ? replay.json : []) as Array<Record<string, any>>;
    const replayRow = replayRows.find((d) => String(d?.id) === decisionId);
    expect(replayRow, '這條回放決策必須在 session 的正式讀回入口上').toBeTruthy();
    expect(
      String(replayRow!.decision_detail?.linked_inference_id ?? ''),
      '回放列的 decision_detail 必須帶著同一個 id',
    ).toBe(inferenceId);

    // Layer 2: the account's decision history (app/api/paper_portfolio.py:1113).
    const after = await storedDecisions(page, portfolioId);
    if (body?.rejection) {
      test.skip(
        true,
        `提交被受理但這一筆在建立決策列之前就被擋下（reason：${String(body.rejection.reason)}）。` +
          '這個入口此時不寫 decision_records，帳號決策歷史與匯出那兩層在這裡無列可讀；' +
          '換一個尚未持有該符號的游標重跑，才驗得到匯出那一層。',
      );
    }
    const fresh = after.filter((d) => !before.some((b) => b.id === d.id));
    expect(fresh.length, '一次成功下單的決策必須在帳號決策歷史裡多出恰好一列').toBe(1);
    recordId = String(fresh[0].id);
    expect(fresh[0].linked_inference_id, '持久化的 linked_inference_id 必須就是被點名那一個').toBe(inferenceId);
    expect(fresh[0].rationale, '那一列必須就是這次按下的理由').toBe(RATIONALE);
  });

  test('the export carries the same lineage after a reload', async () => {
    test.skip(
      !portfolioId || !inferenceId || !recordId,
      'depends on the two tests above finding a source and writing a decision row',
    );

    await page.reload();
    await page.getByTestId('watchlist-panel').waitFor({ state: 'visible', timeout: 30000 });
    await page.getByTestId('watchlist-panel').getByRole('button', { name: `Select ${ASSET}` }).click();
    await page.getByTestId('order-preview-panel').locator('#order-portfolio-select').selectOption(portfolioId);
    await expect(page.getByTestId('ws-lineage-source')).toContainText(`inference_id: ${inferenceId}`);

    // The formal read-back entries, not the screen: the list route
    // (app/api/paper_portfolio.py:1113, one scope function at :1096) and the export
    // route (:1831). The export's envelope names its rows `records` and keys the
    // decision `decision_id` (DECISION_HISTORY_COLUMNS, :1643-1672).
    const list = await storedDecisions(page, portfolioId);
    const row = list.find((d) => d.id === recordId);
    expect(row, 'reload 後這條決策仍必須在正式讀回入口上').toBeTruthy();
    expect(row!.linked_inference_id).toBe(inferenceId);

    const json = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/decision-history?format=json`);
    expect(json.status, 'decision-history 的 json 格式必須可讀').toBe(200);
    const history = (Array.isArray(json.json?.records) ? json.json.records : []) as DecisionHistoryRow[];
    const exported = history.filter((r) => String(r?.decision_id) === recordId);
    expect(exported.length, '決策歷史必須恰好列出這一筆').toBe(1);
    expect(String(exported[0].linked_inference_id ?? ''), '匯出的血緣欄必須就是被點名那一個').toBe(inferenceId);
    expect(String(exported[0].decision_scope ?? ''), '這一筆必須歸在本次選定的技能帳號上').toBe('this_portfolio');
    // The stronger half: the billing row this decision names still reads back for
    // this owner. `missing` would mean the id survived while its usage row did not
    // (column semantics, app/api/paper_portfolio.py:1693-1694).
    expect(
      String(exported[0].inference_source_state ?? ''),
      '血緣指向的計費列也必須仍讀得回來',
    ).toBe('present');

    const csv = await api(page, 'GET', `/api/v1/paper-portfolio/${portfolioId}/decision-history?format=csv`);
    expect(csv.status).toBe(200);
    expect(csv.text.split('\n')[0], '匯出必須有一欄寫著血緣').toMatch(/linked_inference_id/i);
    const csvRow = csv.text.split('\n').find((line) => line.includes(recordId));
    expect(csvRow, 'CSV 必須有這一筆的那一行').toBeTruthy();
    expect(csvRow!, 'CSV 的那一列必須帶著同一個 id').toContain(inferenceId);
  });

  test('a 409 recovers on the same screen: no reload, and the rebuild stamps the re-read version', async () => {
    test.skip(!sessionId, 'depends on the submit test above having bound a session');

    // One page instance for the whole node. A reload or any navigation throws the JS
    // realm away and takes the marker with it, so reading this value back at the end
    // is what 「不 reload、不重新導航」 means here — a measurement, not a description
    // of how the test happens to be written.
    const realm = `no-reload-${Date.now()}`;
    await page.evaluate((v) => {
      (window as unknown as Record<string, string>).__alethraE2ERealm = v;
    }, realm);

    // The version this review is about to freeze, taken from the server rather than
    // from anything the panel prints. Every comparison below runs against a number
    // read this way, never against `N + 1`: an arithmetic premise would pass on what
    // the code is supposed to do instead of on the state the door actually holds.
    const versionAtReview = await sessionVersion(page, sessionId);
    const keysBefore = await sessionDecisionKeys(page, sessionId);

    // The no-source branch of this door, which stays legal whatever the lineage
    // column does: a 「觀望」 records a decision and claims no model estimate. It is
    // also the smallest write that moves the session under a review already on
    // screen — the cursor date and bar do not change (app/api/simulations.py
    // :2551-2555 says only the decision count and the version move), so nothing but
    // the stamped version can catch it.
    await page.getByTestId('order-action').selectOption('hold');
    await page.getByTestId('order-rationale').fill('e2e：驗固定檢視的游標版本（觀望，不帶來源）');
    // A hold owns no lineage field: the offer is there to be declined, not to be
    // silently sent along (dailyEstimate.ts:270-274, and the disabled branch at
    // OrderPreviewPanel.tsx:959).
    await expect(page.getByTestId('ws-lineage-attach'), '觀望時勾選框必須是停用').toBeDisabled();
    await expect(page.getByTestId('ws-lineage-hold'), '觀望必須自己說出為什麼沒有來源').toBeVisible();

    const build = page.getByTestId('order-build-review');
    await expect(build).toBeEnabled();
    await build.click();
    const review = page.getByTestId('order-review');
    await expect(review).toBeVisible();

    // Someone else moves the session while this review sits on the screen.
    const competingKey = `e2e-competing-hold-${Date.now()}`;
    const competing = await api(page, 'POST', `/api/v1/simulations/${sessionId}/manual-decision`, {
      action: 'hold',
      rationale: 'e2e：在檢視之後移動 session 的另一筆合法觀望',
      idempotency_key: competingKey,
    });
    expect(competing.status, `沒有模型來源的一般決策必須被受理，實際 ${competing.status}`).toBe(200);
    const versionAfterMove = await sessionVersion(page, sessionId);
    expect(
      versionAfterMove,
      '那一筆合法決策必須真的把版本推進，否則下面那個 409 從來沒有發生過',
    ).not.toBe(versionAtReview);

    // The press now carries the number the review froze, not the newer one the
    // session holds, and the server's optimistic lock trips on it. A panel that read
    // the version at submit would send `versionAfterMove` and write over a state the
    // owner never reviewed — the guard would never fire on this door.
    const submit = page.getByTestId('order-submit-manual');
    await expect(submit).toBeEnabled();
    const [stale] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/manual-decision') && r.request().method() === 'POST',
        { timeout: 30000 },
      ),
      submit.click(),
    ]);
    const staleSent = stale.request().postDataJSON() as Record<string, unknown>;
    expect(
      Object.keys(staleSent).includes('linked_inference_id'),
      '觀望的固定 payload 不得帶著來源欄（缺失＝沒有宣稱來源，不是取最新）',
    ).toBe(false);
    expect(
      Number(staleSent.expected_version),
      '送出的必須是檢視固定的那一個版本，不是送出時才讀的最新版本',
    ).toBe(versionAtReview);
    expect(stale.status(), `舊檢視必須被樂觀鎖擋下，實際 ${stale.status()}`).toBe(409);
    expect((await stale.text()).toLowerCase(), '拒絕要說明是版本衝突').toContain('state version conflict');

    // Independent read-back keyed by the refused request's own idempotency key rather
    // than by a row count: the server stores that key inside the row it writes
    // (app/api/simulations.py:2471), so 「這一筆沒有留下資料」 is a statement about one
    // identifiable row and not about how many rows happen to exist.
    const afterStale = await sessionDecisionKeys(page, sessionId);
    expect(
      afterStale.filter((k) => k === String(staleSent.idempotency_key)),
      '被擋下的這一筆不能留下決策列',
    ).toHaveLength(0);
    expect(
      afterStale.filter((k) => k === competingKey),
      '那一筆合法觀望必須有列（沒有它，上面的版本推進與 409 都是憑空的）',
    ).toHaveLength(1);
    expect(afterStale.length, '從建立檢視到衝突為止只多了競爭那一筆').toBe(keysBefore.length + 1);

    // The voided review cannot be pressed again: the stamp is gone, so a second
    // submission is a decision the owner has to review once more.
    await expect(review, '衝突之後舊檢視必須作廢').toHaveCount(0);

    // This is the reload-free half. The panel re-reads the session inside its own 409
    // branch (OrderPreviewPanel.tsx:555-560) and installs it before it writes the
    // message the next line waits for (:561), so the message appearing means the
    // screen is already looking at the version the conflict named.
    await expect(page.getByTestId('order-error')).toContainText('State version conflict');
    expect(
      await page.evaluate(() => (window as unknown as Record<string, string>).__alethraE2ERealm),
      '重讀必須由面板自己做，不能靠重新載入頁面',
    ).toBe(realm);

    // Same screen, same form, the same button pressed again: no `goto`, no reload, no
    // re-picking of the instrument or the account.
    await expect(build, '衝突之後必須能在原畫面重新建立檢視').toBeEnabled();
    await build.click();
    await expect(review).toBeVisible();
    const [fresh] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/manual-decision') && r.request().method() === 'POST',
        { timeout: 30000 },
      ),
      page.getByTestId('order-submit-manual').click(),
    ]);
    const freshSent = fresh.request().postDataJSON() as Record<string, unknown>;
    expect(
      Number(freshSent.expected_version),
      '原位重建的檢視必須固定 409 之後重讀到的那一版（讀回值，不是 N+1 的算術）',
    ).toBe(versionAfterMove);
    expect(
      String(freshSent.idempotency_key),
      '重建的檢視必須換一把 key：同一把 key 帶著舊版本重試只會再撞同一個鎖',
    ).not.toBe(String(staleSent.idempotency_key));
    expect(fresh.status(), `不刷新畫面就必須能恢復，實際 ${fresh.status()}`).toBe(200);
    const freshBody = (await fresh.json()) as Record<string, any>;
    expect(String(freshBody?.decision?.id ?? ''), '這一筆要真的寫進決策列').not.toBe('');
    const afterFresh = await sessionDecisionKeys(page, sessionId);
    expect(
      afterFresh.filter((k) => k === String(freshSent.idempotency_key)),
      '恢復的這一筆必須出現在 session 自己的正式讀回入口上',
    ).toHaveLength(1);
    expect(
      await page.evaluate(() => (window as unknown as Record<string, string>).__alethraE2ERealm),
      '整個恢復路徑（含提交與讀回）沒有重新載入過頁面',
    ).toBe(realm);
    await expect(review, '成功之後檢視必須收掉').toHaveCount(0);
  });

  test('a session change the screen sees first voids the review, and no request leaves', async () => {
    test.skip(!sessionId, 'depends on the submit test above having bound a session');

    // The pause button is the on-screen fact this node needs: it renders only for an
    // active session (ReplayController.tsx:111), and WorkspaceShell.tsx:916 wires it
    // to `runReplayAction`, which replaces the session object the panel is reading
    // (:356 → :967). Without it there is no way for this screen to *see* a change,
    // and the claim would silently fall back to the reload path the node above
    // replaced.
    const status = page.getByTestId('replay-status');
    await expect(status, '回放列必須自己說出狀態').toBeVisible();
    const shown = (await status.innerText()).trim();
    if ((await page.getByTestId('replay-pause').count()) === 0) {
      test.skip(
        true,
        `回放狀態是「${shown}」，Pause 沒有渲染（ReplayController.tsx:111 只在 status==='active' 時渲染），` +
          '這一欄要證的是「畫面先看到 session 被動過」，沒有這個按鈕就造不出那個瞬間。',
      );
    }

    const versionBefore = await sessionVersion(page, sessionId);
    const keysBefore = await sessionDecisionKeys(page, sessionId);

    await page.getByTestId('order-action').selectOption('hold');
    await page.getByTestId('order-rationale').fill('e2e：檢視之後畫面自己看到 session 被暫停');
    await page.getByTestId('order-build-review').click();
    const review = page.getByTestId('order-review');
    await expect(review).toBeVisible();

    // Counted at the transport, so 「零 POST」 is about requests leaving this page and
    // not about a response that could never be matched to the right press.
    let manualPosts = 0;
    const count = (req: Request) => {
      if (req.method() === 'POST' && req.url().endsWith('/manual-decision')) manualPosts += 1;
    };
    page.on('request', count);
    try {
      await Promise.all([
        page.waitForResponse(
          (r) => r.url().endsWith('/pause') && r.request().method() === 'POST',
          { timeout: 30000 },
        ),
        page.getByTestId('replay-pause').click(),
      ]);
      // The frozen stamp carries the session's status (the `status` field of
      // `reviewStamp`, src/lib/reviewCurrency.ts, fed at OrderPreviewPanel.tsx:413),
      // so a pause the owner performs on this very screen voids the review by itself:
      // no request, no 409, nothing to reconcile.
      await expect(page.getByTestId('order-review-stale'), '畫面看到改變後必須自己宣告檢視作廢')
        .toBeVisible();
      await expect(page.getByTestId('order-submit-manual'), '作廢的檢視必須不能送出').toBeDisabled();

      // Two layers, and the second one gets its own positive control. A disabled
      // button stops a real click; the guard at the top of `submitManual` (:501)
      // stops a click that never went through a button at all. A programmatic
      // dispatch reaches React's root listener even on a disabled control, so the
      // propagation is asserted first — without it the zero below could be vacuous.
      const dispatched = await page.evaluate(() => {
        const el = document.querySelector('[data-testid="order-submit-manual"]');
        if (!el) return 'no-button';
        let reached = false;
        const on = () => {
          reached = true;
        };
        window.addEventListener('click', on);
        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true }));
        window.removeEventListener('click', on);
        return reached ? 'propagated' : 'suppressed';
      });
      expect(dispatched, '合成的 click 必須真的走到 React 的根監聽，否則下面的零 POST 是空的')
        .toBe('propagated');
      await page.waitForTimeout(500);
      expect(manualPosts, '畫面已先偵測到改變時，一個 request 都不應該離開').toBe(0);
    } finally {
      page.off('request', count);
    }

    // What the pause touched and did not touch, read back rather than assumed: no
    // decision row, and — unlike the write the node above fought — no version move
    // either (app/api/simulations.py:1882-1897). So this refusal is the screen's own
    // stamp and not a conflict it happened to predict; the route's status gate
    // (:2409-2410) would have refused it too, and the point of the two assertions
    // above is that the request never got that far.
    expect(await sessionVersion(page, sessionId), 'Pause 不得動到 state_version').toBe(versionBefore);
    expect(
      (await sessionDecisionKeys(page, sessionId)).length,
      'Pause 一筆決策都不能寫',
    ).toBe(keysBefore.length);

    // Resume restores the state the review described, and the void is a comparison
    // against the current session rather than a one-way latch: the same review is
    // pressable again. It was never submitted (`manualPosts` above says so), and the
    // panel does not claim it was; the press is cancelled here so the screen is left
    // with nothing pending for the node that follows.
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith('/resume') && r.request().method() === 'POST',
        { timeout: 30000 },
      ),
      page.getByTestId('replay-resume').click(),
    ]);
    await expect(page.getByTestId('order-review-stale'), '狀態回到檢視描述的那個樣子，作廢訊息必須消失')
      .toHaveCount(0);
    await page.getByTestId('order-cancel-review').click();
    await expect(review).toHaveCount(0);
    expect(manualPosts, '取消檢視也不該送出決策').toBe(0);
  });

  test('a session whose payload has no version sends no lock at all — a separate shape, not lock evidence', async () => {
    test.skip(!sessionId, 'depends on the submit test above having bound a session');

    // The shape a client meets when `state_version` is simply not in what it was
    // given: `frozenVersion` answers null (src/lib/reviewCurrency.ts), `buildReview`
    // freezes null (OrderPreviewPanel.tsx:491) and `submitManual` then leaves the key
    // out of the body (:513) — and the route compares nothing when the field is
    // absent (app/api/simulations.py:2420). That is the *absence* of the optimistic
    // lock, so a decision written under this shape can overwrite a state nobody
    // reviewed. It must never be booked as the lock having passed.
    //
    // Staged by rewriting one GET response, because no session on a current stack is
    // served without the column. The interception changes only what this browser sees;
    // it writes nothing anywhere, and the route is removed again below.
    const strip = `simulations/${sessionId}`;
    let stripped = 0;
    await page.route((u) => u.pathname.endsWith(`/${strip}`), async (route: Route) => {
      const res = await route.fetch();
      const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
      if (body && Object.keys(body).includes('state_version')) {
        delete body.state_version;
        stripped += 1;
      }
      await route.fulfill({
        status: res.status(),
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      });
    });

    try {
      // A reload, not the panel's own re-read: this shape only exists for a client
      // that never saw the field, so re-entering the workspace is the honest staging.
      await page.reload();
      await page.getByTestId('watchlist-panel').waitFor({ state: 'visible', timeout: 30000 });
      await page.getByTestId('watchlist-panel').getByRole('button', { name: `Select ${ASSET}` }).click();
      await page
        .getByTestId('order-preview-panel')
        .locator('#order-portfolio-select')
        .selectOption(portfolioId);
      expect(stripped, 'GET session 的回應必須真的被改寫過，否則這一節測的是新版本').toBeGreaterThan(0);

      // Move the session first, exactly like the recovery node did. Under the lock
      // this same sequence answers 409; with no version to send it cannot.
      const competingKey = `e2e-legacy-hold-${Date.now()}`;
      const competing = await api(page, 'POST', `/api/v1/simulations/${sessionId}/manual-decision`, {
        action: 'hold',
        rationale: 'e2e：在舊形態檢視之前移動 session 的合法觀望',
        idempotency_key: competingKey,
      });
      expect(competing.status, `競爭那一筆必須被受理，實際 ${competing.status}`).toBe(200);
      const versionNow = await sessionVersion(page, sessionId);

      await page.getByTestId('order-action').selectOption('hold');
      await page.getByTestId('order-rationale').fill('e2e：沒有版本的舊形態（觀望）');
      const build = page.getByTestId('order-build-review');
      await expect(build, '面板讀不到版本時仍然要能建立檢視（這一欄是旧行為，不是錯誤）')
        .toBeEnabled();
      await build.click();
      await expect(page.getByTestId('order-review')).toBeVisible();
      const [noLock] = await Promise.all([
        page.waitForResponse(
          (r) => r.url().includes('/manual-decision') && r.request().method() === 'POST',
          { timeout: 30000 },
        ),
        page.getByTestId('order-submit-manual').click(),
      ]);
      const sent = noLock.request().postDataJSON() as Record<string, unknown>;
      expect(
        Object.keys(sent).includes('expected_version'),
        '讀不到版本時連欄位都不應該出現在 body（缺失＝沒有鎖，不是送 null 讓伺服器比對）',
      ).toBe(false);
      expect(
        noLock.status(),
        `這一節的重點就是「沒有版本可比、所以照樣寫进去」：實際 ${noLock.status()}。` +
          '把它當成樂觀鎖通過就是記錯帳',
      ).toBe(200);
      const afterNoLock = await sessionDecisionKeys(page, sessionId);
      expect(
        afterNoLock.filter((k) => k === String(sent.idempotency_key)),
        '被受理就必須真的有列（這一筆是在別人也寫過之後落下的，正是沒有鎖的後果）',
      ).toHaveLength(1);
      expect(
        await sessionVersion(page, sessionId),
        '這一筆寫在原來的版本之上：版本比對從未發生',
      ).not.toBe(versionNow);
      // The receipt on the screen is the server's own row, not the browser's claim.
      expect(String(((await noLock.json()) as Record<string, any>)?.decision?.id ?? '')).not.toBe('');
    } finally {
      await page.unroute((u) => u.pathname.endsWith(`/${strip}`));
    }
  });
});

/** Read-only oracle over the formal list entry (app/api/paper_portfolio.py:1113). */
async function storedDecisions(page: Page, id: string): Promise<DecisionRow[]> {
  const { status, json } = await api(page, 'GET', `/api/v1/paper-portfolio/${id}/decisions`);
  expect(status, 'the decision list must be readable as an oracle').toBe(200);
  return (Array.isArray(json) ? json : []) as DecisionRow[];
}

/**
 * The session's own optimistic-lock counter, read through the formal GET entry
 * (`app/api/simulations.py:2300`'s sibling `GET /{session_id}`). Every version
 * claim in the last two nodes compares against a value obtained here, never
 * against an expected arithmetic step.
 */
async function sessionVersion(page: Page, id: string): Promise<number> {
  const { status, json } = await api(page, 'GET', `/api/v1/simulations/${id}`);
  expect(status, 'session 必須讀得回來才能核對版本').toBe(200);
  const raw = (json ?? {}).state_version;
  const version = Number(raw);
  expect(
    Number.isInteger(version),
    `oracle 必須回報 session 的 state_version，實際 ${String(raw)}`,
  ).toBe(true);
  return version;
}

/**
 * Every idempotency key the replay session exposes, through the session's own
 * list entry (app/api/simulations.py:1632). The key is stored inside the row
 * (:2471), which is what lets a refused write be disproved by name instead of
 * by counting rows and hoping nothing else moved.
 */
async function sessionDecisionKeys(page: Page, id: string): Promise<Array<string | null>> {
  const { status, json } = await api(page, 'GET', `/api/v1/simulations/${id}/decisions`);
  expect(status, '決策列表必須讀得回來才能數這一筆寫了沒有').toBe(200);
  const rows = (Array.isArray(json) ? json : []) as Array<Record<string, any>>;
  return rows.map((row) => {
    const key = row?.decision_detail?.idempotency_key;
    return key === undefined || key === null ? null : String(key);
  });
}
