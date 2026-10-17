// Compile a solved allocation into editable architectural objects.
//
// The solver returns rectangles on a grid; this turns them into the editor's
// canonical model: deduplicated shared walls on rectangle edges, hosted doors
// and windows, rooms bound to their walls, and stairs with floor openings.
// The output is deterministic: the same allocation always yields the same IDs.

import type { Door, Floor, Project, Room, Stair, Wall, Window } from '$lib/models/types';
import { detectRooms, getRoomPolygon, roomCentroid } from '$lib/utils/roomDetection';
import type { Allocation, PlacedPassage, PlacedSpace } from './engine';

export const COMPILER_VERSION = 'constructos.compiler.v1';

export const EXTERIOR_WALL_CM = 23;
export const INTERIOR_WALL_CM = 11.5;
export const PARAPET_WALL_CM = 15;
export const WALL_HEIGHT_CM = 300;
export const PARAPET_HEIGHT_CM = 100;
export const FLOOR_TO_FLOOR_CM = 320;

const OPEN_TYPES = new Set(['terrace']);
const NO_WINDOW_TYPES = new Set(['garage', 'stair', 'hall', 'corridor', 'terrace', 'foyer', 'store', 'prayer']);

export interface ConstructosLink {
  engineProjectId: string;
  layoutRunId: string;
  candidate: number;
  briefVersion: number;
  briefHash: string;
  compiler: string;
  compiledAt: string;
  solverStatus: string;
}

export type CompiledProject = Project & { constructos?: ConstructosLink };

/** Editor-frame rectangle in integer millimetres; y grows from the rear (top) to the road (bottom). */
interface Rect { space: PlacedSpace; x0: number; x1: number; y0: number; y1: number }
type Kind = 'exterior' | 'interior' | 'parapet' | 'none';
interface Segment { orient: 'h' | 'v'; line: number; a: number; b: number; kind: Kind }

export class CompileError extends Error {}

function toRect(s: PlacedSpace, plotDepth: number): Rect {
  return { space: s, x0: s.x_mm, x1: s.x_mm + s.w_mm, y0: plotDepth - s.y_mm - s.h_mm, y1: plotDepth - s.y_mm };
}

const isOpen = (r?: Rect) => !!r && OPEN_TYPES.has(r.space.type);

function classify(lo?: Rect, hi?: Rect): Kind {
  if (!lo && !hi) return 'none';
  const roofedLo = lo && !isOpen(lo), roofedHi = hi && !isOpen(hi);
  if (roofedLo && roofedHi) return 'interior';
  if (roofedLo || roofedHi) return 'exterior';
  return lo && hi ? 'none' : 'parapet'; // terrace/terrace or terrace/outside
}

/** Split every rectangle edge into atomic pieces and classify each by what lies on either side. */
function segments(rects: Rect[]): Segment[] {
  const out: Segment[] = [];
  for (const orient of ['h', 'v'] as const) {
    const lines = new Set<number>();
    for (const r of rects) for (const v of orient === 'h' ? [r.y0, r.y1] : [r.x0, r.x1]) lines.add(v);
    for (const line of [...lines].sort((p, q) => p - q)) {
      const lo = rects.filter(r => (orient === 'h' ? r.y1 : r.x1) === line); // rect ends at the line
      const hi = rects.filter(r => (orient === 'h' ? r.y0 : r.x0) === line); // rect starts at the line
      const span = (r: Rect): [number, number] => (orient === 'h' ? [r.x0, r.x1] : [r.y0, r.y1]);
      const cuts = [...new Set([...lo, ...hi].flatMap(span))].sort((p, q) => p - q);
      let current: Segment | null = null;
      for (let i = 0; i + 1 < cuts.length; i++) {
        const [a, b] = [cuts[i], cuts[i + 1]];
        const mid = (a + b) / 2;
        const covers = (r: Rect) => { const [s, e] = span(r); return s <= mid && mid <= e; };
        const kind = classify(lo.find(covers), hi.find(covers));
        if (kind === 'none') { current = null; continue; }
        if (current && current.kind === kind && current.b === a) current.b = b;
        else { current = { orient, line, a, b, kind }; out.push(current); }
      }
    }
  }
  return out;
}

