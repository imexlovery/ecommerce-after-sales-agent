import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: Number(process.env.SURFACE_E2E_TIMEOUT_MS ?? "180000"),
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["line"]],
  use: {
    baseURL: process.env.SURFACE_BASE_URL ?? "http://127.0.0.1:5173",
    browserName: "chromium",
    channel: process.env.SURFACE_BROWSER_CHANNEL ?? undefined,
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
