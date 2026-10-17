import { expect, test } from '@playwright/test';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun } from './helpers';

// Live (Google Maps JavaScript + Geocoding): find the plot, pin it, and the green check uses the pinned city.
// Needs MAPS_BROWSER_KEY in the environment (the referrer-restricted browser key).
test('pin the plot on Google Maps and check green performance there', async ({ page, request }) => {
  test.skip(!process.env.MAPS_BROWSER_KEY, 'MAPS_BROWSER_KEY not set');
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Pin ${Date.now()}`, singleFloorBrief('Pin'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 15 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await page.getByRole('link', { name: /Services/ }).click();
  await page.getByRole('tab', { name: 'Green' }).click();

  await expect(page.getByTestId('solar-coverage-note')).toContainText('selected areas');
  await page.getByRole('button', { name: 'Where is this available?' }).click();
  await expect(page.getByTestId('solar-coverage-info')).toContainText('most of India');
  await page.getByTestId('solar-coverage-info').locator('..').screenshot({ path: test.info().outputPath('coverage-info.png') });
  await page.getByRole('button', { name: 'Pin your plot on the map' }).click();
  const pin = page.getByTestId('plot-pin');
  await pin.getByLabel('Plot address').fill('Anna Nagar, Chennai');
  await pin.getByRole('button', { name: 'Find' }).click();
  await pin.getByRole('button', { name: 'Use this location' }).click({ timeout: 60_000 });
  await expect(page.getByTestId('site-summary')).toContainText('Chennai', { timeout: 30_000 });
  await expect(page.getByLabel('City')).toHaveValue(/Chennai/);
  await page.getByRole('button', { name: 'Pin your plot on the map' }).or(page.getByRole('button', { name: 'Move the pin' })).first().click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: test.info().outputPath('pin-map.png') });
  await page.getByRole('button', { name: 'Hide map' }).click();

  await page.getByRole('button', { name: 'Check green performance' }).click();
  await expect(page.getByTestId('green-climate')).toContainText('Chennai', { timeout: 150_000 });
  // Google's Solar API measured the plot's sun (Anna Nagar is covered), and the solar sizing uses it.
  if (process.env.MAPS_SOLAR_KEY) await expect(page.getByTestId('solar-site')).toContainText(/Measured at your plot/);
  await page.getByTestId('green-solar').screenshot({ path: test.info().outputPath('solar-card.png') });
  // The pin is stored with the project.
  const project = await (await request.get(`/api/engine/projects/${id}`)).json();
  expect(project.site.city).toBe('Chennai');
  expect(project.site.lat).toBeGreaterThan(12.9); expect(project.site.lat).toBeLessThan(13.2);
  expect(errors).toEqual([]);
});
