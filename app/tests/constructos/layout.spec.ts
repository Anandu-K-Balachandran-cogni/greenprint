import { expect, test } from '@playwright/test';

// Deterministic: the confirmed brief is set through the API, so this journey
// exercises the real OR-Tools solver, checker and compiler without Gemini.
import { confirmedProject, room, singleFloorBrief, skipEditorTips, waitForSolved } from './helpers';

test('confirmed brief → fresh solved alternatives → checked → editable 2D/3D model that survives reload', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const name = `Single floor ${Date.now()}`;
  const id = await confirmedProject(request, name);

  await page.goto(`/build/${id}`);
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed');
  // A dropped connection while polling must not strand the page: the third poll is reset.
  let polls = 0;
  await page.route(/\/api\/engine\/projects\/[0-9a-f-]{36}\/layouts\/[0-9a-f-]{36}$/, route =>
    route.request().method() === 'GET' && ++polls === 3 ? route.abort('connectionreset') : route.continue());
  await page.getByRole('button', { name: 'Generate layouts' }).click();
  await expect(page).toHaveURL(/\/layouts\/[0-9a-f-]{36}$/, { timeout: 150_000 });
  await waitForSolved(page);
  expect(polls).toBeGreaterThan(3);
  await expect(page.locator('main[data-run-status]')).toHaveAttribute('data-run-status', 'complete');

  // Two genuinely solved options, each independently checked.
  for (const k of [0, 1]) {
    const option = page.getByTestId(`candidate-${k}`);
    await expect(option.getByTestId('solver-status')).toHaveAttribute('data-status', /^(OPTIMAL|FEASIBLE)$/);
    await expect(option.getByTestId('check-status')).toHaveText('Checks passed');
    for (const sid of ['master-bedroom-1', 'bedroom-1', 'kitchen-1', 'living-1', 'parking-1']) {
      await expect(option.locator(`[data-space-id="${sid}"]`)).toHaveCount(1);
    }
  }
  const rectOf = (k: number, sid: string) => page.getByTestId(`candidate-${k}`).locator(`[data-space-id="${sid}"] rect`)
    .evaluate(el => ['x', 'y', 'width', 'height'].map(a => el.getAttribute(a)).join(','));
  const ids = ['master-bedroom-1', 'bedroom-1', 'kitchen-1', 'living-1', 'dining-1', 'parking-1'];
  const differ = (await Promise.all(ids.map(async sid => (await rectOf(0, sid)) !== (await rectOf(1, sid))))).filter(Boolean);
  expect(differ.length).toBeGreaterThanOrEqual(2);
  await expect(page.getByText(/Setbacks assumed/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('layout-options.png'), fullPage: true });

  // Open option 1 in the reused editor: real walls, doors, windows and named rooms.
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await expect(page).toHaveURL(/\/editor\?id=cos-/);
  const app = page.getByRole('application');
  await expect(app).toContainText(/\d+ walls/);
  await expect(app).toContainText('10 rooms');
  await page.getByRole('button', { name: 'Toggle Layers Panel', exact: true }).click();
  for (const label of ['Main bedroom', 'Kids bedroom', 'Kitchen', 'Parking (1 car)']) {
    await expect(page.getByRole('button', { name: new RegExp(label.replace(/[()]/g, '\\$&')) }).first()).toBeVisible();
  }
  await page.waitForTimeout(1000);
  await page.screenshot({ path: test.info().outputPath('editor-2d.png') });

  await page.getByRole('button', { name: '3D', exact: true }).click();
  await expect(page.locator('canvas').first()).toBeVisible();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: test.info().outputPath('editor-3d.png') });
  await page.getByRole('button', { name: '2D', exact: true }).click();

  // A manual edit is saved, survives reload, and reopening the option never overwrites it.
  await page.getByTitle('Click to rename', { exact: true }).click();
  await page.getByRole('textbox', { name: 'Project name' }).fill('My edited option');
  await page.getByRole('textbox', { name: 'Project name' }).press('Enter');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Saved ✓', { exact: true })).toBeVisible();
  const editorUrl = page.url();
  await page.reload();
  await expect(page.getByRole('button', { name: 'My edited option', exact: true })).toBeVisible();
  await expect(app).toContainText('10 rooms');

  await page.goBack();
  await page.goto(editorUrl.replace(/\/editor.*/, '') + `/build/${id}`);
  await page.getByRole('link', { name: /OPTIMAL|FEASIBLE/ }).first().click();
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await expect(page.getByRole('button', { name: 'My edited option', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('layout generation refuses an unconfirmed or stale brief', async ({ request }) => {
  const created = await (await request.post('/api/engine/projects', { data: { name: 'No brief' } })).json();
  const none = await request.post(`/api/engine/projects/${created.id}/layouts`, { data: { expected_brief_version: 1 } });
  expect(none.status()).toBe(409);
  expect((await none.json()).detail.code).toBe('BRIEF_NOT_CONFIRMED');

  const id = await confirmedProject(request, `Stale ${Date.now()}`);
  const stale = await request.post(`/api/engine/projects/${id}/layouts`, { data: { expected_brief_version: 2 } });
  expect(stale.status()).toBe(409);
  expect((await stale.json()).detail.code).toBe('BRIEF_VERSION_CONFLICT');
});

test('an impossible brief reports INFEASIBLE or UNKNOWN and offers nothing to open', async ({ page, request }) => {
  const brief = singleFloorBrief('Tiny');
  brief.plot = { width: 6, depth: 6, unit: 'm', width_mm: null, depth_mm: null, status: 'stated' };
  brief.rooms = [0, 1, 2, 3].map(i => room(`bedroom-${i + 1}`, 'bedroom', `Bedroom ${i + 1}`));
  const id = await confirmedProject(request, `Tiny ${Date.now()}`, brief);
  await page.goto(`/build/${id}`);
  await page.getByRole('button', { name: 'Generate layouts' }).click();
  await expect(page).toHaveURL(/\/layouts\//, { timeout: 150_000 });
  await waitForSolved(page);
  const option = page.getByTestId('candidate-0');
  await expect(option.getByTestId('solver-status')).toHaveAttribute('data-status', /^(INFEASIBLE|UNKNOWN)$/);
  await expect(option.getByTestId('solver-message')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open in editor' })).toHaveCount(0);
  await expect(page.getByTestId('candidate-1')).toHaveCount(0);
});