function wallFromSegment(seg: Segment, id: string): Wall {
  const thickness = seg.kind === 'exterior' ? EXTERIOR_WALL_CM : seg.kind === 'interior' ? INTERIOR_WALL_CM : PARAPET_WALL_CM;
  const height = seg.kind === 'parapet' ? PARAPET_HEIGHT_CM : WALL_HEIGHT_CM;
  const p = (along: number) => (seg.orient === 'h' ? { x: along / 10, y: seg.line / 10 } : { x: seg.line / 10, y: along / 10 });
  return {
    id, start: p(seg.a), end: p(seg.b), thickness, height,
    color: seg.kind === 'exterior' ? '#f3efe6' : '#ffffff',
  };
}

/** The wall lying on an axis line that contains `along`, and the 0-1 position on it. */
function host(walls: { seg: Segment; wall: Wall }[], orient: 'h' | 'v', line: number, along: number) {
  const found = walls.find(w => w.seg.orient === orient && w.seg.line === line && w.seg.a < along && along < w.seg.b);
  if (!found) return null;
  return { wall: found.wall, position: (along - found.seg.a) / (found.seg.b - found.seg.a), length: found.seg.b - found.seg.a };
}

interface Edge { orient: 'h' | 'v'; line: number; a: number; b: number }

function sharedEdge(p: Rect, q: Rect): Edge | null {
  const overlap = (a0: number, a1: number, b0: number, b1: number): [number, number] | null => {
    const s = Math.max(a0, b0), e = Math.min(a1, b1);
    return e > s ? [s, e] : null;
  };
  if (p.x1 === q.x0 || q.x1 === p.x0) {
    const o = overlap(p.y0, p.y1, q.y0, q.y1);
    if (o) return { orient: 'v', line: p.x1 === q.x0 ? p.x1 : p.x0, a: o[0], b: o[1] };
  }
  if (p.y1 === q.y0 || q.y1 === p.y0) {
    const o = overlap(p.x0, p.x1, q.x0, q.x1);
    if (o) return { orient: 'h', line: p.y1 === q.y0 ? p.y1 : p.y0, a: o[0], b: o[1] };
  }
  return null;
}

/** Road-side (bottom) edge of a rectangle. */
const frontEdge = (r: Rect): Edge => ({ orient: 'h', line: r.y1, a: r.x0, b: r.x1 });

function sideEdge(r: Rect, side: PlacedSpace['exterior_sides'][number]): Edge {
  switch (side) {
    case 'front': return frontEdge(r);
    case 'rear': return { orient: 'h', line: r.y0, a: r.x0, b: r.x1 };
    case 'left': return { orient: 'v', line: r.x0, a: r.y0, b: r.y1 };
    case 'right': return { orient: 'v', line: r.x1, a: r.y0, b: r.y1 };
  }
}

/**
 * The editor swings an unflipped door towards the wall's left-hand normal
 * (-uy, ux): +y for our horizontal walls (drawn left to right) and -x for vertical
 * walls (drawn top to bottom). Doors open into the space they serve.
 */
function opensTowardNormal(edge: Edge, into: Rect): boolean {
  return edge.orient === 'h' ? (into.y0 + into.y1) / 2 > edge.line : (into.x0 + into.x1) / 2 < edge.line;
}

function doorSpec(p: PlacedPassage, child: PlacedSpace, overlap: number): { type: Door['type']; width: number } {
  switch (p.kind) {
    case 'entrance': return { type: 'single', width: 1000 };
    case 'garage': return { type: 'garage', width: Math.max(2400, overlap - 600) };
    case 'opening': return { type: 'opening', width: Math.min(overlap - 300, 2400) };
    case 'stair': return { type: 'opening', width: Math.min(overlap - 300, 1200) };
    default:
      if (child.type === 'terrace') return { type: 'sliding', width: Math.min(1800, overlap - 300) };
      return { type: 'single', width: child.type === 'bathroom' || child.type === 'toilet' ? 750 : 900 };
  }
}

