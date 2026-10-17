import { expect, test } from '@playwright/test';
import { confirmedProject, room, singleFloorBrief, waitForSolved } from './helpers';

// Regression for a real session: room sizes typed in square feet into what was a
// square-metre column made every layout INFEASIBLE ("needs at least 982 m²").
function usersBrief() {
  const brief = singleFloorBrief('Family House');
  brief.original_text = 'A house with 40*60 sqft - with 2 rooms 1 kitchen and one drawing rom';
  brief.rooms = [room('bedroom-1', 'bedroom', 'Bedroom 1'), room('bedroom-2', 'bedroom', 'Bedroom 2'),
    room('kitchen-1', 'kitchen', 'Kitchen'), room('living-1', 'living', 'Drawing room')];
  brief.open_kitchen = false;
  return brief;
}

test('room areas follow the plot unit: sq ft entered, m² stored, layout solves', async ({ page, request }) => {
  const id = await confirmedProject(request, `Family House ${Date.now()}`, usersBrief());
  await page.goto(`/build/${id}`);
  await expect(page.getByRole('columnheader', { name: 'Min. area (sq ft)' })).toBeVisible();

  for (const [rid, sqft] of [['bedroom-1', '250'], ['bedroom-2', '250'], ['kitchen-1', '150'], ['living-1', '300']] as const) {
    const input = page.getByLabel(`Minimum area ${rid}`);
    await input.fill(sqft);
    await input.press('Tab');
  }
  await expect(page.getByTestId('area-m2-bedroom-1')).toHaveText('= 23.23 m²');
  await expect(page.getByTestId('area-m2-living-1')).toHaveText('= 27.87 m²');
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v2 confirmed');

  const stored = await (await request.get(`/api/engine/projects/${id}`)).json();
  expect(stored.brief.brief.rooms.map((r: { min_area_m2: number }) => r.min_area_m2)).toEqual([23.23, 23.23, 13.94, 27.87]);
  await page.reload();
  await expect(page.getByLabel('Minimum area bedroom-1')).toHaveValue('250');

  await page.getByRole('button', { name: 'Generate layouts' }).click();
  await expect(page).toHaveURL(/\/layouts\//, { timeout: 150_000 });
  await waitForSolved(page);
  const option = page.getByTestId('candidate-0');
  await expect(option.getByTestId('solver-status')).toHaveAttribute('data-status', /^(OPTIMAL|FEASIBLE)$/);
  await expect(option.getByTestId('check-status')).toHaveText('Checks passed');
  await expect(option.locator('[data-space-id="living-1"]')).toContainText('Drawing room');
});

test('an implausible m² size is flagged with its sq ft reading; an impossible one blocks confirmation', async ({ page, request }) => {
  const brief = usersBrief();
  brief.plot = { width: 12.2, depth: 18.3, unit: 'm', width_mm: null, depth_mm: null, status: 'stated' };
  const id = await confirmedProject(request, `Metric ${Date.now()}`, brief);
  await page.goto(`/build/${id}`);
  await expect(page.getByRole('columnheader', { name: 'Min. area (m²)' })).toBeVisible();

  await page.getByLabel('Minimum area bedroom-1').fill('150');
  await page.getByLabel('Minimum area bedroom-1').press('Tab');
  await page.getByRole('button', { name: 'Check brief' }).click();
  await expect(page.getByLabel('Conflicts')).toContainText('If you meant 150 sq ft, that is 13.9 m²');

  await page.getByLabel('Minimum area bedroom-2').fill('250');
  await page.getByLabel('Minimum area bedroom-2').press('Tab');
  await page.getByRole('button', { name: 'Confirm brief' }).click();
  await expect(page.getByRole('alert')).toHaveText('Some required information is missing.');
  await expect(page.getByText('Bedroom 2 needs 250 m², more than the whole plot (223 m²).')).toBeVisible();
  await expect(page.getByTestId('brief-status')).toHaveText('Brief v1 confirmed · unsaved changes');
});
