import { chromium } from "@playwright/test";
const out = process.argv[2];
const base = "http://localhost:8080";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.goto(`${base}/login`);
await page.getByTestId("role-officer").click();
await page.waitForURL(/\/officer$/);
await page.waitForTimeout(2000);
await page.screenshot({ path: `${out}/mine-queue.png`, fullPage: true });
// S2INCOME has the deepest council
await page.getByTestId("queue-row-case_S2INCOME").locator("a").first().click();
await page.waitForTimeout(3500);
await page.getByTestId("toggle-discussion").click();
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/mine-workbench.png`, fullPage: true });
await browser.close();
console.log("done");
