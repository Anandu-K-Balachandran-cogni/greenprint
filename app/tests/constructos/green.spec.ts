import { expect, test } from '@playwright/test';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun } from './helpers';

// Live (Gemini with Google Search grounding + the sustainability advisor network): green performance of the
// saved plan for a city, window fixes applied to the plan, and a green question routed by the front agent.
test('green check for a city, window fixes, and the sustainability advisor', async ({ page, request }) => {
  test.setTimeout(420_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Green ${Date.now()}`, singleFloorBrief('Green'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 15 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await page.getByRole('link', { name: /Services/ }).click();
  await page.getByRole('tab', { name: 'Green' }).click();
  await expect(page.getByTestId('no-green')).toBeVisible();

  await page.getByLabel('City').fill('Chennai');
  await page.getByLabel('Entrance faces').selectOption('east');
  await page.getByRole('button', { name: 'Check green performance' }).click();
  const score = page.getByTestId('green-score');
  await expect(score).toBeVisible({ timeout: 150_000 });
  const before = Number((await score.innerText()).split('/')[0]);
  const climate = page.getByTestId('green-climate');
  await expect(climate).toContainText('Chennai');
  await expect(climate).toContainText(/warm-humid/);
  await expect(climate.getByRole('link').first()).toBeVisible();      // cited sources
  await expect(page.getByRole('table', { name: 'Rooms' }).locator('tbody tr')).not.toHaveCount(0);
  await expect(page.getByTestId('green-water')).toContainText(/L a year/);
  await expect(page.getByTestId('green-solar')).toContainText(/kWp/);
  await expect(page.getByTestId('green-carbon')).toContainText(/t CO₂e/);
  await page.screenshot({ path: test.info().outputPath('green-before.png'), fullPage: true });

  const apply = page.getByRole('button', { name: /^Apply window fixes/ });
  if (await apply.isVisible()) {
    await apply.click();
    await expect(page.getByTestId('green-applied')).toBeVisible({ timeout: 150_000 });
    expect(Number((await score.innerText()).split('/')[0])).toBeGreaterThanOrEqual(before);
    await page.screenshot({ path: test.info().outputPath('green-after.png'), fullPage: true });
  }

  // The front agent sends a green question to the sustainability advisor, which checks another city.
  await page.getByLabel('Message to Greenprint').fill('How green would this home be in Pune? What size of solar should we put on the roof?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const reply = page.getByTestId('agent-chat').locator('[data-role="agent"]').first();
  await expect(reply.getByTestId('agent-meta')).toContainText(/sustainability/i, { timeout: 180_000 });
  await expect(reply).toContainText(/kWp/);
  await expect(page.getByTestId('green-climate')).toContainText('Pune', { timeout: 30_000 });
  expect(errors).toEqual([]);
});
