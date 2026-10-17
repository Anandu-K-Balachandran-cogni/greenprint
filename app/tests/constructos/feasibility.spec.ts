import { expect, test } from '@playwright/test';
import { confirmedProject, singleFloorBrief, waitForSolved } from './helpers';

test('Check brief explains feasibility with measured numbers, and layouts show their evidence', async ({ page, request }) => {
  const id = await confirmedProject(request, `Feasibility ${Date.now()}`);
  await page.goto(`/build/${id}`);

  const panel = page.getByTestId('feasibility');
  await expect(panel.getByTestId('feasibility-verdict')).toHaveText('Fits');
  await expect(panel.getByTestId('buildable')).toHaveText('10.2 × 15.6 m · 159 m²');
  await expect(panel.getByTestId('floor-budget-0')).toContainText('of 159 m²');
  await expect(panel.locator('[data-check="FRONTAGE"]')).toHaveAttribute('data-status', 'pass');
  await expect(panel.locator('[data-check="FLOOR_AREA"]')).toContainText(/Minimum rooms need \d+ m² of 159 m²/);
  await page.screenshot({ path: test.info().outputPath('feasibility-fits.png'), fullPage: true });

  // An edit that cannot work: a 20 ft wide plot with two cars side by side.
  await page.getByLabel('Plot width').fill('20');
  await page.getByLabel('Parking spaces').fill('2');
  await expect(panel.getByRole('status')).toContainText('Click “Check brief” to update it');
  await page.getByRole('button', { name: 'Check brief' }).click();
  await expect(panel.getByTestId('feasibility-verdict')).toHaveText('Does not fit');
  await expect(panel.getByTestId('feasibility-headline')).toContainText('This brief cannot fit the plot:');
  await expect(panel.locator('[data-status="fail"]').first()).toContainText(/Parking \(2 cars\)/);
  await page.screenshot({ path: test.info().outputPath('feasibility-fails.png'), fullPage: true });

  // Restore the confirmed brief and generate: every option explains why it passes.
  await page.reload();
  await expect(panel.getByTestId('feasibility-verdict')).toHaveText('Fits');
  await page.getByRole('button', { name: 'Generate layouts' }).click();
  await expect(page).toHaveURL(/\/layouts\//, { timeout: 150_000 });
  await waitForSolved(page);
  const evidence = page.getByTestId('candidate-0').getByTestId('layout-evidence');
  await evidence.locator('summary').click();
  await expect(evidence.locator('summary')).toHaveText(/Why this design works · 9\/9 checks/);
  await expect(evidence.locator('[data-check="exact_tiling"]')).toContainText(/Spaces fill the footprint exactly \(floor 0: ([\d.]+) of \1 m²\)/);
  await expect(evidence.locator('[data-check="reachability"]')).toHaveAttribute('data-status', 'pass');
  await expect(evidence.getByTestId('metric-coverage')).toHaveText(/^\d+% of plot$/);
  await page.screenshot({ path: test.info().outputPath('layout-evidence.png'), fullPage: true });
});

test('feasibility asks for missing essentials instead of guessing', async ({ page, request }) => {
  const brief = singleFloorBrief('Partial');
  const id = await confirmedProject(request, `Partial ${Date.now()}`, brief);
  await page.goto(`/build/${id}`);
  await page.getByLabel('Number of floors').fill('');
  await page.getByRole('button', { name: 'Check brief' }).click();
  await expect(page.getByTestId('feasibility-verdict')).toHaveText('Incomplete');
  await expect(page.getByTestId('feasibility-headline')).toHaveText('Add the number of floors to assess feasibility.');
});
