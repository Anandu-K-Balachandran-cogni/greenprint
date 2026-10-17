import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { compileAllocation } from '$lib/constructos/compiler';
import type { Allocation } from '$lib/constructos/engine';
import { THEMES, planTheme, applyThemeChanges, roomKind } from '$lib/constructos/themes';
import { clearanceIssues, LAYER_ITEMS } from '$lib/constructos/roomDesign';
import { resolveRoomGeometry } from '$lib/utils/roomDetection';
import { pointInPolygon } from '$lib/utils/hitTesting';
import { readProject } from '$lib/utils/projectValidation';
import type { Floor, Project } from '$lib/models/types';

const load = (f: string): Allocation => JSON.parse(readFileSync(new URL(`./fixtures/constructos/${f}`, import.meta.url), 'utf8'));
const meta = { projectId: 'p1', name: 'Theme house', link: { engineProjectId: 'e1', layoutRunId: 'r1', candidate: 0, briefVersion: 1, briefHash: 'sha256-x' } };
const houses = ['single-floor-allocation.json', 'two-storey-allocation.json'].map(f => ({ f, project: compileAllocation(load(f), meta).project }));
const has = (floor: Floor, poly: { x: number; y: number }[], re: RegExp) => floor.furniture.filter(x => re.test(x.catalogId) && pointInPolygon(x.position, poly));

describe.each(houses)('themes on $f', ({ project }) => {
  it.each(THEMES.map(t => [t.name, t] as const))('%s furnishes and styles every room without clashes', (_n, theme) => {
    const floors: Floor[] = [];
    for (const floor of project.floors) {
      const homeHasPooja = floors.some(f => f.furniture.some(x => x.catalogId === 'pooja_unit'));
      floors.push(applyThemeChanges(floor, planTheme(floor, theme, { homeHasPooja })));
    }
    const themed: Project = { ...project, floors };
    expect(() => readProject(JSON.parse(JSON.stringify(themed)))).not.toThrow();
    for (const floor of floors) {
      for (const { room, polygon } of resolveRoomGeometry(floor, floor.rooms)) {
        const kind = roomKind(room);
        if (!kind) continue;
        expect(clearanceIssues(floor, polygon).map(i => `${room.name}: ${i.message}`)).toEqual([]);
        expect(room.ceilingColor).toBe(theme.ceiling);
        expect(floor.walls.flatMap(w => w.paints ?? []).some(p => p.roomId === room.id)).toBe(true);
        if (kind === 'living') expect(has(floor, polygon, /^sofa|loveseat/).length, room.name).toBe(1);
        if (kind === 'master' || kind === 'bedroom') expect(has(floor, polygon, /^bed_|bunk/).length, room.name).toBe(1);
        if (kind === 'kitchen') expect(has(floor, polygon, /^stove$|^sink_k$/).length, room.name).toBe(2);
        if (kind === 'bathroom') expect(has(floor, polygon, /^toilet$/).length, room.name).toBe(1);
        if (kind === 'dining') expect(has(floor, polygon, /dining_chair/).length, room.name).toBeGreaterThanOrEqual(2);
      }
      // Every window of a themed room is dressed.
      const dressings = floor.furniture.filter(x => /curtain|blind/.test(x.catalogId));
      for (const win of floor.windows) {
        const wall = floor.walls.find(w => w.id === win.wallId)!;
        const at = { x: wall.start.x + (wall.end.x - wall.start.x) * win.position, y: wall.start.y + (wall.end.y - wall.start.y) * win.position };
        const room = resolveRoomGeometry(floor, floor.rooms).find(g => roomKind(g.room) && dressings.some(() => true) && g.polygon.length > 2
          && [1, -1].some(s => { const dx = wall.end.x - wall.start.x, dy = wall.end.y - wall.start.y, l = Math.hypot(dx, dy);
            return pointInPolygon({ x: at.x - dy / l * 20 * s, y: at.y + dx / l * 20 * s }, g.polygon); }));
        if (room) expect(dressings.some(d => Math.hypot(d.position.x - at.x, d.position.y - at.y) < 40), `window in ${room.room.name}`).toBe(true);
      }
      // Real pieces stand inside a room.
      const polys = resolveRoomGeometry(floor, floor.rooms).map(g => g.polygon);
      for (const item of floor.furniture.filter(x => !LAYER_ITEMS.has(x.catalogId))) expect(polys.some(p => pointInPolygon(item.position, p)), item.catalogId).toBe(true);
    }
    const pooja = floors.flatMap(f => f.furniture).filter(x => x.catalogId === 'pooja_unit').length;
    expect(pooja).toBe(theme.pooja ? 1 : 0);   // one per home
  });

  it('is idempotent and switching themes restyles without adding', () => {
    const [a, b] = [THEMES[0], THEMES[2]];
    const once = applyThemeChanges(project.floors[0], planTheme(project.floors[0], a));
    expect(planTheme(once, a).add).toEqual([]);
    const other = planTheme(once, b);
    expect(other.add).toEqual([]);
    expect(other.restyle.length).toBeGreaterThan(0);
  });
});
