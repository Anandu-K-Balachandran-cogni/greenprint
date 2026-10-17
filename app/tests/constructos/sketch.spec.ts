import { expect, test } from '@playwright/test';
import { fileURLToPath } from 'node:url';

// Live (real engine + Gemini): a phone photo of a hand sketch, plus a note typed in Hindi, becomes a
// reviewed brief. Plot 35 x 50 ft (10,668 × 15,240 mm), G+1, three bedrooms, one car, and the pooja room
// that only the Hindi note asked for.
const PHOTO = fileURLToPath(new URL('../../../engine/tests/fixtures/sketches/house_sketch_photo.jpg', import.meta.url));
const PLOT = fileURLToPath(new URL('../../../engine/tests/fixtures/sketches/plot_survey.png', import.meta.url));

test('a hand sketch photo and a Hindi note become a reviewed brief', async ({ page }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/build');
  await page.getByLabel('Project name').fill(`Sketch house ${Date.now()}`);
  await page.getByLabel('Building brief').fill('मुझे एक पूजा कमरा भी चाहिए।');

  const intake = page.getByTestId('sketch-intake');
  await intake.getByLabel('Add sketch or plot photo').setInputFiles([PHOTO, PLOT]);
  await expect(intake.getByRole('img')).toHaveCount(2);
  await intake.getByRole('button', { name: /^Remove plot_survey/ }).click();   // the user changes their mind
  await expect(intake.getByRole('img')).toHaveCount(1);
  await intake.getByRole('button', { name: 'Read my sketch' }).click();

  const reading = page.getByTestId('sketch-reading');
  await expect(reading).toContainText('Hindi', { timeout: 120_000 });
  await expect(reading).toContainText(/35\s*x\s*50/i);
  const brief = page.getByLabel('Building brief');
  await expect(brief).toHaveValue(/35\s*x\s*50\s*ft/i);
  await expect(brief).toHaveValue(/pooja|prayer/i);
  await page.screenshot({ path: test.info().outputPath('sketch-read.png'), fullPage: true });

  await page.getByRole('button', { name: 'Extract requirements' }).click();
  await expect(page).toHaveURL(/\/build\/[0-9a-f-]{36}$/, { timeout: 120_000 });
  await expect(page.getByTestId('plot-mm')).toHaveText('10,668 × 15,240 mm');
  await expect(page.getByLabel('Number of floors')).toHaveValue('2');
  await expect(page.getByLabel('Parking spaces')).toHaveValue('1');
  const summary = page.getByTestId('room-summary');
  await expect(summary).toContainText(/3 × bedroom|1 × master bedroom/);
  await expect(summary).toContainText(/prayer|pooja/i);
  await page.screenshot({ path: test.info().outputPath('sketch-requirements.png'), fullPage: true });
  expect(errors).toEqual([]);
});
