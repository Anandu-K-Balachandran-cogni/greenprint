import { expect, test as setup } from '@playwright/test';
import { AUTH_STATE, LOGIN } from './login';

// Sign in once through the real form; every journey then reuses the session.
setup('sign in with the shared login', async ({ page }) => {
  await page.goto('/build');
  await expect(page).toHaveURL(/\/login\?next=%2Fbuild$/);
  await page.getByLabel('ID').fill(LOGIN.id);
  await page.getByLabel('Password').fill(LOGIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/build$/);
  await page.context().storageState({ path: AUTH_STATE });
});
