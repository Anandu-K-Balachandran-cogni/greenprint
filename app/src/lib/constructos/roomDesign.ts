// Room-level design operations for the editor: which wall faces belong to a room, painting a
// room (walls, accent wall, ceiling), fitting curtains to its windows, and clearance warnings.
//
// Pure geometry here; the store calls are in applyRoomPaint/addCurtains so a whole change is one
// undo step. A wall face is "interior" on the wall's left normal (the face the 3D viewer paints
// with interiorColor); a room owns the side of a wall whose offset points fall inside its polygon.

import type { Floor, FurnitureItem, Point, Room, Wall, WallPaint, Window } from '$lib/models/types';
import { pointInPolygon } from '$lib/utils/hitTesting';
import { getCatalogItem } from '$lib/utils/furnitureCatalog';

export type WallSide = 'interior' | 'exterior';
export interface RoomWall { wallId: string; side: WallSide; length: number; sharedWith: string[] }

const SAMPLES = [0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9];
/** Items that never block anything: floor layers, window dressings, wall and ceiling fittings. */
export const LAYER_ITEMS = new Set(['rug', 'round_rug', 'runner_rug', 'curtain', 'sheer_curtain', 'roller_blind', 'roman_blind',
  'wall_art', 'mirror', 'clock', 'cushion', 'ceiling_light', 'chandelier', 'recessed_light', 'pendant_light', 'wall_sconce',
  'hanging_plant', 'ceiling_fan']);

export function unitNormal(w: Wall): { d: Point; n: Point; len: number } {
  const dx = w.end.x - w.start.x, dy = w.end.y - w.start.y;
  const len = Math.hypot(dx, dy) || 1;
  // Left normal of start→end; in the 3D viewer this is the box's +z face (interior material).
  return { d: { x: dx / len, y: dy / len }, n: { x: -dy / len, y: dx / len }, len };
}

/** Sides of walls facing a polygon: the side where most offset samples land inside it. */
export function facingSides(walls: Wall[], polygon: Point[]): Map<string, WallSide> {
  const out = new Map<string, WallSide>();
  for (const w of walls) {
    const { d, n, len } = unitNormal(w);
    const off = w.thickness / 2 + 8;
    let left = 0, right = 0;
    // Sample every 15 cm: a small room on a long wall (an en-suite on a 12 m facade) must still count.
    const n_ = Math.max(SAMPLES.length, Math.ceil(len / 15));
    for (let i = 0; i < n_; i++) {
      const t = (i + 0.5) / n_;
      const p = { x: w.start.x + d.x * len * t, y: w.start.y + d.y * len * t };
      if (pointInPolygon({ x: p.x + n.x * off, y: p.y + n.y * off }, polygon)) left++;
      if (pointInPolygon({ x: p.x - n.x * off, y: p.y - n.y * off }, polygon)) right++;
    }
    if (left || right) out.set(w.id, left >= right ? 'interior' : 'exterior');
  }
  return out;
}

/** The wall faces that bound a room, and which other rooms share each face. */
export function roomWalls(floor: Floor, room: Room, polygons: Map<string, Point[]>): RoomWall[] {
  const poly = polygons.get(room.id);
  if (!poly) return [];
  const mine = facingSides(floor.walls, poly);
  const others = [...polygons].filter(([id]) => id !== room.id).map(([id, p]) => ({ id, sides: facingSides(floor.walls, p) }));
  const names = new Map(floor.rooms.map(r => [r.id, r.name]));
  return [...mine].map(([wallId, side]) => {
    const w = floor.walls.find(x => x.id === wallId)!;
    return {
      wallId, side, length: Math.round(Math.hypot(w.end.x - w.start.x, w.end.y - w.start.y)),
      sharedWith: others.filter(o => o.sides.get(wallId) === side).map(o => names.get(o.id) ?? o.id),
    };
  });
}

