import { expect, type APIRequestContext, type Page } from '@playwright/test';

// Shared fixtures for Greenprint journeys (not a spec file, so importing it registers no tests).
export const room = (id: string, type: string, name: string, floor_index: number | null = null) =>
  ({ id, type, name, floor_index, requirement: 'hard', min_area_m2: null, attached_bathroom: false, source: 'test' });

export function singleFloorBrief(name: string) {
  return {
    schema_version: 'constructos.brief.v1',
    original_text: `${name}: a single-floor two-bedroom house on a 40 x 60 ft plot with one parking space and an open kitchen.`,
    plot: { width: 40, depth: 60, unit: 'ft', width_mm: null, depth_mm: null, status: 'stated' },
    storeys: 1,
    rooms: [room('master-bedroom-1', 'master_bedroom', 'Main bedroom'), room('bedroom-1', 'bedroom', 'Kids bedroom'), room('kitchen-1', 'kitchen', 'Kitchen')],
    parking_spaces: 1, open_kitchen: true, entrance_facing: 'north', entrance_status: 'stated',
    preferences: [], assumptions: [], clarifications: [], conflicts: [], extracted_by: null,
  };
}

export async function confirmedProject(request: APIRequestContext, name: string, brief = singleFloorBrief(name)) {
  const created = await request.post('/api/engine/projects', { data: { name } });
  expect(created.status()).toBe(201);
  const { id } = await created.json();
  const confirmed = await request.post(`/api/engine/projects/${id}/brief/confirm`, { data: { brief, expected_version: 0 } });
  expect(confirmed.status(), await confirmed.text()).toBe(200);
  return id as string;
}

export async function skipEditorTips(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('hasSeenWelcome', 'true');
    localStorage.setItem('o3d_tips_seen', JSON.stringify(['first-wall', 'first-furniture', 'first-3d', 'first-export', 'first-door']));
  });
}


export function twoStoreyBrief(name: string) {
  const brief = singleFloorBrief(name);
  brief.original_text = `${name}: a two-storey three-bedroom house on a 40 x 60 ft plot with two parking spaces and an open kitchen.`;
  brief.storeys = 2;
  brief.parking_spaces = 2;
  brief.rooms = [room('master-bedroom-1', 'master_bedroom', 'Parents bedroom', 0), room('bedroom-1', 'bedroom', 'Bedroom 2'),
    room('bedroom-2', 'bedroom', 'Bedroom 3'), room('kitchen-1', 'kitchen', 'Kitchen', 0)];
  return brief;
}

/** Queue a layout run through the API and poll until it finishes (generation runs in the background). */
export async function solvedRun(request: APIRequestContext, id: string, data: Record<string, unknown>) {
  const queued = await request.post(`/api/engine/projects/${id}/layouts`, { data: { expected_brief_version: 1, ...data } });
  expect(queued.status(), await queued.text()).toBe(202);
  const { id: runId } = await queued.json();
  for (let i = 0; i < 400; i++) {
    const run = await (await request.get(`/api/engine/projects/${id}/layouts/${runId}`)).json();
    if (!['queued', 'running'].includes(run.status)) {
      expect(run.status).toBe('complete');
      return run;
    }
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('layout run did not finish');
}

/** On a run page, wait until background solving has finished. */
export async function waitForSolved(page: Page) {
  await expect(page.locator('main[data-run-status]')).toHaveAttribute('data-run-status', /^(complete|failed|interrupted)$/, { timeout: 200_000 });
}
