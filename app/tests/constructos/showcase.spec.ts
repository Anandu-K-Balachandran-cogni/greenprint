import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { skipEditorTips, solvedRun } from './helpers';

// Not part of the regression: one family home end to end (live Gemini, Maps, Solar API), capturing the
// screenshots used in the README, the submission deck and the demo video.
//   SHOWCASE=1 MAPS_BROWSER_KEY=… MAPS_SOLAR_KEY=… npx playwright test -c playwright.constructos.config.ts showcase
const OUT = fileURLToPath(new URL('../../../docs/screenshots/', import.meta.url));
const PHOTO = fileURLToPath(new URL('../../../engine/tests/fixtures/sketches/house_sketch_photo.jpg', import.meta.url));
test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

async function shot(page: Page, name: string, full = false) {
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: full });
}

test('showcase: the Iyer family home', async ({ page, request }) => {
  test.skip(!process.env.SHOWCASE, 'set SHOWCASE=1 to capture the showcase');
  test.setTimeout(1_500_000);
  mkdirSync(OUT, { recursive: true });
  await skipEditorTips(page);

  // 1. Sketch photo + a note in Hindi → brief → requirements.
  await page.goto('/build');
  await page.getByLabel('Project name').fill('The Iyer family home');
  await page.getByLabel('Building brief').fill('मुझे एक पूजा कमरा भी चाहिए, और घर में अच्छी धूप और हवा आनी चाहिए।');
  await page.getByTestId('sketch-intake').getByLabel('Add sketch or plot photo').setInputFiles([PHOTO]);
  await page.getByRole('button', { name: 'Read my sketch' }).click();
  await expect(page.getByTestId('sketch-reading')).toBeVisible({ timeout: 120_000 });
  await shot(page, '01-sketch-intake');
  await page.getByRole('button', { name: 'Extract requirements' }).click();
  await expect(page).toHaveURL(/\/build\/[0-9a-f-]{36}$/, { timeout: 120_000 });
  const id = page.url().split('/').pop()!;
  await page.getByLabel('Entrance faces').selectOption('east');
  await shot(page, '02-requirements', true);
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed');

  // 2. Layout options from the solver.
  const run = await solvedRun(request, id, { alternatives: 3, time_limit_s: 25 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.waitForTimeout(1500);
  await shot(page, '03-layout-options', true);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await expect(page).toHaveURL(/\/editor\?id=cos-/);
  await page.waitForTimeout(2000);
  await shot(page, '04-editor-2d');

  // 3. 3D, a theme for the whole floor, the interior designer, a photo-real render.
  await page.getByRole('button', { name: '3D', exact: true }).click();
  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible();
  await page.waitForTimeout(3000);
  await shot(page, '05-3d');
  await page.getByRole('button', { name: 'Top-Down View' }).click();
  await page.getByRole('button', { name: 'Edit Mode', exact: true }).click();
  const design = page.getByTestId('room-design');
  const applyRoom = design.getByRole('button', { name: /^Apply to (?!whole)/ });
  const box = (await canvas.boundingBox())!;
  for (const [fx, fy] of [[0.5, 0.6], [0.45, 0.55], [0.55, 0.5], [0.4, 0.45], [0.6, 0.65], [0.35, 0.35], [0.65, 0.4]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
    if (await design.isVisible() && await applyRoom.isEnabled()) break;
  }
  await design.getByRole('radio', { name: 'Theme Indian Contemporary' }).click();
  await design.getByRole('button', { name: 'Apply to whole floor' }).click();
  await page.waitForTimeout(4000);
  await shot(page, '06-theme-top-down');
  const designer = page.getByTestId('interior-designer');
  await designer.getByLabel('Describe the look you want').fill('Warm and calm, lots of wood and plants, and a reading corner.');
  await designer.getByRole('button', { name: 'Design it' }).click();
  await expect(designer.getByTestId('design-budget')).toBeVisible({ timeout: 180_000 });
  await page.waitForTimeout(3000);
  await shot(page, '07-interior-designer');
  await designer.getByRole('button', { name: 'Keep design' }).click();
  const render = page.getByTestId('photo-render');
  await render.getByRole('button', { name: 'Render this room' }).click();
  const img = render.getByTestId('render-image');
  await expect(img).toBeVisible({ timeout: 180_000 });
  await page.waitForTimeout(2000);
  await shot(page, '08-render-panel');
  const res = await page.request.get((await img.getAttribute('src'))!);
  writeFileSync(`${OUT}08-render.${res.headers()['content-type'].split('/')[1]}`, await res.body());
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '2D', exact: true }).click();
  await page.getByRole('button', { name: '3D', exact: true }).click();
  await page.waitForTimeout(4000);
  await shot(page, '09-3d-furnished');
  await page.getByRole('button', { name: 'Save', exact: true }).click();

  // 4. Services, cost, green (with the map pin and Google's Solar API), and the agent team.
  await page.getByRole('link', { name: /Services/ }).click();
  await page.getByRole('button', { name: 'Generate electrical diagram' }).click();
  await expect(page.getByTestId('diagram-status')).toHaveText('Complete', { timeout: 180_000 });
  await shot(page, '10-electrical', true);
  await page.getByRole('tab', { name: 'Plumbing 2D' }).click();
  await page.getByRole('button', { name: 'Generate plumbing diagram' }).click();
  await expect(page.getByTestId('diagram-status')).toHaveText('Complete', { timeout: 180_000 });
  await shot(page, '11-plumbing', true);
  await page.getByRole('tab', { name: 'Cost' }).click();
  await page.getByRole('button', { name: 'Estimate cost' }).click();
  await expect(page.getByTestId('estimate-total')).toContainText('lakh', { timeout: 120_000 });
  await shot(page, '12-cost', true);
  await page.getByRole('tab', { name: 'Green' }).click();
  if (process.env.MAPS_BROWSER_KEY) {
    await page.getByRole('button', { name: 'Pin your plot on the map' }).click();
    const pin = page.getByTestId('plot-pin');
    await pin.getByLabel('Plot address').fill('Anna Nagar, Chennai');
    await pin.getByRole('button', { name: 'Find' }).click();
    await page.waitForTimeout(2500);
    await shot(page, '13-map-pin');
    await pin.getByRole('button', { name: 'Use this location' }).click({ timeout: 60_000 });
    await expect(page.getByTestId('site-summary')).toContainText('Chennai', { timeout: 30_000 });
  } else {
    await page.getByLabel('City').fill('Chennai');
  }
  await page.getByRole('button', { name: 'Check green performance' }).click();
  await expect(page.getByTestId('green-score')).toBeVisible({ timeout: 180_000 });
  await shot(page, '14-green', true);
  await page.getByLabel('Message to Greenprint').fill('Where are we, and what would make this home greener?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(page.getByTestId('agent-chat').locator('[data-role="agent"]').first().getByTestId('agent-meta')).toBeVisible({ timeout: 180_000 });
  await shot(page, '15-agent-team');
});
