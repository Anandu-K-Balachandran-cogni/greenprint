import { expect, test } from '@playwright/test';
import { confirmedProject, singleFloorBrief, skipEditorTips, solvedRun, waitForSolved } from './helpers';

// Live: real Gemini review and change interpretation, real solver re-planning.
test('AI review cites measured facts; a change request re-plans close to the option with a before/after', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Review ${Date.now()}`, singleFloorBrief('Review'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 15 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  const option = page.getByTestId('candidate-0');
  await expect(option.getByTestId('check-status')).toHaveText('Checks passed');

  await option.getByRole('button', { name: 'Architect’s review' }).click();
  const review = option.getByTestId('ai-review');
  await expect(review.getByTestId('review-summary')).not.toBeEmpty({ timeout: 90_000 });
  // Every statement carries at least one citation, and every citation resolves to a fact.
  const items = review.locator('li');
  expect(await items.count()).toBeGreaterThan(0);
  for (const li of await items.all()) expect(await li.locator('[data-fact]').count()).toBeGreaterThan(0);
  for (const chip of await review.locator('[data-fact]').all()) expect(await chip.getAttribute('title')).toBeTruthy();
  await page.screenshot({ path: test.info().outputPath('review.png'), fullPage: true });

  // An ambiguous request asks rather than guesses, and stays on this page.
  await option.getByLabel('Request changes to option 1').fill('make the bedroom bigger');
  await option.getByRole('button', { name: 'Re-plan with these changes' }).click();
  await expect(option.getByTestId('clarification')).toBeVisible({ timeout: 90_000 });
  await expect(page).toHaveURL(new RegExp(`/layouts/${run.id}$`));

  // A clear request becomes typed changes and a checked revision.
  const before = run.candidates[0].allocation.spaces.find((s: { id: string }) => s.id === 'bedroom-1').area_m2;
  // Explicit enough that interpretation is not a judgement call (ambiguity is tested above).
  await option.getByLabel('Request changes to option 1').fill('Make the Kids bedroom at least 16 square metres');
  await option.getByRole('button', { name: 'Re-plan with these changes' }).click();
  await expect(page.getByTestId('revision')).toBeVisible({ timeout: 150_000 });
  await expect(page.getByTestId('changes-applied')).toContainText('Kids bedroom: at least 16 m²');
  await waitForSolved(page);
  const revised = page.getByTestId('candidate-0');
  await expect(revised.getByTestId('check-status')).toHaveText('Checks passed');
  const row = page.getByTestId('revision').locator('[data-row="bedroom-1"]');
  await expect(row).toContainText(`${before.toFixed(1)} m²`);
  await expect(row.locator('td').nth(3)).toHaveText(/^\+\d+\.\d m²$/);
  await page.screenshot({ path: test.info().outputPath('revision.png'), fullPage: true });

  await revised.getByRole('button', { name: 'Open in editor' }).click();
  await expect(page).toHaveURL(/\/editor\?id=cos-/);
  await expect(page.getByRole('application')).toContainText(/\d+ rooms/);

  // The requirements page lists the revision with its request.
  await page.goto(`/build/${id}`);
  await expect(page.getByRole('link', { name: /Revision: “Make the Kids bedroom at least 16 square metres”/ })).toBeVisible();
  expect(errors).toEqual([]);
});
