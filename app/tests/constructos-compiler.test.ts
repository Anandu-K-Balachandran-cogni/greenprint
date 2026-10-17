import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { compileAllocation, CompileError, EXTERIOR_WALL_CM, FLOOR_TO_FLOOR_CM, INTERIOR_WALL_CM, netRoomSize, PARAPET_HEIGHT_CM, stairInCore } from '$lib/constructos/compiler';
import { stairPlanBounds } from '$lib/utils/stairPlanGeometry';
import type { Allocation, PlacedSpace } from '$lib/constructos/engine';
import { readProject } from '$lib/utils/projectValidation';
import { detectRooms } from '$lib/utils/roomDetection';

const solved: Allocation = JSON.parse(readFileSync(new URL('./fixtures/constructos/single-floor-allocation.json', import.meta.url), 'utf8'));
const twoStorey: Allocation = JSON.parse(readFileSync(new URL('./fixtures/constructos/two-storey-allocation.json', import.meta.url), 'utf8'));
// 2 bedrooms on a 30 x 40 ft plot: the living room's only outside wall is its 3 m front, which also takes the entrance.
const narrowFront: Allocation = JSON.parse(readFileSync(new URL('./fixtures/constructos/narrow-front-allocation.json', import.meta.url), 'utf8'));
const meta = {
  projectId: 'p1', name: 'Test house',
  link: { engineProjectId: 'e1', layoutRunId: 'r1', candidate: 0, briefVersion: 1, briefHash: 'sha256-x' },
};
const space = (id: string, x: number, y: number, w: number, h: number, exterior_sides: PlacedSpace['exterior_sides'], type = 'bedroom'): PlacedSpace =>
  ({ id, type, name: id, floor: 0, x_mm: x, y_mm: y, w_mm: w, h_mm: h, area_m2: (w * h) / 1e6, exterior_sides, filler: false });

/** Hand-authored: a living room at the front and a bedroom behind it, 3 m wide. */
const twoRooms: Allocation = {
  schema_version: 'constructos.allocation.v1', status: 'OPTIMAL', alternative: 0, seed: 0, solve_seconds: 0, objective: 0,
  grid_mm: 300, plot: { width_mm: 5000, depth_mm: 10000 }, setbacks: { front_mm: 1000, rear_mm: 1000, side_mm: 1000 },
  footprint: { x_mm: 1000, y_mm: 1000, width_mm: 3000, depth_mm: 8000 },
  spaces: [
    space('living-1', 1000, 1000, 3000, 4000, ['front', 'left', 'right'], 'living'),
    space('bedroom-1', 1000, 5000, 3000, 4000, ['rear', 'left', 'right']),
  ],
  passages: [
    { a: 'exterior', b: 'living-1', kind: 'entrance', floor: 0, side: null, overlap_mm: 3000 },
    { a: 'living-1', b: 'bedroom-1', kind: 'door', floor: 0, side: 'rear', overlap_mm: 3000 },
  ],
  assumptions: [], message: null,
};

