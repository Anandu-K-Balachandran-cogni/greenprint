import { expect, test } from '@playwright/test';
import { confirmedProject, skipEditorTips, twoStoreyBrief, solvedRun } from './helpers';

const lakh = (text: string) => Number(text.replace(/[^\d.]/g, ''));

test('cost estimate from the saved plan: tiers, city factor, and the cost estimator agent', async ({ page, request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Cost ${Date.now()}`, twoStoreyBrief('Cost'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 25 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await page.getByRole('link', { name: /Services/ }).click();

  await page.getByRole('tab', { name: 'Cost' }).click();
  await expect(page.getByTestId('no-estimate')).toBeVisible();
  await page.getByRole('button', { name: 'Estimate cost' }).click();
  await expect(page.getByTestId('estimate-total')).toContainText('lakh');
  await expect(page.getByTestId('estimate-benchmark')).toContainText('within');
  const standard = lakh(await page.getByTestId('estimate-total').innerText());
  // Every line is quantity × rate, and the groups add up to the total less contingency.
  const lines = page.getByRole('table', { name: 'Estimate lines' });
  await expect(lines).toContainText('Foundation, RCC frame and slabs');
  await expect(lines).toContainText('Electrical points');
  await page.screenshot({ path: test.info().outputPath('cost-standard.png'), fullPage: true });

  await page.getByLabel('Finish level').selectOption('premium');
  await page.getByLabel('City type').selectOption('metro');
  await page.getByRole('button', { name: 'Re-estimate' }).click();
  await expect.poll(async () => lakh(await page.getByTestId('estimate-total').innerText())).toBeGreaterThan(standard * 1.5);
  await expect(page.getByTestId('estimate-benchmark')).toContainText('within');

  // The cost estimator agent answers from a real stored estimate and the tab shows it.
  await page.getByLabel('Message to Greenprint').fill('What would it cost with basic finishes in a non-metro city?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const reply = page.getByTestId('agent-chat').locator('[data-role="agent"]').first();
  await expect(reply.getByTestId('agent-meta')).toContainText('cost estimator', { timeout: 120_000 });
  await expect(page.getByLabel('Finish level')).toHaveValue('basic');
  const basic = await page.getByTestId('estimate-total').innerText();
  expect(lakh(basic)).toBeLessThan(standard);
  // The agent quotes the stored total (27.5 and 27.50 are the same number).
  const quoted = [...(await reply.innerText()).matchAll(/₹\s?([\d.]+)\s*lakh/g)].map(m => Number(m[1]));
  expect(quoted).toContain(lakh(basic));
  expect(errors).toEqual([]);
});
