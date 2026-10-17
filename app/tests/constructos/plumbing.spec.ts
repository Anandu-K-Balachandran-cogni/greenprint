import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { confirmedProject, skipEditorTips, twoStoreyBrief, solvedRun } from './helpers';

test('plumbing from the saved two-storey plan: fixtures, entry, manhole, stack and directed drainage', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Plumbing ${Date.now()}`, twoStoreyBrief('Plumbing'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 25 });
  expect(run.candidates[0].report.status).toBe('PASS');
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await page.getByRole('link', { name: /Services/ }).click();

  await page.getByRole('tab', { name: 'Plumbing 2D' }).click();
  await expect(page.getByTestId('no-diagram')).toHaveText('No plumbing diagram has been generated for this plan yet.');
  await page.getByRole('button', { name: 'Generate plumbing diagram' }).click();
  const view = page.getByTestId('diagram-view');
  await expect(view.locator('svg')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('diagram-status')).toHaveText('Complete');
  await expect(page.getByTestId('connected-supply')).toHaveText(/^(\d+) of \1 fixtures connected on allowed paths\.$/);
  await expect(page.getByTestId('connected-drainage')).toHaveText(/^(\d+) of \1 fixtures connected on allowed paths\.$/);
  await expect(page.getByTestId('gravity')).toContainText('pipe slopes are set on site');

  // Ground floor: entry, manhole, kitchen sink, WC, directed drainage with arrows, the stack's foot.
  for (const kind of ['water_entry', 'drain_exit', 'sink', 'wc', 'basin', 'stack']) {
    expect(await view.locator(`svg > g[data-kind="${kind}"]`).count(), kind).toBeGreaterThan(0);
  }
  expect(await view.locator('[data-circuit="CW0"]').count()).toBe(1);
  expect(await view.locator('[data-circuit="DR0"]').count()).toBe(1);
  expect(await view.locator('[data-flow]').count()).toBeGreaterThan(0);
  await page.screenshot({ path: test.info().outputPath('plumbing-ground.png'), fullPage: true });

  // First floor: the same stack, upstairs bathroom fixtures, terrace floor drains.
  await page.getByRole('group', { name: 'Floor' }).getByRole('button', { name: 'First floor' }).click();
  await expect(view.locator('svg')).toHaveAttribute('data-floor', '1');
  await expect(view.locator('svg > g[data-kind="stack"]')).toContainText('S1');
  for (const kind of ['wc', 'shower', 'floor_drain']) {
    expect(await view.locator(`svg > g[data-kind="${kind}"]`).count(), kind).toBeGreaterThan(0);
  }
  await page.screenshot({ path: test.info().outputPath('plumbing-first.png'), fullPage: true });

  const pending = page.waitForEvent('download');
  await page.getByRole('group', { name: 'Export diagram' }).getByRole('button', { name: 'SVG' }).click();
  const file = await readFile((await (await pending).path())!, 'utf8');
  expect(file).toContain('Plumbing layout — First floor');
  expect(file).toContain('gravity falls not verified');

  // Server-rendered exports: scaled PDF sheets and a layered DXF in millimetres.
  const exportsGroup = page.getByRole('group', { name: 'Export diagram' });
  const pdfDownload = page.waitForEvent('download');
  await exportsGroup.getByRole('button', { name: 'PDF' }).click();
  const pdf = await pdfDownload;
  expect(pdf.suggestedFilename()).toMatch(/^constructos-plumbing-[0-9a-f]{8}\.pdf$/);
  const pdfBytes = await readFile((await pdf.path())!);
  expect(pdfBytes.subarray(0, 5).toString()).toBe('%PDF-');
  const dxfDownload = page.waitForEvent('download');
  await exportsGroup.getByRole('button', { name: 'DXF' }).click();
  const dxf = await readFile((await (await dxfDownload).path())!, 'utf8');
  for (const layer of ['A-WALL', 'P-SUPPLY', 'P-DRAINAGE', 'P-SYMBOL']) expect(dxf).toContain(layer);
  expect(dxf).toMatch(/\$INSUNITS\s+70\s+4\b/);
  const planDownload = page.waitForEvent('download');
  await page.getByRole('group', { name: 'Export floor plan' }).getByRole('button', { name: 'PDF' }).click();
  expect((await planDownload).suggestedFilename()).toMatch(/^constructos-plan-[0-9a-f]{8}\.pdf$/);

  // A chosen drain exit is honoured on regeneration.
  await page.getByLabel('Drain exit room').selectOption({ label: 'Foyer' });
  await page.getByRole('button', { name: 'Regenerate plumbing diagram' }).click();
  await page.locator('summary', { hasText: 'Assumptions' }).click();
  await expect(page.getByTestId('assumptions')).toContainText(/manhole at the road-side wall of Foyer\.|Drainage leaves through a manhole at the road-side wall of .*\(assumed\)/);
  await expect(page.getByRole('region', { name: 'Diagram history' })).toBeVisible();

  // The electrical tab has its own, independent diagram state.
  await page.getByRole('tab', { name: 'Electrical 2D' }).click();
  await expect(page.getByTestId('no-diagram')).toHaveText('No electrical diagram has been generated for this plan yet.');
  expect(errors).toEqual([]);
});