function windowSpec(type: string): { width: number; height: number; sill: number } {
  switch (type) {
    case 'living': case 'family': return { width: 1800, height: 1350, sill: 750 };
    case 'master_bedroom': case 'bedroom': case 'guest_room': case 'dining': return { width: 1500, height: 1350, sill: 900 };
    case 'bathroom': case 'toilet': case 'laundry': case 'utility': return { width: 600, height: 600, sill: 1500 };
    default: return { width: 1200, height: 1200, sill: 900 };
  }
}

function floorTexture(type: string): string {
  if (['bathroom', 'toilet', 'utility', 'laundry'].includes(type)) return 'ceramic-white';
  if (type === 'kitchen') return 'porcelain';
  if (type === 'garage' || type === 'terrace') return 'concrete';
  if (['foyer', 'hall', 'corridor', 'stair', 'living', 'dining'].includes(type)) return 'marble-white';
  return 'light-oak';
}

function roomCategory(type: string): Room['roomType'] {
  if (type === 'garage') return 'garage';
  if (type === 'terrace' || type === 'balcony') return 'outdoor';
  if (['utility', 'laundry', 'store'].includes(type)) return 'utility';
  return 'indoor';
}

// The editor's own room-type vocabulary (see utils/itemDetails ROOM_TYPES).
const EDITOR_ROOM_TYPE: Record<string, string> = {
  living: 'livingRoom', family: 'livingRoom', master_bedroom: 'bedroom', bedroom: 'bedroom', guest_room: 'bedroom',
  kitchen: 'kitchen', bathroom: 'bathroom', toilet: 'bathroom', dining: 'diningRoom', laundry: 'laundryRoom',
  utility: 'laundryRoom', study: 'office', home_office: 'office', hall: 'hallway', corridor: 'hallway',
  stair: 'hallway', garage: 'garage', store: 'closet', foyer: 'entryway',
};

function floorName(f: number) { return f === 0 ? 'Ground floor' : f === 1 ? 'First floor' : `Floor ${f}`; }