describe('ConstructOS compiler', () => {
  it('deduplicates the shared wall and merges collinear exterior edges (hand oracle)', () => {
    const { project, problems } = compileAllocation(twoRooms, meta);
    expect(problems).toEqual([]);
    const walls = project.floors[0].walls;
    // left and right exterior sides each run the full 8 m; one interior partition; front and rear.
    expect(walls).toHaveLength(5);
    const interior = walls.filter(w => w.thickness === INTERIOR_WALL_CM);
    expect(interior).toHaveLength(1);
    expect(interior[0].start).toEqual({ x: 100, y: 500 });
    expect(interior[0].end).toEqual({ x: 400, y: 500 });
    expect(walls.filter(w => w.thickness === EXTERIOR_WALL_CM)).toHaveLength(4);
    // The road side (y = 1000 mm in plot terms) is drawn at the bottom of the plan.
    const front = walls.find(w => w.start.y === 900 && w.end.y === 900);
    expect(front).toBeDefined();

    const doors = project.floors[0].doors;
    expect(doors.map(d => [d.wallId, d.width, d.position])).toEqual([[front!.id, 100, 0.5], [interior[0].id, 90, 0.5]]);
    expect(project.floors[0].rooms.map(r => [r.id, r.name, r.area]).sort()).toEqual([['bedroom-1', 'bedroom-1', 12], ['living-1', 'living-1', 12]]);
    // Each habitable room gets a window on an exterior wall; the living room gets two.
    const exteriorIds = new Set(walls.filter(w => w.thickness === EXTERIOR_WALL_CM).map(w => w.id));
    expect(project.floors[0].windows.every(w => exteriorIds.has(w.wallId))).toBe(true);
    expect(project.floors[0].windows).toHaveLength(3);
  });

  it('accounts for wall thickness in net room size', () => {
    expect(netRoomSize(twoRooms.spaces[0])).toEqual({ width: 300 - EXTERIOR_WALL_CM, depth: 400 - EXTERIOR_WALL_CM / 2 - INTERIOR_WALL_CM / 2 });
  });

  it('compiles a real solver result into a valid, editor-readable project', () => {
    const { project, problems } = compileAllocation(solved, meta);
    expect(problems).toEqual([]);
    const floor = project.floors[0];
    // Every programmed space becomes exactly one room bound to real walls, keeping its stable ID.
    expect(floor.rooms.map(r => r.id).sort()).toEqual(solved.spaces.map(s => s.id).sort());
    expect(detectRooms(floor.walls)).toHaveLength(solved.spaces.length);
    // Every passage is a hosted door or opening within its wall.
    expect(floor.doors).toHaveLength(solved.passages.length);
    const wallIds = new Set(floor.walls.map(w => w.id));
    for (const d of [...floor.doors, ...floor.windows]) {
      expect(wallIds.has(d.wallId)).toBe(true);
      expect(d.position).toBeGreaterThan(0);
      expect(d.position).toBeLessThan(1);
    }
    expect(floor.doors.filter(d => d.type === 'garage')).toHaveLength(1);
    expect(floor.doors.filter(d => d.type === 'opening').length).toBeGreaterThanOrEqual(2); // open kitchen + dining
    // No duplicate wall geometry.
    const keys = floor.walls.map(w => [w.start.x, w.start.y, w.end.x, w.end.y].join(','));
    expect(new Set(keys).size).toBe(keys.length);
    // The editor's own validator accepts it and keeps the ConstructOS link.
    const read = readProject(JSON.parse(JSON.stringify(project)));
    expect(read.floors[0].walls).toHaveLength(floor.walls.length);
    expect((read as any).constructos.briefHash).toBe('sha256-x');
  });

  it('is deterministic for identical input', () => {
    const now = new Date('2026-10-12T00:00:00Z');
    expect(compileAllocation(solved, meta, now)).toEqual(compileAllocation(structuredClone(solved), meta, now));
  });

  it('rejects candidates without a layout or with malformed geometry instead of publishing partial state', () => {
    expect(() => compileAllocation({ ...solved, footprint: null, spaces: [], message: 'INFEASIBLE' }, meta)).toThrow(CompileError);
    const broken = structuredClone(solved);
    broken.spaces[0].w_mm = -300;
    expect(() => compileAllocation(broken, meta)).toThrow(/invalid geometry/);
  });

  it('reports, rather than hides, a passage whose rooms do not touch', () => {
    const apart = structuredClone(twoRooms);
    apart.spaces[1].x_mm += 3000;
    const { problems } = compileAllocation(apart, meta);
    expect(problems).toContain('bedroom-1 has no wall shared with its access.');
  });

  it('fits the U-stair, landing included, inside its core in either orientation', () => {
    for (const core of [{ x0: 0, x1: 2400, y0: 0, y1: 3300 }, { x0: 1000, x1: 4300, y0: 500, y1: 2900 }]) {
      const b = stairPlanBounds(stairInCore(core));
      expect(b.minX).toBeGreaterThanOrEqual(core.x0 / 10 + 9.99);
      expect(b.maxX).toBeLessThanOrEqual(core.x1 / 10 - 9.99);
      expect(b.minY).toBeGreaterThanOrEqual(core.y0 / 10 + 9.99);
      expect(b.maxY).toBeLessThanOrEqual(core.y1 / 10 - 9.99);
    }
  });

  it('compiles two storeys with a real stair, an upper slab opening above it and accessible terraces', () => {
    const { project, problems } = compileAllocation(twoStorey, meta);
    expect(problems).toEqual([]);
    expect(project.floors.map(f => [f.name, f.level, f.elevation])).toEqual([['Ground floor', 0, 0], ['First floor', 1, FLOOR_TO_FLOOR_CM]]);
    const [ground, first] = project.floors;
    expect(ground.stairs).toHaveLength(1);
    expect(first.stairs).toHaveLength(0);
    const core = twoStorey.spaces.find(s => s.id === 'stair-1')!;
    const opening = first.rooms.find(r => r.id === 'stair-1')!;
    expect(opening.floorOpening).toBe(true);
    // the flight sits inside the core that is open above it
    const b = stairPlanBounds(ground.stairs[0]);
    const D = twoStorey.plot.depth_mm;
    expect(b.minX * 10).toBeGreaterThanOrEqual(core.x_mm);
    expect(b.maxX * 10).toBeLessThanOrEqual(core.x_mm + core.w_mm);
    expect(b.minY * 10).toBeGreaterThanOrEqual(D - core.y_mm - core.h_mm);
    expect(b.maxY * 10).toBeLessThanOrEqual(D - core.y_mm);
    // each upper room is a detected room; terraces are open (outdoor) with parapets at the edge and a sliding door
    for (const s of twoStorey.spaces.filter(s => s.floor === 1 && !s.filler)) expect(first.rooms.some(r => r.id === s.id)).toBe(true);
    const terraces = first.rooms.filter(r => r.roomType === 'outdoor');
    expect(terraces.length).toBeGreaterThan(0);
    expect(first.walls.some(w => w.height === PARAPET_HEIGHT_CM)).toBe(true);
    expect(first.doors.filter(d => d.type === 'sliding').length).toBe(twoStorey.spaces.filter(s => s.filler).length);
    expect(readProject(JSON.parse(JSON.stringify(project))).floors).toHaveLength(2);
  });

  it('swings every door into the space it serves (including the entrance into the house)', () => {
    for (const alloc of [solved, twoStorey]) {
      const { project } = compileAllocation(alloc, meta);
      const D = alloc.plot.depth_mm;
      for (const floor of project.floors) {
        const f = floor.level;
        for (const [i, p] of alloc.passages.filter(x => x.floor === f).entries()) {
          const door = floor.doors.find(d => d.id === `f${f}-door-${i + 1}`);
          if (!door || door.type !== 'single') continue;
          const wall = floor.walls.find(w => w.id === door.wallId)!;
          const len = Math.hypot(wall.end.x - wall.start.x, wall.end.y - wall.start.y);
          const [ux, uy] = [(wall.end.x - wall.start.x) / len, (wall.end.y - wall.start.y) / len];
          const sign = door.flipSide ? -1 : 1; // the editor's swing side (see canvasRenderer.drawDoorOnWall)
          const at = { x: wall.start.x + ux * len * door.position, y: wall.start.y + uy * len * door.position };
          const child = alloc.spaces.find(s => s.id === p.b && s.floor === f)!;
          const centre = { x: (child.x_mm + child.w_mm / 2) / 10, y: (D - child.y_mm - child.h_mm / 2) / 10 };
          expect((centre.x - at.x) * -uy * sign + (centre.y - at.y) * ux * sign, `${p.a} -> ${p.b}`).toBeGreaterThan(0);
        }
      }
    }
  });

  it('slides the entrance aside when it would leave no room for a window on a short front wall', () => {
    const frontOnly = { ...twoRooms, spaces: [{ ...twoRooms.spaces[0], exterior_sides: ['front'] as PlacedSpace['exterior_sides'] }, twoRooms.spaces[1]] };
    for (const alloc of [frontOnly, narrowFront]) {
      const { project, problems } = compileAllocation(alloc, meta);
      expect(problems).toEqual([]);
      const floor = project.floors[0];
      const living = alloc.spaces.find(s => s.type === 'living')!;
      const entrance = floor.doors.find(d => d.id === `f0-door-${alloc.passages.findIndex(p => p.a === 'exterior' && p.b === living.id) + 1}`)!;
      const along = (o: { wallId: string; position: number; width: number }) => {
        const w = floor.walls.find(x => x.id === o.wallId)!;
        const x = w.start.x + (w.end.x - w.start.x) * o.position;
        return [x - o.width / 2, x + o.width / 2];
      };
      const windows = floor.windows.filter(w => w.wallId === entrance.wallId).map(along)
        .filter(([a, b]) => a >= living.x_mm / 10 && b <= (living.x_mm + living.w_mm) / 10);
      expect(windows.length).toBe(1);
      const [[wa, wb]] = windows, [da, db] = along(entrance);
      expect(wb - wa).toBeGreaterThanOrEqual(60);
      expect(wb <= da || db <= wa).toBe(true);   // side by side, not overlapping
      for (const [a, b] of [[wa, wb], [da, db]]) {
        expect(a).toBeGreaterThanOrEqual(living.x_mm / 10 + 10);
        expect(b).toBeLessThanOrEqual((living.x_mm + living.w_mm) / 10 - 10);
      }
    }
  });
});
