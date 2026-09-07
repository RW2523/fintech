import { chromium } from "@playwright/test";
const out = process.argv[2];
const base = "http://localhost:8080";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto(`${base}/login`);
await page.getByTestId("role-officer").click();
await page.waitForURL(/\/officer$/);
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/mine-queue.png`, fullPage: true });
// open the first case
const row = page.locator('[data-testid^="queue-row-"]').first();
await row.locator("a").first().click();
await page.waitForTimeout(2500);
await page.screenshot({ path: `${out}/mine-workbench.png`, fullPage: true });
await browser.close();
console.log("done");
