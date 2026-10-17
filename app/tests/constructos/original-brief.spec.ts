import { expect, test, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { skipEditorTips, waitForSolved } from './helpers';

// F01 / E2E-01 (without AI review yet): the user's original brief from an empty
// project, through live Gemini extraction and the real solver, to an editable
// two-storey building with a real stair, viewed in 2D and 3D.
const ORIGINAL =
  "I need a house on a 40 x 60 ft site with three bedrooms across two floors, parking for two cars, " +
  "an open kitchen and the parents' bedroom on the ground floor.";

async function exportJSON(page: Page) {
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download JSON', exact: true }).click();
  const json = JSON.parse(await readFile((await (await pending).path())!, 'utf8'));
  await page.keyboard.press('Escape');
  return json;
}

test('original 40 x 60 ft two-storey brief → editable building with stairs, both floors, 2D and 3D', async ({ page }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);

  await page.goto('/build');
  await page.getByLabel('Project name').fill(`Original brief ${Date.now()}`);
  await page.getByLabel('Building brief').fill(ORIGINAL);
  await page.getByRole('button', { name: 'Extract requirements' }).click();
  await expect(page).toHaveURL(/\/build\/[0-9a-f-]{36}$/, { timeout: 120_000 });
  await expect(page.getByLabel('Number of floors')).toHaveValue('2');
  await expect(page.getByLabel('Floor master-bedroom-1')).toHaveValue('0');
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed');

  await page.getByRole('button', { name: 'Generate layouts' }).click();
  await expect(page).toHaveURL(/\/layouts\//, { timeout: 170_000 });
  await waitForSolved(page);
  const option = page.getByTestId('candidate-0');
  await expect(option.getByTestId('solver-status')).toHaveAttribute('data-status', /^(OPTIMAL|FEASIBLE)$/);
  await expect(option.getByTestId('check-status')).toHaveText('Checks passed');
  // The parents' bedroom is on the ground floor; the other bedrooms are upstairs.
  await expect(option.locator('[data-space-id="master-bedroom-1"]')).toHaveCount(1);
  await expect(option.locator('[data-space-id="stair-0"]')).toHaveCount(1);
  await option.getByRole('button', { name: 'First' }).click();
  await expect(option.locator('[data-space-id="stair-1"]')).toHaveCount(1);
  await expect(option.locator('[data-space-id^="bedroom-"]')).toHaveCount(2);
  await page.screenshot({ path: test.info().outputPath('original-options-first-floor.png'), fullPage: true });

  await option.getByRole('button', { name: 'Open in editor' }).click();
  await expect(page).toHaveURL(/\/editor\?id=cos-/);
  const floor = page.getByRole('combobox', { name: 'Current floor' });
  await expect(floor.locator('option')).toHaveText(['Ground floor', 'First floor']);
  await page.waitForTimeout(800);
  await page.screenshot({ path: test.info().outputPath('original-ground-2d.png') });

  const exported = await exportJSON(page);
  const [ground, first] = exported.floors;
  expect(exported.floors).toHaveLength(2);
  expect(first.elevation).toBeGreaterThan(ground.elevation);
  expect(ground.stairs).toHaveLength(1);
  expect(ground.stairs[0].stairType).toBe('u-shaped');
  expect(first.rooms.find((r: { id: string }) => r.id === 'stair-1').floorOpening).toBe(true);
  expect(ground.rooms.map((r: { id: string }) => r.id)).toContain('master-bedroom-1');
  expect(first.rooms.filter((r: { id: string }) => /^bedroom-/.test(r.id))).toHaveLength(2);
  expect(ground.doors.some((d: { type: string }) => d.type === 'garage')).toBe(true);
  // The source link survives the editor's save/load/export, for later diagram currency checks.
  expect(exported.constructos).toMatchObject({ briefVersion: 1, candidate: 0, compiler: 'constructos.compiler.v1' });

  await floor.selectOption({ label: 'First floor' });
  await page.waitForTimeout(800);
  await page.screenshot({ path: test.info().outputPath('original-first-2d.png') });

  await page.getByRole('button', { name: '3D', exact: true }).click();
  await expect(page.locator('canvas').first()).toBeVisible();
  await page.getByRole('button', { name: 'Show All Floors Stacked' }).click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: test.info().outputPath('original-3d.png') });
  expect(errors).toEqual([]);
});
