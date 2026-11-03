const { chromium } = require('/Users/tommylam0915/Documents/Qoder/2026-10-24/alethra-unified-dev/frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }
  });
  const page = await context.newPage();

  // Login page
  await page.goto('http://localhost:3000/login');
  await page.waitForTimeout(1000);
  await page.screenshot({
    path: '/Users/tommylam0915/Desktop/implementation-login-desktop.png',
    fullPage: true
  });
  console.log('✓ Login page (desktop)');

  // Dashboard page (will redirect to login if not authenticated)
  await page.goto('http://localhost:3000/dashboard', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  await page.screenshot({
    path: '/Users/tommylam0915/Desktop/implementation-dashboard-desktop.png',
    fullPage: true
  });
  console.log('✓ Dashboard page (desktop)');

  // Mobile login
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('http://localhost:3000/login');
  await page.waitForTimeout(1000);
  await page.screenshot({
    path: '/Users/tommylam0915/Desktop/implementation-login-mobile.png',
    fullPage: true
  });
  console.log('✓ Login page (mobile)');

  // Workspace mobile (will redirect to login if not authenticated)
  await page.goto('http://localhost:3000/workspace', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  await page.screenshot({
    path: '/Users/tommylam0915/Desktop/implementation-workspace-mobile.png',
    fullPage: true
  });
  console.log('✓ Workspace page (mobile)');

  await browser.close();
  console.log('\nAll screenshots captured.');
})();
