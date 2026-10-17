import { expect, test } from '@playwright/test';
import { confirmedProject, skipEditorTips, twoStoreyBrief, solvedRun } from './helpers';

// Live: the Neuro-SAN Greenprint front agent (Gemini) bringing in the architect and both engineers.
test('Ask Greenprint: project status, both engineers in one request, and a layout change', async ({ page, request }) => {
  test.setTimeout(360_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await skipEditorTips(page);
  const id = await confirmedProject(request, `Agent ${Date.now()}`, twoStoreyBrief('Agent'));
  const run = await solvedRun(request, id, { alternatives: 1, time_limit_s: 25 });
  await page.goto(`/build/${id}/layouts/${run.id}`);
  await page.getByTestId('candidate-0').getByRole('button', { name: 'Open in editor' }).click();
  await page.getByRole('link', { name: /Services/ }).click();
  await expect(page.getByTestId('no-diagram')).toBeVisible();

  const replies = page.getByTestId('agent-chat').locator('[data-role="agent"]');
  const input = page.getByLabel('Message to Greenprint');
  async function ask(text: string, n: number) {
    await input.fill(text);
    await page.getByRole('button', { name: 'Ask', exact: true }).click();
    const reply = replies.nth(n);
    await expect(reply.getByTestId('agent-meta')).toBeVisible({ timeout: 150_000 });
    return reply;
  }

  // 1. Where are we? — from the project's stored state.
  const status = await ask('Where are we with my house?', 0);
  await expect(status.getByTestId('agent-meta')).toContainText('project status');

  // 2. One request, two experts: points added to the electrical layout and the house plumbed.
  const both = await ask('Please add an AC point in Bedroom 2 and Bedroom 3, and plumb the house.', 1);
  await expect(both.getByTestId('agent-meta')).toContainText('electrical engineer');
  await expect(both.getByTestId('agent-meta')).toContainText('plumbing engineer');
  await page.getByRole('tab', { name: 'Plumbing 2D' }).click();
  await expect(page.getByTestId('diagram-status')).toHaveText('Complete');
  await page.getByRole('tab', { name: 'Electrical 2D' }).click();
  // Two AC points on the first floor, plus the legend symbol.
  await page.getByRole('group', { name: 'Floor' }).getByRole('button', { name: 'First floor' }).click();
  await expect(page.getByTestId('diagram-view').locator('[data-kind="ac_point"]')).toHaveCount(3);

  // 3. A layout change goes to the architect and queues a real revision.
  const change = await ask('Make Bedroom 2 at least 14 square metres.', 2);
  await expect(change.getByTestId('agent-meta')).toContainText('architect');
  await change.getByRole('link', { name: 'See the revised layout →' }).click();
  await expect(page.getByTestId('changes-applied')).toContainText('Bedroom 2: at least 14 m²');
  await expect(page.locator('main[data-run-status]')).toHaveAttribute('data-run-status', 'complete', { timeout: 120_000 });
  await page.screenshot({ path: test.info().outputPath('agent-revision.png'), fullPage: true });
  expect(errors).toEqual([]);
});