function compileFloor(f: number, allocation: Allocation, problems: string[]): Floor {
  const plotDepth = allocation.plot.depth_mm;
  const rects = allocation.spaces.filter(s => s.floor === f).map(s => toRect(s, plotDepth));
  const byId = new Map(rects.map(r => [r.space.id, r]));
  const segs = segments(rects);
  const walls = segs.map((seg, i) => ({ seg, wall: wallFromSegment(seg, `f${f}-wall-${i + 1}`) }));
  const doors: Door[] = [];
  const windows: Window[] = [];
  // Occupied stretches of wall lines (absolute mm, 100 mm clearance each side), with the door that holds each.
  type Stretch = { orient: Edge['orient']; line: number; a: number; b: number; door?: Door };
  const used: Stretch[] = [];

  const place = (edge: Edge, width: number, label: string, centre = (edge.a + edge.b) / 2) => {
    const h = host(walls, edge.orient, edge.line, centre);
    if (!h) { problems.push(`No wall hosts the ${label}.`); return null; }
    const stretch: Stretch = { orient: edge.orient, line: edge.line, a: centre - width / 2 - 100, b: centre + width / 2 + 100 };
    const clash = used.some(u => u.orient === edge.orient && u.line === edge.line && centre - width / 2 < u.b && u.a < centre + width / 2);
    if (stretch.a < edge.a || stretch.b > edge.b || clash) { problems.push(`The ${label} does not fit its wall.`); return null; }
    used.push(stretch);
    return { ...h, stretch };
  };

  for (const [i, p] of allocation.passages.filter(x => x.floor === f).entries()) {
    const child = byId.get(p.b);
    if (!child) continue;
    const parent = p.a === 'exterior' ? null : byId.get(p.a);
    const edge = parent ? sharedEdge(parent, child) : frontEdge(child);
    if (!edge) { problems.push(`${child.space.name} has no wall shared with its access.`); continue; }
    const spec = doorSpec(p, child.space, edge.b - edge.a);
    const h = place(edge, spec.width, `door to ${child.space.name}`);
    if (!h) continue;
    h.stretch.door = {
      id: `f${f}-door-${i + 1}`, wallId: h.wall.id, position: h.position, width: spec.width / 10,
      height: spec.type === 'garage' ? 240 : 210, type: spec.type, swingDirection: 'left',
      flipSide: !opensTowardNormal(edge, child),
    };
    doors.push(h.stretch.door);
  }

  let w = 0;
  // A window goes in the middle of an outside wall; with `aside`, in the widest stretch its doors leave free.
  const addWindow = (r: Rect, edge: Edge, aside = false) => {
    const spec = windowSpec(r.space.type);
    const taken = used.filter(u => u.orient === edge.orient && u.line === edge.line && u.a < edge.b && edge.a < u.b).sort((p, q) => p.a - q.a);
    let lo = edge.a + 300, free = { a: 0, b: 0 };
    for (const u of taken) {
      if (u.a - 100 - lo > free.b - free.a) free = { a: lo, b: u.a - 100 };
      lo = Math.max(lo, u.b + 100);
    }
    if (edge.b - 300 - lo > free.b - free.a) free = { a: lo, b: edge.b - 300 };
    const tries = aside ? [{ width: Math.min(spec.width, free.b - free.a), centre: (free.a + free.b) / 2 }]
      : [{ width: Math.min(spec.width, edge.b - edge.a - 600), centre: (edge.a + edge.b) / 2 }];
    for (const { width, centre } of tries) {
      if (width < 600) continue;
      const before = problems.length;
      const h = place(edge, width, `window in ${r.space.name}`, centre);
      if (!h) { problems.length = before; continue; }
      windows.push({
        id: `f${f}-window-${++w}`, wallId: h.wall.id, position: h.position, width: width / 10,
        height: spec.height / 10, sillHeight: spec.sill / 10, type: 'standard',
      });
      return true;
    }
    return false;
  };

  for (const r of rects) {
    if (NO_WINDOW_TYPES.has(r.space.type)) continue;
    const sides = [...r.space.exterior_sides].map(side => sideEdge(r, side)).sort((p, q) => (q.b - q.a) - (p.b - p.a));
    const wanted = r.space.type === 'living' || r.space.type === 'family' ? 2 : 1;
    let placed = 0;
    for (const edge of sides) {
      if (placed >= wanted) break;
      if (addWindow(r, edge)) placed++; // else try another exterior side
    }
    for (const edge of placed ? [] : sides) if (addWindow(r, edge, true)) { placed++; break; }
    // A door in the middle of a short outside wall leaves no room for a window: slide the door to one end.
    for (const edge of placed ? [] : sides) {
      const held = used.find(u => u.door && u.orient === edge.orient && u.line === edge.line && edge.a <= u.a && u.b <= edge.b);
      if (!held?.door) continue;
      const { a, b } = held, half = (b - a) / 2;
      for (const centre of [edge.a + 200 + half, edge.b - 200 - half]) {
        const h = host(walls, edge.orient, edge.line, centre);
        if (!h || used.some(u => u !== held && u.orient === edge.orient && u.line === edge.line && centre - half < u.b && u.a < centre + half)) continue;
        held.a = centre - half; held.b = centre + half;
        if (addWindow(r, edge, true)) { held.door.wallId = h.wall.id; held.door.position = h.position; placed++; break; }
        held.a = a; held.b = b;
      }
      if (placed) break;
    }
    if (!placed && r.space.exterior_sides.length) problems.push(`No window could be placed in ${r.space.name}.`);
  }

  const wallList = walls.map(x => x.wall);
  const rooms: Room[] = [];
  for (const detected of detectRooms(wallList)) {
    const polygon = getRoomPolygon(detected, wallList);
    if (polygon.length < 3) continue;
    const c = roomCentroid(polygon);
    const owner = rects.find(r => r.x0 < c.x * 10 && c.x * 10 < r.x1 && r.y0 < c.y * 10 && c.y * 10 < r.y1);
    if (!owner || rooms.some(x => x.id === owner.space.id)) continue;
    const s = owner.space;
    rooms.push({
      ...detected, id: s.id, name: s.name, floorTexture: floorTexture(s.type), roomType: roomCategory(s.type),
      ...(f > 0 && s.type === 'stair' ? { floorOpening: true } : {}),
      ...(EDITOR_ROOM_TYPE[s.type] ? { details: { roomType: EDITOR_ROOM_TYPE[s.type] } } : {}),
    });
  }
  for (const r of rects) {
    if (!rooms.some(x => x.id === r.space.id) && !isOpen(r)) problems.push(`${r.space.name} did not form an enclosed room.`);
  }

  // The flight starts on the lowest floor; upper floors keep the core as a slab opening.
  const stairs: Stair[] = rects.filter(r => r.space.type === 'stair' && f === 0).map(r => stairInCore(r));

  return {
    id: `floor-${f}`, name: floorName(f), level: f, elevation: f * FLOOR_TO_FLOOR_CM,
    walls: wallList, rooms, doors, windows, furniture: [], stairs, columns: [],
    guides: [], measurements: [], annotations: [], textAnnotations: [], groups: [],
  };
}