/** The stretch of a wall along which a room's boundary runs: [from, to] in cm from the wall start. */
export function roomSpan(w: Wall, polygon: Point[]): [number, number] | null {
  const { d, n, len } = unitNormal(w);
  const tol = w.thickness / 2 + 5;
  let from = Infinity, to = -Infinity;
  for (let i = 0; i < polygon.length; i++) {
    const p = polygon[i], q = polygon[(i + 1) % polygon.length];
    const off = (pt: Point) => (pt.x - w.start.x) * n.x + (pt.y - w.start.y) * n.y;
    const along = (pt: Point) => (pt.x - w.start.x) * d.x + (pt.y - w.start.y) * d.y;
    if (Math.abs(off(p)) > tol || Math.abs(off(q)) > tol) continue;  // this edge does not run along the wall
    from = Math.min(from, along(p), along(q));
    to = Math.max(to, along(p), along(q));
  }
  from = Math.max(0, from); to = Math.min(len, to);
  return to - from > 5 ? [Math.round(from), Math.round(to)] : null;
}

/** Paint spans for a room: its stretch of each facing wall side, replacing earlier paint for that room. */
export function spanUpdates(floor: Floor, walls: RoomWall[], polygon: Point[], roomId: string, color: string,
                            accent?: { wallId: string; color: string }): { wallId: string; patch: Partial<Wall> }[] {
  const out: { wallId: string; patch: Partial<Wall> }[] = [];
  for (const rw of walls) {
    const w = floor.walls.find(x => x.id === rw.wallId);
    if (!w || w.curvePoint) continue;
    const span = roomSpan(w, polygon);
    if (!span) continue;
    const [from, to] = span;
    const c = accent && accent.wallId === rw.wallId ? accent.color : color;
    // Drop this room's earlier paint, and any paint wholly inside the new span on the same side.
    const kept = (w.paints ?? []).filter(p => !(p.side === rw.side && (p.roomId === roomId || (p.from >= from - 1 && p.to <= to + 1))));
    const paints: WallPaint[] = [...kept, { side: rw.side, from, to, color: c, roomId }];
    out.push({ wallId: w.id, patch: { paints } });
  }
  return out;
}

/** Wall updates that paint whole faces (every facing side), with an optional accent wall. */
export function paintUpdates(walls: RoomWall[], color: string, accent?: { wallId: string; color: string }): { wallId: string; patch: Partial<Wall> }[] {
  return walls.map(w => {
    const c = accent && accent.wallId === w.wallId ? accent.color : color;
    return { wallId: w.wallId, patch: w.side === 'interior' ? { interiorColor: c, interiorTexture: undefined } : { exteriorColor: c, exteriorTexture: undefined } };
  });
}

export interface CurtainPlan { windowId: string; position: Point; rotation: number; width: number; height: number; depth: number }

/** Floor-to-rod curtains for each window that opens into the room, hung on the room side. */
export function curtainPlans(floor: Floor, walls: RoomWall[], polygon: Point[], ceilingHeight = 270): CurtainPlan[] {
  const plans: CurtainPlan[] = [];
  const byId = new Map(floor.walls.map(w => [w.id, w]));
  const sides = new Map(walls.map(w => [w.wallId, w.side]));
  for (const win of floor.windows as Window[]) {
    const w = byId.get(win.wallId);
    const side = sides.get(win.wallId);
    if (!w || !side) continue;
    const { d, n, len } = unitNormal(w);
    const s = side === 'interior' ? 1 : -1;
    const off = w.thickness / 2 + 8;
    const at = { x: w.start.x + d.x * len * win.position, y: w.start.y + d.y * len * win.position };
    const top = (win.sillHeight ?? 90) + win.height;
    // A long wall face can span several rooms: keep only windows whose spot is inside this room.
    if (!pointInPolygon({ x: at.x + n.x * off * s, y: at.y + n.y * off * s }, polygon)) continue;
    plans.push({
      windowId: win.id,
      position: { x: Math.round(at.x + n.x * off * s), y: Math.round(at.y + n.y * off * s) },
      rotation: Math.round(((Math.atan2(d.y, d.x) * 180) / Math.PI + 360) % 360),
      width: Math.round(win.width + 40), depth: 10,
      height: Math.round(Math.min(ceilingHeight - 5, top + 15)),
    });
  }
  return plans;
}

/** Curtains already hanging close to a planned position (so adding twice does nothing). */
export function hasCurtainNear(floor: Floor, p: Point, within = 40): boolean {
  return floor.furniture.some(f => (f.catalogId === 'curtain' || f.catalogId === 'sheer_curtain')
    && Math.hypot(f.position.x - p.x, f.position.y - p.y) <= within);
}

// --- Clearance ------------------------------------------------------------------------------

