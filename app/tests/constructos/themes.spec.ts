import { expect, test, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun } from './helpers';

async function exportJSON(page: Page) {
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download JSON', exact: true }).click();
  // The menu closes itself on download; an Escape here would leave 3D edit mode.
  return JSON.parse(await readFile((await (await pending).path())!, 'utf8'));
}

const THEMES = ['Indian Contemporary', 'Warm Traditional', 'Scandinavian', 'Modern Minimal', 'Industrial', 'Coastal', 'Japandi', 'Boho'];

// Phase B: pick a theme for the whole floor in 3D — empty rooms are furnished, every room is
// painted, floored, dressed and lit — then switch themes (restyle only) and undo.
test('apply interior themes to a whole floor in 3D', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Themes ${Date.now()}`, singleFloorBrief('Themes'));
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

  const design = page.getByTestId('room-design');
  const POINTS = [[0.5, 0.5], [0.45, 0.4], [0.55, 0.6], [0.4, 0.55], [0.6, 0.45], [0.35, 0.35], [0.65, 0.65]];
  const pickRoom = async () => {
    const box = (await canvas.boundingBox())!;
    for (const [fx, fy] of POINTS) {
      await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
      if (await design.isVisible()) return;
    }
  };
  await pickRoom();
  await expect(design).toBeVisible();
  await page.getByRole('button', { name: 'Top-Down View' }).click();

  const shots = test.info().outputPath('.');
  for (const [i, name] of THEMES.entries()) {
    await design.getByRole('radio', { name: `Theme ${name}` }).click();
    await design.getByRole('button', { name: 'Apply to whole floor' }).click();
    const status = design.getByTestId('theme-status');
    await expect(status).toContainText(`${name} applied to`);
    if (i === 0) await expect(status).toContainText(/\d+ items added \(furnished \d+\)/);
    else await expect(status).toContainText('0 items added');   // later themes restyle what is there
    await page.waitForTimeout(2500);                              // let the models load
    await page.screenshot({ path: `${shots}/theme-${i + 1}-${name.toLowerCase().replace(/ /g, '-')}.png` });
  }

  // The model: every themed room has paint, the last theme's ceiling, curtains and lights; furniture carries the palette.
  const floor = (await exportJSON(page)).floors[0];
  const boho = { ceiling: '#ffffff', wood: '#a47148', curtain: '#e9c46a' };
  const themedRooms = floor.rooms.filter((r: { ceilingColor?: string }) => r.ceilingColor === boho.ceiling);
  expect(themedRooms.length).toBeGreaterThanOrEqual(6);
  const ids = new Set(floor.walls.flatMap((w: { paints?: { roomId: string }[] }) => (w.paints ?? []).map(p => p.roomId)));
  for (const r of themedRooms) expect(ids.has(r.id), r.name).toBe(true);
  const cat = (re: RegExp) => floor.furniture.filter((f: { catalogId: string }) => re.test(f.catalogId));
  expect(cat(/^sofa/).length).toBeGreaterThanOrEqual(1);
  expect(cat(/^bed_/).length).toBeGreaterThanOrEqual(2);
  expect(cat(/^stove$/).length).toBe(1);
  expect(cat(/^toilet$/).length).toBeGreaterThanOrEqual(1);
  expect(cat(/curtain/).every((c: { color: string }) => c.color === boho.curtain)).toBe(true);
  expect(cat(/^bed_/).every((b: { palette?: { wood: string } }) => b.palette?.wood === boho.wood)).toBe(true);
  expect(cat(/ceiling_fan|pendant_light|ceiling_light|recessed_light|chandelier/).length).toBeGreaterThanOrEqual(6);

  // One undo removes the last theme only (Japandi's wood comes back).
  await page.keyboard.press('ControlOrMeta+z');
  const undone = (await exportJSON(page)).floors[0];
  expect(undone.furniture.filter((f: { catalogId: string }) => /^bed_/.test(f.catalogId))
    .every((b: { palette?: { wood: string } }) => b.palette?.wood === '#b08968')).toBe(true);

  // A perspective look at the furnished floor for the visual check (re-entering 3D frames it at an angle).
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '2D', exact: true }).click();
  await page.getByRole('button', { name: '3D', exact: true }).click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${shots}/theme-perspective.png` });
  expect(errors).toEqual([]);
});
