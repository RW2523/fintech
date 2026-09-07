import { chromium } from "@playwright/test";
const dir = process.argv[2];
const out = process.argv[3];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
await page.goto(`file://${dir}/Credit_Intelligence_OS_Advanced_Standalone_Fixed.html`);
await page.waitForTimeout(1200);
const pages = ["dashboard","applications","workbench","documents","members","collections","sandbox","ledger"];
for (const key of pages) {
  await page.evaluate((k) => window.CI && window.CI.go(k), key);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${out}/proto-${key}.png`, fullPage: true });
  console.log("shot", key);
}
await browser.close();
