const { chromium } = require('/Users/tommylam0915/Documents/Qoder/2026-10-24/alethra-unified-dev/frontend/node_modules/playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }
  });
  const page = await context.newPage();

  const prototypes = [
    { name: 'direction-a-trading-platform', file: 'direction-a-trading-platform.html' },
    { name: 'direction-b-research-first', file: 'direction-b-research-first.html' },
    { name: 'direction-c-minimalist', file: 'direction-c-minimalist.html' },
    { name: 'direction-a-final', file: 'direction-a-final.html' }
  ];

  for (const proto of prototypes) {
    const filePath = path.join(__dirname, proto.file);
    await page.goto(`file://${filePath}`);
    await page.waitForTimeout(500); // Let CSS render
    
    // Full page screenshot
    await page.screenshot({
      path: path.join(__dirname, `${proto.name}-full.png`),
      fullPage: true
    });
    
    console.log(`✓ ${proto.name}-full.png`);
  }

  await browser.close();
  console.log('\nAll screenshots captured.');
})();
