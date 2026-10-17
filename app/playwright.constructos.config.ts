import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { hashPassword } from './src/lib/server/auth';
import { AUTH_STATE, LOGIN } from './tests/constructos/login';

// ConstructOS end-to-end suite: the production app build plus the real Python
// engine (and live Gemini, via the repository .env) on isolated test ports/data.
const dataDir = mkdtempSync(join(tmpdir(), 'constructos-e2e-'));
const ENGINE_PORT = 8797;

export default defineConfig({
  testDir: './tests/constructos',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-constructos' }]],
  use: {
    baseURL: 'http://127.0.0.1:4189',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, storageState: AUTH_STATE, launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } },
    },
  ],
  webServer: [
    {
      command: `uv run --directory ../engine uvicorn constructos_engine.main:app --host 127.0.0.1 --port ${ENGINE_PORT}`,
      url: `http://127.0.0.1:${ENGINE_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { CONSTRUCTOS_DATA_DIR: dataDir, MAPS_SOLAR_KEY: process.env.MAPS_SOLAR_KEY ?? '' },
    },
    {
      command: 'node build/index.js',
      url: 'http://127.0.0.1:4189',
      reuseExistingServer: false,
      timeout: 30_000,
      env: {
        NODE_ENV: 'production', HOST: '127.0.0.1', PORT: '4189', ORIGIN: 'http://127.0.0.1:4189',
        ENGINE_URL: `http://127.0.0.1:${ENGINE_PORT}`, HANDOFF_UPLOADS_ENABLED: 'false', HANDOFF_BUCKET: '', BODY_SIZE_LIMIT: '12M', MAPS_BROWSER_KEY: process.env.MAPS_BROWSER_KEY ?? '',
        // The journeys run behind the same shared login as a deployment.
        APP_LOGIN_ID: LOGIN.id, APP_LOGIN_PASSWORD_HASH: hashPassword(LOGIN.password), APP_SESSION_SECRET: 'local-e2e-session-secret-0123456789abcdef',
      },
    },
  ],
});