/**
 * A dog-leg (U) stair filling its core with a 10 cm clearance all round. The editor's
 * U-stair centres its two flights on `position` and adds a landing of 0.1 × width
 * beyond one end of its depth, so the depth and position account for that landing.
 */
export function stairInCore(r: { x0: number; x1: number; y0: number; y1: number }): Stair {
  const w = (r.x1 - r.x0) / 10, h = (r.y1 - r.y0) / 10, cx = (r.x0 + r.x1) / 20, cy = (r.y0 + r.y1) / 20;
  const portrait = h >= w;
  const width = (portrait ? w : h) - 20;
  const depth = (portrait ? h : w) - 20 - 0.1 * width;
  return {
    id: 'stair-core', rotation: portrait ? 0 : 90, width, depth, direction: 'up', stairType: 'u-shaped',
    riserCount: Math.round(FLOOR_TO_FLOOR_CM / 17.8),
    position: portrait ? { x: cx, y: cy + 0.05 * width } : { x: cx - 0.05 * width, y: cy },
  };
}

export interface CompileResult { project: CompiledProject; problems: string[] }

/** Compile one candidate. Problems are reported, never silently patched; a candidate with problems is not ready. */
export function compileAllocation(
  allocation: Allocation,
  meta: { projectId: string; name: string; link: Omit<ConstructosLink, 'compiler' | 'compiledAt' | 'solverStatus'> },
  now = new Date(),
): CompileResult {
  if (!allocation.footprint || !allocation.spaces.length) throw new CompileError(allocation.message ?? 'This candidate has no layout.');
  for (const s of allocation.spaces) {
    if (![s.x_mm, s.y_mm, s.w_mm, s.h_mm].every(v => Number.isInteger(v)) || s.w_mm <= 0 || s.h_mm <= 0) {
      throw new CompileError(`Space ${s.id} has invalid geometry.`);
    }
  }
  const problems: string[] = [];
  const storeys = Math.max(...allocation.spaces.map(s => s.floor)) + 1;
  const floors = Array.from({ length: storeys }, (_, f) => compileFloor(f, allocation, problems));
  return {
    problems,
    project: {
      id: meta.projectId, name: meta.name, floors, activeFloorId: floors[0].id,
      createdAt: now, updatedAt: now,
      description: 'Generated by Greenprint from a confirmed brief.',
      constructos: { ...meta.link, compiler: COMPILER_VERSION, compiledAt: now.toISOString(), solverStatus: allocation.status },
    },
  };
}

/** Net clear room size in cm once half of each bounding wall's thickness is removed. */
export function netRoomSize(space: PlacedSpace): { width: number; depth: number } {
  const ext = new Set(space.exterior_sides);
  const half = (side: PlacedSpace['exterior_sides'][number]) => (ext.has(side) ? EXTERIOR_WALL_CM : INTERIOR_WALL_CM) / 2;
  return {
    width: space.w_mm / 10 - half('left') - half('right'),
    depth: space.h_mm / 10 - half('front') - half('rear'),
  };
}
