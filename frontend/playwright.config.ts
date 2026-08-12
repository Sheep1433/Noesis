import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright 配置：双 Tab SSE 多标签页 E2E。
 * 验证 reliable-sse-multitab change 的多 Tab 行为：
 * - 中途加入、关闭创建 Tab、断网恢复、任意 Tab stop、任意 Tab HITL resume。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'list',
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:8090',
    trace: 'on-first-retry',
    storageState: process.env.E2E_STORAGE_STATE || undefined,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: process.env.E2E_BASE_URL || process.env.E2E_SKIP_SERVER
    ? undefined
    : {
        command: 'pnpm preview --port 8090 --strictPort',
        url: 'http://127.0.0.1:8090',
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
})
