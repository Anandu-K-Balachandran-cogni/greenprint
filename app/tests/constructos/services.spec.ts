import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun } from './helpers';

test('electrical diagram from the saved plan: generate, inspect, download, go stale after an edit, regenerate', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Services ${Date.now()}`, singleFloorBrief('Services'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 15 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await expect(page).toHaveURL(/\/editor\?id=cos-/);

  await page.getByRole('link', { name: /Services/ }).click();
  await expect(page).toHaveURL(/\/services\/cos-/);
  await expect(page.getByTestId('no-diagram')).toBeVisible();
  await page.getByRole('button', { name: 'Generate electrical diagram' }).click();

  const view = page.getByTestId('diagram-view');
  await expect(view.locator('svg')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('diagram-status')).toHaveText('Complete');
  await expect(page.getByTestId('currency')).toHaveText('Current — matches the saved plan');
  await expect(page.getByTestId('connected-wiring')).toHaveText(/^(\d+) of \1 supply points connected on allowed paths\.$/);
  await expect(page.getByTestId('improvement-wiring')).toContainText(/(better than the shortest path|The shortest path is already the best route)/);
  for (const kind of ['db', 'switchboard', 'light', 'socket', 'power_socket', 'geyser', 'exhaust']) {
    expect(await view.locator(`[data-kind="${kind}"]`).count(), kind).toBeGreaterThan(0);
  }
  expect(await view.locator('[data-circuit]').count()).toBeGreaterThan(1);
  expect(await view.locator('[data-control]').count()).toBeGreaterThan(0);
  await expect(view.locator('[data-legend]')).toContainText('Switch control (not a cable run)');
  await page.screenshot({ path: test.info().outputPath('electrical.png'), fullPage: true });

  const pending = page.waitForEvent('download');
  await page.getByRole('group', { name: 'Export diagram' }).getByRole('button', { name: 'SVG' }).click();
  const file = await (await pending).path();
  const svg = await readFile(file!, 'utf8');
  expect(svg).toMatch(/^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  expect(svg).not.toMatch(/<script/i);

  // Live Gemini: a wish adds typed points.
  await page.getByLabel('Electrical wishes').fill('Please add an AC point in the main bedroom');
  await page.getByRole('button', { name: 'Regenerate electrical diagram' }).click();
  await expect(view.locator('[data-kind="ac_point"]')).toHaveCount(2, { timeout: 90_000 }); // the point plus its legend symbol

  // An architectural edit makes the diagram out of date; regeneration routes the new plan.
  await page.getByRole('link', { name: '← Back to the editor' }).click();
  await page.getByRole('button', { name: 'Toggle Layers Panel', exact: true }).click();
  await page.getByRole('button', { name: /Wall 3$/ }).click();
  const thickness = page.getByRole('spinbutton', { name: 'Thickness (cm)', exact: true });
  await thickness.fill('20');
  await thickness.press('Tab');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Saved ✓', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: /Services/ }).click();
  await expect(page.getByTestId('currency')).toHaveText('Out of date — the plan changed since this diagram');
  await page.getByRole('button', { name: 'Regenerate electrical diagram' }).click();
  await expect(page.getByTestId('currency')).toHaveText('Current — matches the saved plan', { timeout: 60_000 });
  await expect(page.getByRole('region', { name: 'Diagram history' })).toContainText('older plan');
  expect(errors).toEqual([]);
});
