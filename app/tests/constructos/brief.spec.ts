import { expect, test } from '@playwright/test';

// Live: uses the real engine and Gemini model configured in the repository .env.
const ORIGINAL =
  "I need a house on a 40 x 60 ft site with three bedrooms across two floors, parking for two cars, " +
  "an open kitchen and the parents' bedroom on the ground floor.";

test('original brief → Gemini extraction → user correction → confirmed, versioned, survives reload', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const projectName = `Original brief house ${Date.now()}`;

  await page.goto('/');
  // First visit shows the welcome dialog; brief generation is its primary option.
  await page.getByRole('link', { name: /Generate from a brief/ }).click();
  await expect(page).toHaveURL(/\/build$/);

  await page.getByLabel('Project name').fill(projectName);
  await page.getByLabel('Building brief').fill(ORIGINAL);
  await page.getByRole('button', { name: 'Extract requirements' }).click();
  await expect(page).toHaveURL(/\/build\/[0-9a-f-]{36}$/, { timeout: 120_000 });

  // F01 oracle: exact plot conversion and the stated programme, from a blank project.
  await expect(page.getByTestId('brief-status')).toHaveText('Draft — not confirmed');
  await expect(page.getByTestId('plot-mm')).toHaveText('12,192 × 18,288 mm');
  await expect(page.getByLabel('Number of floors')).toHaveValue('2');
  await expect(page.getByLabel('Parking spaces')).toHaveValue('2');
  await expect(page.getByRole('checkbox', { name: 'Open kitchen', exact: true })).toBeChecked();
  const summary = page.getByTestId('room-summary');
  await expect(summary).toContainText('1 × master bedroom');
  await expect(summary).toContainText('2 × bedroom');
  await expect(page.getByLabel('Floor master-bedroom-1')).toHaveValue('0');
  // Orientation was never stated, so it must not appear as a fact.
  await expect(page.getByLabel('Entrance faces').locator('option:checked')).toHaveText('Not specified');

  // The user corrects the brief: adds a bathroom and states the entrance.
  await page.getByLabel('New room type').selectOption('bathroom');
  await page.getByRole('button', { name: 'Add room' }).click();
  const bathroomName = page.locator('[data-room-id="bathroom-1"] input[aria-label^="Room name"]');
  await expect(bathroomName).toHaveValue('Bathroom');
  await page.getByLabel('Floor bathroom-1').selectOption({ label: 'Ground floor' });
  await page.getByLabel('Entrance faces').selectOption('north');
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed');
  await page.screenshot({ path: test.info().outputPath('requirements-confirmed.png'), fullPage: true });

  // Reload: the confirmed version and the user's corrections persist.
  await page.reload();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed');
  await expect(page.getByLabel('Floor bathroom-1')).toHaveValue('0');
  await expect(page.getByLabel('Entrance faces')).toHaveValue('north');

  // Removing an essential input blocks confirmation and keeps v1 as the confirmed version.
  await page.getByLabel('Plot width').fill('');
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByRole('alert')).toHaveText('Some required information is missing.');
  await expect(page.getByText('Plot width, depth and unit are required.')).toBeVisible();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed · unsaved changes');

  // Restoring it and confirming creates v2.
  await page.getByLabel('Plot width').fill('40');
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v2 confirmed');

  // The new building is listed with its confirmed version.
  await page.goto('/build');
  await expect(page.getByRole('link', { name: new RegExp(projectName) })).toContainText('Brief v2 confirmed');
  expect(errors).toEqual([]);
});

test('engine gateway forwards only known routes', async ({ request }) => {
  expect((await request.get('/api/engine/health')).status()).toBe(200);
  expect((await request.get('/api/engine/docs')).status()).toBe(404);
  expect((await request.get('/api/engine/projects/not-a-uuid')).status()).toBe(404);
  expect((await request.get('/api/engine/projects/00000000-0000-0000-0000-000000000000')).status()).toBe(404);
});
