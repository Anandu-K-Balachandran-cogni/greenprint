import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun } from './helpers';

// Live (Gemini image model, server key): style a room, then a photo-real render of it from eye level
// and of the current 3D view; the images are stored with the project and served through the app.
test('photo-real renders of a styled room and the 3D view', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') console.log('BROWSER', m.text()); });
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Render ${Date.now()}`, singleFloorBrief('Render'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 15 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await page.getByRole('button', { name: '3D', exact: true }).click();
  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible();
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Top-Down View' }).click();
  await page.getByRole('button', { name: 'Edit Mode', exact: true }).click();
  const design = page.getByTestId('room-design');
  const applyRoom = design.getByRole('button', { name: /^Apply to (?!whole)/ });
  const box = (await canvas.boundingBox())!;
  for (const [fx, fy] of [[0.5, 0.5], [0.45, 0.4], [0.55, 0.6], [0.4, 0.55], [0.6, 0.45], [0.35, 0.35], [0.65, 0.65]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    if (await design.isVisible() && await applyRoom.isEnabled()) break;
  }
  await design.getByRole('radio', { name: 'Theme Japandi' }).click();
  await applyRoom.click();
  await page.waitForTimeout(2500);

  const panel = page.getByTestId('photo-render');
  for (const [button, file] of (process.env.ONLY_VIEW ? [['Render 3D view', 'render-view']] : [['Render this room', 'render-room'], ['Render 3D view', 'render-view']]) as [string, string][]) {
    await panel.getByRole('button', { name: button }).click();
    const img = panel.getByTestId('render-image');
    await expect(img).toBeVisible({ timeout: 150_000 });
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth), { timeout: 30_000 }).toBeGreaterThan(600);
    const src = await img.getAttribute('src');
    const res = await page.request.get(src!);
    expect(res.headers()['content-type']).toMatch(/^image\//);
    await writeFile(test.info().outputPath(`${file}.${res.headers()['content-type'].split('/')[1]}`), await res.body());
  }
  await page.screenshot({ path: test.info().outputPath('render-panel.png') });
  expect(errors).toEqual([]);
});
