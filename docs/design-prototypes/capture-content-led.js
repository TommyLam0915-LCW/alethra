const { chromium } = require('/Users/tommylam0915/Documents/Qoder/2026-10-24/alethra-unified-dev/frontend/node_modules/playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }
  });
  const page = await context.newPage();

  const filePath = path.join(__dirname, 'direction-a-content-led.html');
  await page.goto(`file://${filePath}`);
  await page.waitForTimeout(500);

  await page.screenshot({
    path: path.join(__dirname, 'direction-a-content-led-full.png'),
    fullPage: true
  });

  console.log('✓ direction-a-content-led-full.png');
  await browser.close();
})();
