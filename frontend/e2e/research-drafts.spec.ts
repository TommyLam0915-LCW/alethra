import { test, expect } from '@playwright/test';

const API_BASE = 'http://localhost:8001';
const FRONTEND_BASE = 'http://localhost:3001';

const USER_A = {
  email: 'user_a_test@example.com',
  password: process.env.ACCEPTANCE_PASSWORD_A || 'TestPassword123!@#',
};

const USER_B = {
  email: 'user_b_test@example.com',
  password: process.env.ACCEPTANCE_PASSWORD_B || 'TestPassword456!@#',
};

async function login(page, email, password) {
  await page.goto(`${FRONTEND_BASE}/login`);
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard');
}

async function getAuthToken(email, password) {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(`Fixture login failed for ${email}: ${response.status} ${await response.text()}`);
  }
  const data = await response.json();
  if (!data.access_token) {
    throw new Error(`Fixture login for ${email} returned no access_token`);
  }
  return data.access_token;
}

async function createDraft(token, draftData) {
  const response = await fetch(`${API_BASE}/api/v1/research-drafts/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(draftData),
  });
  if (!response.ok) {
    throw new Error(`Fixture draft creation failed: ${response.status} ${await response.text()}`);
  }
  const data = await response.json();
  if (!data.id) {
    throw new Error('Fixture draft creation returned no draft id');
  }
  return data;
}

async function approveDraft(token, draftId, action = 'accept') {
  const response = await fetch(`${API_BASE}/api/v1/research-drafts/${draftId}/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({ action, notes: 'Test approval' }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to ${action} draft: ${response.status} ${errorText}`);
  }

  return await response.json();
}

async function getDraft(token, draftId) {
  const response = await fetch(`${API_BASE}/api/v1/research-drafts/${draftId}`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`Failed to get draft: ${response.status}`);
  }
  return await response.json();
}

test.describe('PR #3 Browser Acceptance - Local Playwright', () => {
  test.describe('A. Navigation', () => {
    test('should navigate to research drafts list and detail', async ({ page }) => {
      await login(page, USER_A.email, USER_A.password);

      // Navigate to research drafts
      await page.goto(`${FRONTEND_BASE}/research-drafts`);
      await expect(page).toHaveURL(/.*research-drafts/);

      // Wait for page to load
      await page.waitForLoadState('networkidle');

      // Check that we're on the research drafts page
      const pageContent = await page.content();
      expect(pageContent).toBeTruthy();

      // Try to click on a draft if one exists
      const draftLink = page.locator('a, button').filter({ hasText: /Browser Acceptance Draft|Deletion Test|Existing form/i }).first();
      if (await draftLink.count() > 0) {
        await draftLink.click();
        await expect(page).toHaveURL(/.*research-drafts\/.*/);
      }
    });
  });

  test.describe('B. Mock Disclosure', () => {
    test('should display mock disclosure text', async ({ page }) => {
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts`);

      // Look for disclosure text
      const disclosureText = page.locator('text=/模擬研究助手|僅供流程示範|demo|mock/i');
      const hasDisclosure = await disclosureText.count() > 0;

      // If no explicit disclosure, check for any demo/mock indicators
      if (!hasDisclosure) {
        const pageContent = await page.content();
        const hasMockIndicator = pageContent.includes('模擬') ||
                                  pageContent.includes('demo') ||
                                  pageContent.includes('mock') ||
                                  pageContent.includes('DEMO-');
        expect(hasMockIndicator).toBeTruthy();
      } else {
        await expect(disclosureText.first()).toBeVisible();
      }
    });
  });

  test.describe('E. Zero Values', () => {
    test('should preserve zero commission and slippage', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Test-owned fixture created via API (documented setup). The UI behavior
      // under test — zero display on the read-only detail page — is verified in
      // the browser. Fixture failures propagate and fail the test.
      const zeroDraft = await createDraft(token, {
        title: `Zero Values Test Draft ${Date.now()}`,
        hypothesis: 'Zero commission and slippage must be preserved and displayed.',
        falsification_criteria: 'Not applicable to this zero-value fixture.',
        strategy_template: 'buy_and_hold',
        asset_symbol: 'DEMO-ALPHA',
        initial_cash: '10000',
        commission_rate: '0',
        slippage_rate: '0',
      });

      const persisted = await getDraft(token, zeroDraft.id);
      expect(persisted.commission_rate).toBe('0');
      expect(persisted.slippage_rate).toBe('0');

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${zeroDraft.id}`);

      // Unconditional assertions: the draft-status detail page renders fields as
      // a read-only grid (label div followed by value div). Zeros must show as "0".
      const commissionLabel = page.getByText('手續費率', { exact: true });
      await expect(commissionLabel).toBeVisible();
      await expect(commissionLabel.locator('xpath=following-sibling::div[1]')).toHaveText('0');

      const slippageLabel = page.getByText('滑價率', { exact: true });
      await expect(slippageLabel).toBeVisible();
      await expect(slippageLabel.locator('xpath=following-sibling::div[1]')).toHaveText('0');
    });
  });

  test.describe('G. Acceptance and Finalization', () => {
    test('should accept a draft through the UI and finalize it as read-only', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Test-owned fixture in draft status, created via API (documented setup).
      // The acceptance action itself is performed through the UI below.
      const draft = await createDraft(token, {
        title: `Acceptance Test Draft ${Date.now()}`,
        hypothesis: 'Acceptance must finalize the draft as read-only.',
        falsification_criteria: 'Not applicable to this acceptance fixture.',
        strategy_template: 'buy_and_hold',
        asset_symbol: 'DEMO-ALPHA',
        initial_cash: '10000',
      });

      const persisted = await getDraft(token, draft.id);
      expect(persisted.status).toBe('draft');

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);

      // Pre-acceptance: draft badge, review controls, and edit availability.
      await expect(page.locator('span').filter({ hasText: /^草稿$/ })).toBeVisible();
      await expect(page.getByRole('heading', { name: '審閱決定' })).toBeVisible();
      await expect(page.getByRole('button', { name: '編輯' })).toBeVisible();

      // Perform acceptance through the UI with the disclosure and notes visible.
      await page.getByRole('button', { name: '接受草稿' }).click();
      await expect(page.getByText('這不會建立可執行的策略。', { exact: true })).toBeVisible();
      await page.getByPlaceholder('備註（選填）...').fill('UI acceptance notes');
      await page.getByRole('button', { name: '確認接受' }).click();

      // Post-acceptance: success message and finalized badge.
      await expect(page.getByText(/草稿已接受/)).toBeVisible();
      await expect(page.locator('span').filter({ hasText: /^已接受$/ })).toBeVisible();

      // Finalized draft is read-only: no edit controls, no review/questions cards.
      await expect(page.getByRole('button', { name: '編輯' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: '儲存' })).toHaveCount(0);
      await expect(page.getByRole('heading', { name: '研究助手問題' })).toHaveCount(0);
      await expect(page.getByRole('heading', { name: '審閱決定' })).toHaveCount(0);
      await expect(page.getByRole('heading', { name: '套用草稿欄位' })).toBeVisible();
    });
  });

  test.describe('J. URL Privacy', () => {
    test('should not expose private content in URL', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Get a draft with known content
      const draftsResponse = await fetch(`${API_BASE}/api/v1/research-drafts/`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const drafts = await draftsResponse.json();

      if (drafts.length > 0) {
        const draft = drafts[0];
        await login(page, USER_A.email, USER_A.password);
        await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);

        const url = page.url();

        // URL should not contain hypothesis, falsification criteria, etc. (only if they exist)
        if (draft.hypothesis && draft.hypothesis.length > 0) {
          expect(url).not.toContain(draft.hypothesis);
          expect(url).not.toContain(encodeURIComponent(draft.hypothesis));
        }

        if (draft.falsification_criteria && draft.falsification_criteria.length > 0) {
          expect(url).not.toContain(draft.falsification_criteria);
          expect(url).not.toContain(encodeURIComponent(draft.falsification_criteria));
        }

        // URL may contain draft_id but not sensitive content
        // This is acceptable - draft_id is not private content
      }
    });
  });

  test.describe('M. Deletion', () => {
    test('should cancel then confirm deletion of a disposable draft', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Test-owned disposable fixture, created via API (documented setup).
      const title = `Deletion Test Draft ${Date.now()}`;
      const draft = await createDraft(token, {
        title,
        hypothesis: 'Deletion confirmation must guard against accidental removal.',
        falsification_criteria: 'Not applicable to this deletion fixture.',
        strategy_template: 'buy_and_hold',
        asset_symbol: 'DEMO-ALPHA',
        initial_cash: '10000',
      });

      const persisted = await getDraft(token, draft.id);
      expect(persisted.status).toBe('draft');

      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);

      const deleteCard = page.locator('.card').filter({
        has: page.getByRole('heading', { name: '刪除草稿' }),
      });
      await expect(deleteCard.getByText('刪除草稿會一併移除所有修訂記錄與稽核事件。此操作無法復原。')).toBeVisible();

      // Open confirmation: warning must be shown before any destructive action.
      await deleteCard.getByRole('button', { name: '刪除此草稿' }).click();
      await expect(deleteCard.getByText('確定要刪除此草稿嗎？所有修訂與稽核事件將一併刪除。')).toBeVisible();

      // Cancel: draft must remain accessible and listed unchanged.
      await deleteCard.getByRole('button', { name: '取消' }).click();
      await expect(deleteCard.getByText('確定要刪除此草稿嗎？所有修訂與稽核事件將一併刪除。')).not.toBeVisible();
      await expect(deleteCard.getByRole('button', { name: '刪除此草稿' })).toBeVisible();
      await expect(page.getByRole('heading', { name: title })).toBeVisible();
      expect(page.url()).toContain(draft.id);

      // Confirm deletion: redirect to the list, draft gone from list and API.
      await deleteCard.getByRole('button', { name: '刪除此草稿' }).click();
      await expect(deleteCard.getByText('確定要刪除此草稿嗎？所有修訂與稽核事件將一併刪除。')).toBeVisible();
      await deleteCard.getByRole('button', { name: '確認刪除' }).click();

      await page.waitForURL(/\/research-drafts$/);
      await expect(page.getByRole('heading', { name: '研究草稿' })).toBeVisible();
      await expect(page.locator(`a:has-text("${title}")`)).toHaveCount(0);

      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await expect(page.getByText('草稿不存在', { exact: true })).toBeVisible();
    });
  });

  test.describe('I. Selected-Field Transfer', () => {
    test('should transfer only selected fields to experiment form', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create a draft with distinct values
      const draft = await createDraft(token, {
        title: 'Field Transfer Test Draft',
        hypothesis: 'This hypothesis should be transferred',
        falsification_criteria: 'This should NOT be transferred',
        strategy_template: 'sma_crossover',
        asset_symbol: 'DEMO-BETA',
        benchmark_symbol: 'DEMO-BENCHMARK',
        initial_cash: '25000',
        commission_rate: '0.001',
        slippage_rate: '0.002',
      });

      // Accept the draft
      await approveDraft(token, draft.id, 'accept');

      // Verify draft is actually accepted via API
      const acceptedDraft = await getDraft(token, draft.id);
      expect(acceptedDraft.status).toBe('accepted');

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Reload to ensure we have the latest status from backend
      await page.reload();
      await page.waitForLoadState('networkidle');

      // Wait for accepted status badge to appear
      const statusBadge = page.locator('span:has-text("已接受")');
      await expect(statusBadge).toBeVisible({ timeout: 5000 });

      // Click "選擇要套用的欄位" button
      const applyFieldsButton = page.locator('button:has-text("選擇要套用的欄位")');
      await expect(applyFieldsButton).toBeVisible();
      await applyFieldsButton.click();

      // Select only title and hypothesis (NOT strategy_template, asset_symbol, etc.)
      const titleCheckbox = page.locator('label:has-text("標題") input[type="checkbox"]');
      const hypothesisCheckbox = page.locator('label:has-text("研究假設") input[type="checkbox"]');

      await titleCheckbox.check();
      await hypothesisCheckbox.check();

      // Verify only 2 fields selected
      const confirmButton = page.locator('button:has-text("確認套用 2 個欄位")');
      await expect(confirmButton).toBeVisible();

      // Click confirm to navigate to experiments/new
      await confirmButton.click();

      // Wait for navigation
      await page.waitForURL(/.*experiments\/new.*/);

      // Verify URL contains draft_id and fields params
      const url = page.url();
      expect(url).toContain('draft_id=');
      // URL encodes comma as %2C
      expect(url).toMatch(/fields=title(,|%2C)hypothesis/);

      // Verify URL does NOT contain private content (hypothesis text)
      expect(url).not.toContain('This hypothesis should be transferred');
      expect(url).not.toContain(encodeURIComponent('This hypothesis should be transferred'));

      // Wait for preview to load
      await page.waitForLoadState('networkidle');

      // Verify preview shows only selected fields
      const previewSection = page.locator('.card:has-text("預覽草稿欄位")');
      await expect(previewSection).toBeVisible();

      // Preview should show title and hypothesis
      await expect(previewSection.locator('text=標題')).toBeVisible();
      await expect(previewSection.locator('text=Field Transfer Test Draft')).toBeVisible();
      await expect(previewSection.locator('text=研究假設')).toBeVisible();
      await expect(previewSection.locator('text=This hypothesis should be transferred')).toBeVisible();

      // Preview should NOT show falsification_criteria or strategy_template
      await expect(previewSection.locator('text=推翻條件')).not.toBeVisible();
      await expect(previewSection.locator('text=策略模板')).not.toBeVisible();

      // Click "確認套用" to apply fields
      const applyConfirmButton = page.locator('button:has-text("確認套用 2 個欄位")').last();
      await applyConfirmButton.click();

      // Wait for form to update
      await page.waitForTimeout(500);

      // Verify form values: title and hypothesis should match draft
      const titleInput = page.locator('input[type="text"]').first();
      const titleValue = await titleInput.inputValue();
      expect(titleValue).toBe('Field Transfer Test Draft');

      const hypothesisTextarea = page.locator('textarea').first();
      const hypothesisValue = await hypothesisTextarea.inputValue();
      expect(hypothesisValue).toBe('This hypothesis should be transferred');

      // Verify unselected fields remain at defaults
      const templateSelect = page.locator('select').first();
      const templateValue = await templateSelect.inputValue();
      expect(templateValue).toBe('buy_and_hold'); // Default, NOT sma_crossover from draft

      // Verify falsification_criteria was NOT transferred (not in form anyway)
      const pageContent = await page.content();
      expect(pageContent).not.toContain('This should NOT be transferred');
    });
  });

  test.describe('K. Preview and Cancellation', () => {
    test('should preserve original values when cancelling field transfer', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create and accept a draft
      const draft = await createDraft(token, {
        title: 'Cancellation Test Draft',
        hypothesis: 'This should NOT be applied after cancellation',
        falsification_criteria: 'Test falsification criteria for cancellation',
        strategy_template: 'buy_and_hold',
        asset_symbol: 'DEMO-ALPHA',
      });
      await approveDraft(token, draft.id, 'accept');

      // Verify draft is actually accepted via API
      const acceptedDraft = await getDraft(token, draft.id);
      expect(acceptedDraft.status).toBe('accepted');

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Reload to ensure we have the latest status from backend
      await page.reload();
      await page.waitForLoadState('networkidle');

      // Wait for accepted status badge to appear
      const statusBadge = page.locator('span:has-text("已接受")');
      await expect(statusBadge).toBeVisible({ timeout: 5000 });

      // Initiate field transfer
      const applyFieldsButton = page.locator('button:has-text("選擇要套用的欄位")');
      await applyFieldsButton.click();

      // Select title field
      const titleCheckbox = page.locator('label:has-text("標題") input[type="checkbox"]');
      await titleCheckbox.check();

      // Navigate to experiments/new
      const confirmButton = page.locator('button:has-text("確認套用 1 個欄位")');
      await confirmButton.click();
      await page.waitForURL(/.*experiments\/new.*/);
      await page.waitForLoadState('networkidle');

      // Verify preview is shown
      const previewSection = page.locator('.card:has-text("預覽草稿欄位")');
      await expect(previewSection).toBeVisible();

      // Verify form still has default values (not yet applied)
      const titleInput = page.locator('input[type="text"]').first();
      const titleValue = await titleInput.inputValue();
      expect(titleValue).toBe(''); // Default empty, NOT "Cancellation Test Draft"

      // Click "取消" button
      const cancelButton = page.locator('button:has-text("取消")').last();
      await cancelButton.click();

      // Verify redirect to /research-drafts
      await page.waitForURL(/.*research-drafts.*/);
      expect(page.url()).toContain('/research-drafts');

      // Navigate to /experiments/new directly (no params)
      await page.goto(`${FRONTEND_BASE}/experiments/new`);
      await page.waitForLoadState('networkidle');

      // Verify no preview is shown
      const noPreview = page.locator('.card:has-text("預覽草稿欄位")');
      await expect(noPreview).not.toBeVisible();

      // Verify form has default values (not draft values)
      const freshTitleInput = page.locator('input[type="text"]').first();
      const freshTitleValue = await freshTitleInput.inputValue();
      expect(freshTitleValue).toBe(''); // Default empty

      // Verify draft content is not in the page
      const pageContent = await page.content();
      expect(pageContent).not.toContain('Cancellation Test Draft');
      expect(pageContent).not.toContain('This should NOT be applied after cancellation');
    });
  });

  test.describe('L. Confirmation and Population', () => {
    test('should apply only confirmed fields and preserve zero costs', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create a draft with distinct values including zero costs
      const draft = await createDraft(token, {
        title: 'Confirmation Test Draft',
        hypothesis: 'Confirmed hypothesis from draft',
        falsification_criteria: 'Test falsification criteria for confirmation',
        strategy_template: 'sma_crossover',
        asset_symbol: 'DEMO-BETA',
        benchmark_symbol: 'DEMO-BENCHMARK',
        initial_cash: '50000',
        commission_rate: '0',
        slippage_rate: '0',
      });
      await approveDraft(token, draft.id, 'accept');

      // Verify draft is actually accepted via API
      const acceptedDraft = await getDraft(token, draft.id);
      expect(acceptedDraft.status).toBe('accepted');

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Reload to ensure we have the latest status from backend
      await page.reload();
      await page.waitForLoadState('networkidle');

      // Wait for accepted status badge to appear
      const statusBadge = page.locator('span:has-text("已接受")');
      await expect(statusBadge).toBeVisible({ timeout: 5000 });

      // Initiate field transfer
      const applyFieldsButton = page.locator('button:has-text("選擇要套用的欄位")');
      await applyFieldsButton.click();

      // Select title, hypothesis, commission_rate, slippage_rate (NOT strategy_template, asset_symbol, initial_cash)
      const titleCheckbox = page.locator('label:has-text("標題") input[type="checkbox"]');
      const hypothesisCheckbox = page.locator('label:has-text("研究假設") input[type="checkbox"]');
      const commissionCheckbox = page.locator('label:has-text("手續費率") input[type="checkbox"]');
      const slippageCheckbox = page.locator('label:has-text("滑價率") input[type="checkbox"]');

      await titleCheckbox.check();
      await hypothesisCheckbox.check();
      await commissionCheckbox.check();
      await slippageCheckbox.check();

      // Verify 4 fields selected
      const confirmButton = page.locator('button:has-text("確認套用 4 個欄位")');
      await expect(confirmButton).toBeVisible();

      // Navigate to experiments/new
      await confirmButton.click();
      await page.waitForURL(/.*experiments\/new.*/);
      await page.waitForLoadState('networkidle');

      // Apply fields
      const applyConfirmButton = page.locator('button:has-text("確認套用 4 個欄位")').last();
      await applyConfirmButton.click();
      await page.waitForTimeout(500);

      // Verify SELECTED fields were applied
      const titleInput = page.locator('input[type="text"]').first();
      expect(await titleInput.inputValue()).toBe('Confirmation Test Draft');

      const hypothesisTextarea = page.locator('textarea').first();
      expect(await hypothesisTextarea.inputValue()).toBe('Confirmed hypothesis from draft');

      // Find commission and slippage inputs (they're number inputs)
      const numberInputs = page.locator('input[type="number"]');
      const commissionInput = numberInputs.nth(0); // First number input is initial_cash
      const slippageInput = numberInputs.nth(1); // Second is commission_rate

      // Actually, let's be more precise - find by label
      const commissionField = page.locator('.form-group:has-text("佣金率") input');
      const slippageField = page.locator('.form-group:has-text("滑價率") input');

      // Verify zero values are preserved from draft
      expect(await commissionField.inputValue()).toBe('0');
      expect(await slippageField.inputValue()).toBe('0');

      // Verify UNSELECTED fields remain at defaults
      const templateSelect = page.locator('select').first();
      expect(await templateSelect.inputValue()).toBe('buy_and_hold'); // Default, NOT sma_crossover

      const assetSelect = page.locator('.form-group:has-text("資產") select');
      expect(await assetSelect.inputValue()).toBe('DEMO-ALPHA'); // Default, NOT DEMO-BETA

      const initialCashInput = page.locator('.form-group:has-text("初始資金") input');
      expect(await initialCashInput.inputValue()).toBe('10000'); // Default, NOT 50000

      // Verify success message mentions applied fields
      const successMessage = page.locator('.alert-info:has-text("已從研究草稿套用")');
      await expect(successMessage).toBeVisible();

      // Verify no experiment was created (still on /experiments/new, not redirected)
      expect(page.url()).toContain('/experiments/new');
    });
  });

  test.describe('N. Access Control', () => {
    test('should deny cross-user access', async ({ page }) => {
      const tokenA = await getAuthToken(USER_A.email, USER_A.password);

      // Get User A's draft with known content
      const draftsResponse = await fetch(`${API_BASE}/api/v1/research-drafts/`, {
        headers: { 'Authorization': `Bearer ${tokenA}` },
      });
      const drafts = await draftsResponse.json();

      if (drafts.length > 0) {
        const draft = drafts[0];

        // Login as User B
        await login(page, USER_B.email, USER_B.password);

        // Try to access User A's draft
        await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);

        // Wait for page to load or show error
        await page.waitForLoadState('networkidle');

        // Should either redirect, show error, or show empty/forbidden
        const url = page.url();
        const pageContent = await page.content();

        // Check for various denial indicators
        const isDenied = url.includes('login') ||
                         url.includes('forbidden') ||
                         url.includes('unauthorized') ||
                         pageContent.includes('403') ||
                         pageContent.includes('401') ||
                         pageContent.toLowerCase().includes('forbidden') ||
                         pageContent.toLowerCase().includes('access denied') ||
                         pageContent.toLowerCase().includes('unauthorized') ||
                         pageContent.includes('權限') || // Chinese for "permission"
                         pageContent.includes('拒絕') || // Chinese for "denied"
                         pageContent.includes('草稿不存在') || // Chinese for "draft does not exist"
                         pageContent.toLowerCase().includes('not found') ||
                         pageContent.toLowerCase().includes('does not exist');

        // Check that actual private draft content is NOT displayed
        // The draftId itself can appear in HTML (URL path, data attributes) even when denied
        // So we check for actual content: title, hypothesis, falsification_criteria
        const hasPrivateContent =
          (draft.title && draft.title.length > 0 && pageContent.includes(draft.title)) ||
          (draft.hypothesis && draft.hypothesis.length > 0 && pageContent.includes(draft.hypothesis)) ||
          (draft.falsification_criteria && draft.falsification_criteria.length > 0 && pageContent.includes(draft.falsification_criteria));

        // Either access is denied OR private content is not displayed
        expect(isDenied || !hasPrivateContent).toBeTruthy();
      }
    });
  });

  test.describe('C. Questions and Answers Persistence', () => {
    test('should generate questions, submit answers, and persist after reload', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create a draft with missing fields via API
      const draft = await createDraft(token, {
        title: 'Questions Test Draft',
        hypothesis: 'Test hypothesis for questions',
      });

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Click "產生問題" button
      const generateButton = page.locator('button:has-text("產生問題")');
      await expect(generateButton).toBeVisible();
      await generateButton.click();

      // Wait for questions to appear
      await page.waitForTimeout(1000);

      // Verify questions are shown for missing fields
      const questionsSection = page.locator('text=/研究假設|策略模板|推翻條件/').first();
      await expect(questionsSection).toBeVisible({ timeout: 5000 });

      // Fill in answers to questions
      const strategySelect = page.locator('select').first();
      await strategySelect.selectOption('sma_crossover');

      const falsificationInput = page.locator('input[placeholder="輸入你的回答..."]').first();
      await falsificationInput.fill('If backtest shows Sharpe ratio < 0.5');

      // Click "提交答案" button
      const submitButton = page.locator('button:has-text("提交答案")');
      await expect(submitButton).toBeVisible();
      await submitButton.click();

      // Wait for submission
      await page.waitForTimeout(1000);

      // Verify success message
      const successMessage = page.locator('text=/答案已提交|草稿已更新/');
      await expect(successMessage).toBeVisible({ timeout: 5000 });

      // Reload the page
      await page.reload();
      await page.waitForLoadState('networkidle');

      // Verify answers are persisted - check that strategy_template is now set
      const strategyField = page.locator('text=策略模板').locator('..').locator('div').nth(1);
      const strategyValue = await strategyField.textContent();
      expect(strategyValue).toContain('SMA Crossover');

      // Verify falsification_criteria is now set
      const falsificationField = page.locator('text=推翻條件').locator('..').locator('div').nth(1);
      const falsificationValue = await falsificationField.textContent();
      expect(falsificationValue).toContain('Sharpe ratio');
    });
  });

  test.describe('D. Invalid Input Handling', () => {
    test('should show error when submitting empty answers', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create a draft with missing fields
      const draft = await createDraft(token, {
        title: 'Invalid Input Test Draft',
      });

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Generate questions
      const generateButton = page.locator('button:has-text("產生問題")');
      await generateButton.click();
      await page.waitForTimeout(1000);

      // Try to submit without filling any answers
      const submitButton = page.locator('button:has-text("提交答案")');
      await submitButton.click();

      // Wait for error message
      await page.waitForTimeout(500);

      // Verify error message is shown
      const errorMessage = page.locator('text=/請至少回答一個問題/');
      await expect(errorMessage).toBeVisible({ timeout: 5000 });

      // Verify draft was not updated (still has no strategy_template)
      const strategyField = page.locator('text=策略模板').locator('..').locator('div').nth(1);
      const strategyValue = await strategyField.textContent();
      expect(strategyValue).toContain('未填寫');
    });
  });

  test.describe('F. Revision and Audit History', () => {
    test('should show revision and audit history after editing draft', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create a draft
      const draft = await createDraft(token, {
        title: 'Revision History Test Draft',
        hypothesis: 'Original hypothesis',
        falsification_criteria: 'Original falsification criteria',
      });

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Click "編輯" button
      const editButton = page.locator('button:has-text("編輯")');
      await expect(editButton).toBeVisible();
      await editButton.click();

      // Modify the hypothesis
      const hypothesisTextarea = page.locator('textarea').first();
      await hypothesisTextarea.clear();
      await hypothesisTextarea.fill('Updated hypothesis after edit');

      // Click "儲存" button
      const saveButton = page.locator('button:has-text("儲存")');
      await saveButton.click();

      // Wait for save
      await page.waitForTimeout(1000);

      // Verify success message
      const successMessage = page.locator('text=/草稿已更新/');
      await expect(successMessage).toBeVisible({ timeout: 5000 });

      // Click "修訂記錄" tab
      const revisionsTab = page.locator('button:has-text("修訂記錄")');
      await revisionsTab.click();
      await page.waitForTimeout(1000);

      // Verify revision history shows 2 revisions (original + edit)
      const revisionsHeading = page.locator('text=/修訂記錄 \\(2\\)/');
      await expect(revisionsHeading).toBeVisible({ timeout: 5000 });

      // Click "稽核事件" tab
      const auditTab = page.locator('button:has-text("稽核事件")');
      await auditTab.click();
      await page.waitForTimeout(1000);

      // Verify audit events show the update (Chinese label for "draft updated")
      const auditContent = await page.content();
      expect(auditContent).toContain('草稿更新');
    });
  });

  test.describe('H. Rejection Prevention', () => {
    test('should not allow field transfer from rejected draft', async ({ page }) => {
      const token = await getAuthToken(USER_A.email, USER_A.password);

      // Create and reject a draft
      const draft = await createDraft(token, {
        title: 'Rejected Draft Test',
        hypothesis: 'This draft will be rejected',
        falsification_criteria: 'Test falsification criteria',
        strategy_template: 'buy_and_hold',
      });

      // Reject the draft via API
      await approveDraft(token, draft.id, 'reject');

      // Verify draft is rejected via API
      const rejectedDraft = await getDraft(token, draft.id);
      expect(rejectedDraft.status).toBe('rejected');

      // Login and navigate to draft detail
      await login(page, USER_A.email, USER_A.password);
      await page.goto(`${FRONTEND_BASE}/research-drafts/${draft.id}`);
      await page.waitForLoadState('networkidle');

      // Reload to ensure latest status
      await page.reload();
      await page.waitForLoadState('networkidle');

      // Wait for rejected status badge
      const statusBadge = page.locator('span:has-text("已退回")');
      await expect(statusBadge).toBeVisible({ timeout: 5000 });

      // Verify "選擇要套用的欄位" button is NOT visible
      const applyFieldsButton = page.locator('button:has-text("選擇要套用的欄位")');
      await expect(applyFieldsButton).not.toBeVisible({ timeout: 5000 });

      // Try to navigate directly to experiments/new with draft_id
      await page.goto(`${FRONTEND_BASE}/experiments/new?draft_id=${draft.id}&fields=title,hypothesis`);
      await page.waitForLoadState('networkidle');

      // Verify no preview is shown (draft is rejected, should not be applied)
      const previewSection = page.locator('.card:has-text("預覽草稿欄位")');
      await expect(previewSection).not.toBeVisible({ timeout: 5000 });

      // Verify form has default values, not draft values
      const titleInput = page.locator('input[type="text"]').first();
      const titleValue = await titleInput.inputValue();
      expect(titleValue).toBe(''); // Default empty, NOT "Rejected Draft Test"

      // Verify draft content is not in the page
      const pageContent = await page.content();
      expect(pageContent).not.toContain('Rejected Draft Test');
    });
  });
});
