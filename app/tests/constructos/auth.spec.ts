import { expect, test } from '@playwright/test';
import { LOGIN } from './login';

test.use({ storageState: { cookies: [], origins: [] } });

test('everything requires the shared login; only the configured ID and password are accepted', async ({ page, request }) => {
  // Pages redirect to the login, keeping where you were going; APIs answer 401.
  await page.goto('/build?from=test');
  await expect(page).toHaveURL(/\/login\?next=%2Fbuild%3Ffrom%3Dtest$/);
  for (const path of ['/api/engine/projects', '/api/engine/health']) {
    expect((await request.get(path)).status(), path).toBe(401);
  }
  expect((await request.post('/api/engine/projects', { data: { name: 'x' } })).status()).toBe(401);

  // Wrong ID or password: refused with one generic message.
  for (const [id, password] of [[LOGIN.id, 'wrong password'], ['admin', LOGIN.password]]) {
    await page.getByLabel('ID').fill(id);
    await page.getByLabel('Password').fill(password);
    const answered = page.waitForResponse(r => r.url().includes('/login') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Sign in' }).click();
    expect((await answered).status()).toBe(200); // a form action failure is rendered on the page
    await expect(page.getByRole('alert')).toHaveText('The ID or password is not correct.');
    await expect(page).toHaveURL(/\/login/);
  }

  // Correct login lands where you were going, and the session cookie is HttpOnly.
  await page.getByLabel('ID').fill(LOGIN.id);
  await page.getByLabel('Password').fill(LOGIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/build\?from=test$/);
  const cookie = (await page.context().cookies()).find(c => c.name === 'cos_session');
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe('Lax');
  expect((await page.request.get('/api/engine/health')).status()).toBe(200);

  // Signing out ends the session.
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto('/build');
  await expect(page).toHaveURL(/\/login\?next=/);
});

test('a login link cannot send you to another site', async ({ page }) => {
  await page.goto('/login?next=//evil.example/steal');
  await page.getByLabel('ID').fill(LOGIN.id);
  await page.getByLabel('Password').fill(LOGIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:4189\/build$|^https:\/\/[^/]+\/build$/);
});
