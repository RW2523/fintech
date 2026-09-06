import { defineConfig, devices } from "@playwright/test";

/** docs/09 §6 — the workbench smoke test runs against the real stack.
 *
 *  There is no mock API. The point of the test is that what an officer reads
 *  on the screen is what the decision service recorded, and a fixture cannot
 *  show that. `make up` and `scripts/seed_demo_case.py` must have run. */
const PORT = Number(process.env.WEB_TEST_PORT ?? 5273);

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      WEB_PORT: String(PORT),
      GATEWAY_URL: process.env.GATEWAY_URL ?? "http://localhost:8000",
    },
  },
});