export function footprint(item: FurnitureItem): Point[] {
  const cat = getCatalogItem(item.catalogId);
  const w = (item.width ?? cat?.width ?? 50) * Math.abs(item.scale?.x ?? 1);
  const h = (item.depth ?? cat?.depth ?? 50) * Math.abs(item.scale?.y ?? 1);
  const a = (item.rotation * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  return [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]]
    .map(([x, y]) => ({ x: item.position.x + x * c - y * s, y: item.position.y + x * s + y * c }));
}

/** Height band an item occupies above the floor, in cm (wall-mounted items sit above the floor). */
export function heightBand(item: FurnitureItem): [number, number] {
  const cat = getCatalogItem(item.catalogId);
  const bottom = item.elevation ?? cat?.mount ?? 0;
  return [bottom, bottom + (item.height ?? cat?.height ?? 50) * Math.abs(item.scale?.z ?? 1)];
}

/** Two items clash when their footprints and height bands both overlap (a wall cabinet clears a counter). */
export function itemsClash(a: FurnitureItem, b: FurnitureItem): boolean {
  const [a0, a1] = heightBand(a), [b0, b1] = heightBand(b);
  return a0 < b1 - 1 && b0 < a1 - 1 && overlap(footprint(a), footprint(b));
}

export function overlap(a: Point[], b: Point[]): boolean {
  // Separating axis theorem for convex polygons (here: rotated rectangles), with a 1 cm tolerance.
  for (const poly of [a, b]) {
    for (let i = 0; i < poly.length; i++) {
      const p = poly[i], q = poly[(i + 1) % poly.length];
      const ax = { x: q.y - p.y, y: p.x - q.x };
      const proj = (pts: Point[]) => pts.map(v => v.x * ax.x + v.y * ax.y);
      const [pa, pb] = [proj(a), proj(b)];
      const norm = Math.hypot(ax.x, ax.y) || 1;
      if (Math.max(...pa) - 1 * norm <= Math.min(...pb) || Math.max(...pb) - 1 * norm <= Math.min(...pa)) return false;
    }
  }
  return true;
}

export interface ClearanceIssue { kind: 'overlap' | 'door'; items: string[]; message: string }

/** Furniture inside the room that overlaps other furniture or sits in a door's swing. */
export function clearanceIssues(floor: Floor, polygon: Point[]): ClearanceIssue[] {
  const name = (f: FurnitureItem) => getCatalogItem(f.catalogId)?.name ?? f.catalogId;
  const inRoom = floor.furniture.filter(f => pointInPolygon(f.position, polygon) && !LAYER_ITEMS.has(f.catalogId));
  const issues: ClearanceIssue[] = [];
  for (let i = 0; i < inRoom.length; i++) {
    for (let j = i + 1; j < inRoom.length; j++) {
      if (itemsClash(inRoom[i], inRoom[j])) {
        issues.push({ kind: 'overlap', items: [inRoom[i].id, inRoom[j].id], message: `${name(inRoom[i])} overlaps ${name(inRoom[j])}.` });
      }
    }
  }
  const byId = new Map(floor.walls.map(w => [w.id, w]));
  for (const door of floor.doors) {
    const w = byId.get(door.wallId);
    if (!w || door.type === 'opening' || door.type === 'garage') continue;
    const { d, n, len } = unitNormal(w);
    const at = { x: w.start.x + d.x * len * door.position, y: w.start.y + d.y * len * door.position };
    for (const s of [1, -1]) {
      // The swing zone on each side: a square of the door's width in front of the opening.
      const half = door.width / 2, depth = door.type === 'double' || door.type === 'french' ? door.width / 2 : door.type === 'single' ? door.width : 60;
      const zone = [[-half, 0], [half, 0], [half, depth], [-half, depth]].map(([u, v]) => ({
        x: at.x + d.x * u + n.x * v * s, y: at.y + d.y * u + n.y * v * s }));
      if (!pointInPolygon({ x: at.x + n.x * 30 * s, y: at.y + n.y * 30 * s }, polygon)) continue;
      for (const f of inRoom) {
        if (heightBand(f)[0] < 200 && overlap(footprint(f), zone)) issues.push({ kind: 'door', items: [f.id], message: `${name(f)} blocks a door.` });
      }
    }
  }
  return issues;
}
