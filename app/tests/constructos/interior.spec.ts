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

// Phase A: click a room in 3D and style it — paint its own stretch of each wall (with an accent
// wall), the ceiling, curtains on its windows — and place, select, move and rotate furniture in 3D.
test('style a room from the 3D view', async ({ page, request }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Interior ${Date.now()}`, singleFloorBrief('Interior'));
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

  // 1. Click a room's floor in 3D: the Room design panel opens for that room. Opening the side
  //    panel narrows the 3D view, so re-frame top-down and pick the room again on the final layout.
  const design = page.getByTestId('room-design');
  const POINTS = [[0.5, 0.5], [0.45, 0.4], [0.55, 0.6], [0.4, 0.55], [0.6, 0.45], [0.35, 0.35], [0.65, 0.65]];
  let at = { x: 0, y: 0 };
  let box = (await canvas.boundingBox())!;
  // Prefer a room with windows (so curtains are exercised); otherwise the first room hit.
  const pickRoom = async () => {
    let first: typeof at | null = null;
    for (const [fx, fy] of POINTS) {
      const p = { x: box.x + box.width * fx, y: box.y + box.height * fy };
      await page.mouse.click(p.x, p.y);
      if (!(await design.isVisible())) continue;
      first ??= p;
      if (await design.getByRole('button', { name: /^Add curtains/ }).isVisible()) { at = p; return; }
    }
    if (first) { at = first; await page.mouse.click(at.x, at.y); }
  };
  await pickRoom();
  await expect(design).toBeVisible();
  await page.getByRole('button', { name: 'Top-Down View' }).click();
  await page.waitForTimeout(800);
  box = (await canvas.boundingBox())!;
  await pickRoom();
  await expect(design).toBeVisible();
  const roomName = await page.locator('[data-plan-properties] input[type="text"]').first().inputValue();

  // 2. Paint: walls, an accent wall, the ceiling.
  await design.getByRole('button', { name: 'Paint walls Sage Green' }).click();
  await design.getByLabel('Accent wall').selectOption({ index: 1 });
  await design.getByRole('button', { name: 'Paint accent wall Terracotta' }).click();
  await design.getByRole('button', { name: 'Ceiling #eef2f3' }).click();

  // 3. Curtains on every window of this room (if it has any), then none are missing.
  const addCurtains = design.getByRole('button', { name: /^Add curtains/ });
  const hadWindows = await addCurtains.isVisible();
  if (hadWindows) {
    await addCurtains.click();
    await expect(design.getByTestId('curtain-status')).toHaveText('Every window in this room has curtains.');
  }

  // 4. Place an armchair by clicking the room floor, then select it in 3D, drag it, rotate with R.
  await page.getByRole('button', { name: 'Place Furniture' }).click();
  await page.getByRole('button', { name: 'Living Room', exact: true }).click();
  await page.getByRole('button', { name: /Armchair/ }).click();
  await page.mouse.click(at.x, at.y);
  await expect(design.getByTestId('room-furniture')).toContainText('Armchair');
  await page.getByRole('button', { name: 'Exit Furniture Placement' }).click();
  await page.mouse.click(at.x, at.y);                               // select the chair
  await expect(page.locator('[data-plan-properties]')).toContainText('Armchair');
  const placed = (await exportJSON(page)).floors[0].furniture.find((f: { catalogId: string }) => f.catalogId === 'chair').position;
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  await page.mouse.move(at.x + 8, at.y + 4, { steps: 6 });
  await page.mouse.up();
  await page.keyboard.press('r');
  await page.waitForTimeout(400);

  // 5. The saved model holds exactly what was done.
  const project = await exportJSON(page);
  const floor = project.floors[0];
  const room = floor.rooms.find((r: { name: string }) => r.name === roomName);
  expect(room.ceilingColor).toBe('#eef2f3');
  const spans = floor.walls.flatMap((w: { paints?: { roomId: string; color: string }[] }) => w.paints ?? []).filter((p: { roomId: string }) => p.roomId === room.id);
  expect(spans.length).toBeGreaterThanOrEqual(3);
  expect(spans.filter((p: { color: string }) => p.color === '#d2691e')).toHaveLength(1);    // one accent wall
  expect(spans.filter((p: { color: string }) => p.color === '#d4e2d4').length).toBeGreaterThanOrEqual(2);
  // Paint stays on this room's stretch: no span is longer than the room's longest side.
  const longest = Math.max(...floor.walls.map((w: { start: { x: number; y: number }; end: { x: number; y: number } }) => Math.hypot(w.end.x - w.start.x, w.end.y - w.start.y)));
  for (const p of spans) expect(p.to - p.from).toBeLessThan(longest);
  if (hadWindows) {
    const curtains = floor.furniture.filter((f: { catalogId: string }) => f.catalogId === 'curtain');
    expect(curtains.length).toBeGreaterThan(0);
    for (const c of curtains) expect(c.height).toBeGreaterThan(150);
  }
  const chair = floor.furniture.find((f: { catalogId: string }) => f.catalogId === 'chair');
  expect(chair.rotation).toBe(15);
  expect(Math.hypot(chair.position.x - placed.x, chair.position.y - placed.y)).toBeGreaterThan(5);   // dragged

  // 6. Undo the rotation; reload — paint, curtains and the chair survive.
  await page.keyboard.press('ControlOrMeta+z');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Saved ✓', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '3D', exact: true }).click();
  await expect(canvas).toBeVisible();
  const reloaded = (await exportJSON(page)).floors[0];
  expect(reloaded.furniture.find((f: { catalogId: string }) => f.catalogId === 'chair').rotation).toBe(0);
  expect(reloaded.walls.flatMap((w: { paints?: unknown[] }) => w.paints ?? []).length).toBe(floor.walls.flatMap((w: { paints?: unknown[] }) => w.paints ?? []).length);

  // A look from an angle for the record.
  await page.waitForTimeout(1200);
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.45, { steps: 10 });
  await page.mouse.up();
  await page.waitForTimeout(800);
  await page.screenshot({ path: test.info().outputPath('styled-room.png') });
  expect(errors).toEqual([]);
});
