import { expect, test, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun } from './helpers';

async function exportJSON(page: Page) {
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download JSON', exact: true }).click();
  return JSON.parse(await readFile((await (await pending).path())!, 'utf8'));
}

// Phase C: ask the interior designer agent (live Neuro-SAN + Gemini) for a room; its design is
// previewed in 3D, kept or discarded, and its budget shows on the Cost tab.
test('the interior designer proposes, previews and prices a design', async ({ page, request }) => {
  test.setTimeout(420_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Designer ${Date.now()}`, singleFloorBrief('Designer'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 15 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await expect(page).toHaveURL(/\/editor\?id=cos-/);
  await page.getByRole('button', { name: '3D', exact: true }).click();
  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible();
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Top-Down View' }).click();
  await page.getByRole('button', { name: 'Edit Mode', exact: true }).click();
  await page.waitForTimeout(800);

  // Select a room the designer can style (living space, bedroom, kitchen…).
  const design = page.getByTestId('room-design');
  const applyRoom = design.getByRole('button', { name: /^Apply to (?!whole)/ });
  const box = (await canvas.boundingBox())!;
  for (const [fx, fy] of [[0.5, 0.5], [0.45, 0.4], [0.55, 0.6], [0.4, 0.55], [0.6, 0.45], [0.35, 0.35], [0.65, 0.65], [0.3, 0.6]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    if (await design.isVisible() && await applyRoom.isEnabled()) break;
  }
  await expect(applyRoom).toBeEnabled();
  const before = (await exportJSON(page)).floors[0].furniture.length;

  const designer = page.getByTestId('interior-designer');
  await designer.getByLabel('Describe the look you want').fill('Warm Indian style with lots of wood and plants, please.');
  await designer.getByRole('button', { name: 'Design it' }).click();
  const reply = designer.getByTestId('designer-reply');
  await expect(designer.getByTestId('design-budget')).toContainText(/Interiors budget: ₹[\d.]+ lakh/, { timeout: 150_000 });
  await expect(reply).not.toContainText(/#[0-9a-f]{6}/i);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: test.info().outputPath('designer-preview.png') });
  const previewed = (await exportJSON(page)).floors[0];
  expect(previewed.furniture.length).toBeGreaterThan(before);
  expect(previewed.furniture.some((f: { palette?: unknown }) => f.palette)).toBe(true);
  await designer.getByRole('button', { name: 'Keep design' }).click();
  await expect(designer.getByTestId('design-kept')).toBeVisible();

  // A second idea, discarded: the floor goes back to the kept design.
  await designer.getByLabel('Describe the look you want').fill('Try a coastal look instead, with blue accents.');
  await designer.getByRole('button', { name: 'Design it' }).click();
  await expect(designer.getByRole('button', { name: 'Discard' })).toBeEnabled({ timeout: 150_000 });
  await designer.getByRole('button', { name: 'Discard' }).click();
  const after = (await exportJSON(page)).floors[0];
  expect(after.furniture.length).toBe(previewed.furniture.length);
  expect(JSON.stringify(after.walls)).toBe(JSON.stringify(previewed.walls));

  // The Cost tab shows the latest design's interiors budget, apart from construction.
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Saved ✓', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: /Services/ }).click();
  await page.getByRole('tab', { name: 'Cost' }).click();
  await expect(page.getByTestId('interiors-budget')).toContainText('separate from construction');
  await expect(page.getByTestId('interiors-total')).toContainText('lakh');
  expect(errors).toEqual([]);
});
