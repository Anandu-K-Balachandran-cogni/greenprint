import { defineConfig, devices } from '@playwright/test';
import { AUTH_STATE } from './tests/constructos/login';

// The ConstructOS journeys against a deployed environment (no local servers):
//   CONSTRUCTOS_BASE_URL=https://… CONSTRUCTOS_LOGIN_ID=… CONSTRUCTOS_LOGIN_PASSWORD=… npx playwright test -c playwright.cloud.config.ts
// Journeys create uniquely named test projects in that environment's store.
const baseURL = process.env.CONSTRUCTOS_BASE_URL;
if (!baseURL) throw new Error('Set CONSTRUCTOS_BASE_URL to the deployed app URL');
if (!process.env.CONSTRUCTOS_LOGIN_PASSWORD) throw new Error('Set CONSTRUCTOS_LOGIN_ID and CONSTRUCTOS_LOGIN_PASSWORD');

export default defineConfig({
  testDir: './tests/constructos',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: { baseURL, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, storageState: AUTH_STATE, launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } },
    },
  ],
});
