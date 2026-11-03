const { chromium } = require('/Users/tommylam0915/Documents/Qoder/2026-10-24/alethra-unified-dev/frontend/node_modules/playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch();

  // Desktop version - 1440x900
  const desktopContext = await browser.newContext({
    viewport: { width: 1440, height: 900 }
  });
  const desktopPage = await desktopContext.newPage();
  const desktopFile = path.join(__dirname, 'direction-a-content-led.html');
  await desktopPage.goto(`file://${desktopFile}`);
  await desktopPage.waitForTimeout(500);
  await desktopPage.screenshot({
    path: path.join(__dirname, 'direction-a-content-led-desktop.png'),
    fullPage: true
  });
  console.log('✓ direction-a-content-led-desktop.png (1440x900, full page)');
  await desktopContext.close();

  // Mobile login - 375x812
  const mobileLoginContext = await browser.newContext({
    viewport: { width: 375, height: 812 }
  });
  const mobileLoginPage = await mobileLoginContext.newPage();
  const mobileFile = path.join(__dirname, 'direction-a-mobile.html');
  await mobileLoginPage.goto(`file://${mobileFile}`);
  await mobileLoginPage.waitForTimeout(500);

  // Screenshot just the mobile-login section
  const loginSection = await mobileLoginPage.$('.mobile-login');
  await loginSection.screenshot({
    path: path.join(__dirname, 'direction-a-mobile-login.png')
  });
  console.log('✓ direction-a-mobile-login.png (375x812)');
  await mobileLoginContext.close();

  // Mobile research - 375x812
  const mobileResearchContext = await browser.newContext({
    viewport: { width: 375, height: 812 }
  });
  const mobileResearchPage = await mobileResearchContext.newPage();
  await mobileResearchPage.goto(`file://${mobileFile}`);
  await mobileResearchPage.waitForTimeout(500);

  const researchSection = await mobileResearchPage.$('.mobile-research');
  await researchSection.screenshot({
    path: path.join(__dirname, 'direction-a-mobile-research.png')
  });
  console.log('✓ direction-a-mobile-research.png (375x812)');
  await mobileResearchContext.close();

  // Mobile workspace - 375x812
  const mobileWorkspaceContext = await browser.newContext({
    viewport: { width: 375, height: 812 }
  });
  const mobileWorkspacePage = await mobileWorkspaceContext.newPage();
  await mobileWorkspacePage.goto(`file://${mobileFile}`);
  await mobileWorkspacePage.waitForTimeout(500);

  const workspaceSection = await mobileWorkspacePage.$('.mobile-workspace');
  await workspaceSection.screenshot({
    path: path.join(__dirname, 'direction-a-mobile-workspace.png')
  });
  console.log('✓ direction-a-mobile-workspace.png (375x812)');
  await mobileWorkspaceContext.close();

  await browser.close();
  console.log('\nAll screenshots captured.');
})();
