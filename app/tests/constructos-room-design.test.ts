import { describe, expect, it } from 'vitest';
import type { Floor, FurnitureItem, Point, Room, Wall } from '../src/lib/models/types';
import { clearanceIssues, curtainPlans, facingSides, hasCurtainNear, paintUpdates, roomWalls } from '../src/lib/constructos/roomDesign';

const wall = (id: string, x1: number, y1: number, x2: number, y2: number, thickness = 15): Wall =>
  ({ id, start: { x: x1, y: y1 }, end: { x: x2, y: y2 }, thickness, height: 270, color: '#eee' } as Wall);
const room = (id: string, name: string, walls: string[]): Room => ({ id, name, walls, floorTexture: 'light-oak', area: 0 });
const item = (id: string, catalogId: string, x: number, y: number, rotation = 0, width?: number, depth?: number): FurnitureItem =>
  ({ id, catalogId, position: { x, y }, rotation, scale: { x: 1, y: 1, z: 1 }, width, depth });

// Two rooms side by side: A (0..400) and B (400..700), y 0..300, partition at x=400 drawn top→bottom.
const A: Point[] = [{ x: 0, y: 0 }, { x: 400, y: 0 }, { x: 400, y: 300 }, { x: 0, y: 300 }];
const B: Point[] = [{ x: 400, y: 0 }, { x: 700, y: 0 }, { x: 700, y: 300 }, { x: 400, y: 300 }];
function floor(extra: Partial<Floor> = {}): Floor {
  return {
    id: 'f', name: 'Ground', level: 0,
    walls: [wall('top', 0, 0, 700, 0, 23), wall('right', 700, 0, 700, 300, 23), wall('bottom', 700, 300, 0, 300, 23),
      wall('left', 0, 300, 0, 0, 23), wall('mid', 400, 0, 400, 300)],
    rooms: [room('a', 'Bedroom', ['top', 'mid', 'bottom', 'left']), room('b', 'Study', ['top', 'right', 'bottom', 'mid'])],
    doors: [], windows: [], furniture: [], stairs: [], columns: [],
    ...extra,
  } as unknown as Floor;
}
const polys = new Map([['a', A], ['b', B]]);

describe('room design', () => {
  it('finds the room-facing side of each wall, matching the 3D interior face (left normal)', () => {
    const sides = facingSides(floor().walls, A);
    // "top" runs left→right; its left normal points down (+y), into the rooms → interior.
    expect(sides.get('top')).toBe('interior');
    // "bottom" runs right→left; its left normal points up, into the rooms → interior.
    expect(sides.get('bottom')).toBe('interior');
    // The partition runs top→bottom: its left normal (-dy, dx) points to -x, into room A; B owns the other face.
    expect(sides.get('mid')).toBe('interior');
    expect(facingSides(floor().walls, B).get('mid')).toBe('exterior');
    expect(sides.has('right')).toBe(false);
  });

  it('paints only the room side of each wall, with an accent wall, and reports shared faces', () => {
    const walls = roomWalls(floor(), floor().rooms[0], polys);
    expect(walls.map(w => w.wallId).sort()).toEqual(['bottom', 'left', 'mid', 'top']);
    expect(walls.find(w => w.wallId === 'top')!.sharedWith).toEqual(['Study']);  // one long face spans both rooms
    expect(walls.find(w => w.wallId === 'mid')!.sharedWith).toEqual([]);         // the partition's sides differ
    const patches = new Map(paintUpdates(walls, '#ffffff', { wallId: 'left', color: '#c8643b' }).map(u => [u.wallId, u.patch]));
    expect(patches.get('mid')).toEqual({ interiorColor: '#ffffff', interiorTexture: undefined });
    expect(patches.get('top')).toEqual({ interiorColor: '#ffffff', interiorTexture: undefined });
    expect(patches.get('left')).toEqual({ interiorColor: '#c8643b', interiorTexture: undefined });
  });

  it('fits floor-to-rod curtains to each window on the room side, without duplicates', () => {
    const f = floor({ windows: [{ id: 'w1', wallId: 'top', position: 2 / 7, width: 120, height: 120, sillHeight: 90, type: 'standard' }] } as Partial<Floor>);
    const plans = curtainPlans(f, roomWalls(f, f.rooms[0], polys), A);
    expect(plans).toHaveLength(1);
    const [p] = plans;
    expect(p.width).toBe(160);                    // window + 20 cm each side
    expect(p.height).toBe(225);                   // sill 90 + 120 + 15 cm rod clearance
    expect(p.rotation).toBe(0);                   // parallel to the wall
    expect(p.position.x).toBe(200);
    expect(p.position.y).toBeGreaterThan(0);      // inside the room, not outside the wall
    expect(p.position.y).toBeLessThan(30);
    const withCurtain = { ...f, furniture: [item('c', 'curtain', p.position.x, p.position.y)] } as Floor;
    expect(hasCurtainNear(withCurtain, p.position)).toBe(true);
    // The same long wall bounds the Study, but this window opens into the Bedroom only.
    expect(curtainPlans(f, roomWalls(f, f.rooms[1], polys), B)).toHaveLength(0);
  });

  it('warns about overlapping furniture and a blocked door, ignoring rugs and curtains', () => {
    const f = floor({
      furniture: [item('bed', 'bed', 150, 150, 0, 160, 200), item('desk', 'desk', 230, 150, 0, 120, 60),
        item('rug', 'rug', 150, 150, 0, 200, 140), item('chair', 'armchair', 380, 60, 0, 70, 70)],
      doors: [{ id: 'd', wallId: 'mid', position: 0.2, width: 90, height: 210, type: 'single', swingDirection: 'left', flipSide: false }],
    } as Partial<Floor>);
    const issues = clearanceIssues(f, A);
    expect(issues.filter(i => i.kind === 'overlap').map(i => i.items.sort().join('+'))).toEqual(['bed+desk']);
    expect(issues.filter(i => i.kind === 'door').map(i => i.items[0])).toEqual(['chair']);
    expect(issues.some(i => i.items.includes('rug'))).toBe(false);
  });
});
