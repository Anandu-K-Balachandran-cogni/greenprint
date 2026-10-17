// Deterministic furniture placement for a room: pieces go against walls (back to the wall,
// facing into the room) or relative to an anchor (a coffee table in front of the sofa), and every
// placement is checked — inside the room, clear of other furniture (by height band too), clear of
// door swings and door openings, and tall pieces never in front of a window.
//
// Works on a scratch copy of the floor so each placement sees the ones before it. The theme
// planner (themes.ts) and, later, the interior-designer agent both place through here.

import type { Floor, FurnitureItem, Point, Room, Window } from '$lib/models/types';
import { pointInPolygon } from '$lib/utils/hitTesting';
import { getCatalogItem } from '$lib/utils/furnitureCatalog';
import { roomWalls, roomSpan, unitNormal, footprint, overlap, itemsClash, heightBand, LAYER_ITEMS, type RoomWall } from './roomDesign';

export type RoomKind = 'living' | 'dining' | 'master' | 'bedroom' | 'kids' | 'kitchen' | 'bathroom' | 'study' | 'circulation' | 'utility';

/** A room's side of one wall: the stretch it owns, its inward normal and the openings on it. */
export interface Face {
  wallId: string; from: number; to: number; start: Point; d: Point; nIn: Point; half: number;
  doors: [number, number][];
  windows: { a: number; b: number; sill: number; top: number }[];
}

export interface RoomCtx {
  work: Floor; room: Room; polygon: Point[]; walls: RoomWall[]; faces: Face[]; kind: RoomKind; ceiling: number;
  doorZones: Point[][];
}

let seq = 0;
const nextId = () => `draft-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const deg = (r: number) => Math.round(((r * 180) / Math.PI + 360) % 360);
/** Rotation (degrees) that turns an item's front (+y at 0°) to face direction n. */
export const facing = (n: Point) => deg(Math.atan2(-n.x, n.y));
const add = (p: Point, q: Point, k = 1): Point => ({ x: p.x + q.x * k, y: p.y + q.y * k });

export function size(catalogId: string) {
  const c = getCatalogItem(catalogId);
  return { w: c?.width ?? 50, d: c?.depth ?? 50, h: c?.height ?? 50 };
}

export function polygonArea(poly: Point[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) { const p = poly[i], q = poly[(i + 1) % poly.length]; a += p.x * q.y - q.x * p.y; }
  return Math.abs(a) / 2;
}

/** Area-weighted centroid, or the vertex average when that falls outside (L-shapes). */
export function centroid(poly: Point[]): Point | null {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i], q = poly[(i + 1) % poly.length], k = p.x * q.y - q.x * p.y;
    a += k; cx += (p.x + q.x) * k; cy += (p.y + q.y) * k;
  }
  const c = a ? { x: cx / (3 * a), y: cy / (3 * a) } : null;
  if (c && pointInPolygon(c, poly)) return c;
  const avg = { x: poly.reduce((s, p) => s + p.x, 0) / poly.length, y: poly.reduce((s, p) => s + p.y, 0) / poly.length };
  return pointInPolygon(avg, poly) ? avg : null;
}

export function roomContext(work: Floor, room: Room, polygon: Point[], polygons: Map<string, Point[]>, kind: RoomKind, ceiling: number): RoomCtx {
  const walls = roomWalls(work, room, polygons);
  const faces: Face[] = [];
  for (const rw of walls) {
    const w = work.walls.find(x => x.id === rw.wallId);
    if (!w || w.curvePoint) continue;
    const span = roomSpan(w, polygon);
    if (!span) continue;
    const { d, n, len } = unitNormal(w);
    const s = rw.side === 'interior' ? 1 : -1;
    faces.push({
      wallId: w.id, from: span[0], to: span[1], start: w.start, d, nIn: { x: n.x * s, y: n.y * s }, half: w.thickness / 2,
      doors: work.doors.filter(o => o.wallId === w.id).map(o => [o.position * len - o.width / 2, o.position * len + o.width / 2]),
      windows: (work.windows as Window[]).filter(o => o.wallId === w.id).map(o => ({
        a: o.position * len - o.width / 2, b: o.position * len + o.width / 2, sill: o.sillHeight ?? 90, top: (o.sillHeight ?? 90) + o.height })),
    });
  }
  // Swing zones of doors that open into this room (a square of the door's width in front of it).
  const doorZones: Point[][] = [];
  for (const door of work.doors) {
    const w = work.walls.find(x => x.id === door.wallId);
    if (!w || door.type === 'garage') continue;
    const { d, n, len } = unitNormal(w);
    const at = { x: w.start.x + d.x * len * door.position, y: w.start.y + d.y * len * door.position };
    for (const s of [1, -1]) {
      if (!pointInPolygon({ x: at.x + n.x * 30 * s, y: at.y + n.y * 30 * s }, polygon)) continue;
      // Swing doors keep their leaf's arc clear; openings and sliding doors keep a 70 cm walkway.
      const leaf = door.type === 'double' || door.type === 'french' ? door.width / 2 : door.width;
      const half = door.width / 2 + 5, depth = ['single', 'double', 'french'].includes(door.type) ? Math.max(leaf, 70) : 70;
      doorZones.push([[-half, 0], [half, 0], [half, depth], [-half, depth]].map(([u, v]) => ({ x: at.x + d.x * u + n.x * v * s, y: at.y + d.y * u + n.y * v * s })));
    }
  }
  return { work, room, polygon, walls, faces, kind, ceiling, doorZones };
}

export const faceLength = (f: Face) => f.to - f.from;
export const facePoint = (f: Face, u: number, out: number): Point => add(add(f.start, f.d, u), f.nIn, out);
/** Position along a face of a point (its projection). */
export const faceU = (f: Face, p: Point) => (p.x - f.start.x) * f.d.x + (p.y - f.start.y) * f.d.y;

/** Room items that are real obstacles. */
function obstacles(ctx: RoomCtx): FurnitureItem[] {
  return ctx.work.furniture.filter(f => !LAYER_ITEMS.has(f.catalogId));
}

export interface Draft { catalogId: string; position: Point; rotation: number; width?: number; depth?: number; height?: number; elevation?: number; color?: string }

function asItem(d: Draft, id = 'probe'): FurnitureItem {
  return { id, catalogId: d.catalogId, position: d.position, rotation: d.rotation, scale: { x: 1, y: 1, z: 1 },
    ...(d.width ? { width: d.width } : {}), ...(d.depth ? { depth: d.depth } : {}), ...(d.height ? { height: d.height } : {}),
    ...(d.elevation !== undefined ? { elevation: d.elevation } : {}), ...(d.color ? { color: d.color } : {}) };
}

/** Whether a draft fits: inside the room, no clash, clear of door swings. */
export function fits(ctx: RoomCtx, draft: Draft): boolean {
  const item = asItem(draft);
  const corners = footprint(item);
  const c = item.position;
  // Pull corners 1 cm towards the centre so pieces flush against a wall still count as inside.
  if (!corners.every(p => { const v = { x: c.x - p.x, y: c.y - p.y }, l = Math.hypot(v.x, v.y) || 1; return pointInPolygon({ x: p.x + v.x / l, y: p.y + v.y / l }, ctx.polygon); })) return false;
  if (!LAYER_ITEMS.has(item.catalogId)) {
    if (obstacles(ctx).some(o => itemsClash(o, item))) return false;
    if (heightBand(item)[0] < 200 && ctx.doorZones.some(z => overlap(corners, z))) return false;
  }
  return true;
}

/** Whether an interval along a face [a, b] at height band [lo, hi] is clear of its openings. */
function clearOfOpenings(f: Face, a: number, b: number, lo: number, hi: number): boolean {
  if (lo < 210 && f.doors.some(([x, y]) => a < y + 10 && b > x - 10)) return false;
  return !f.windows.some(w => a < w.b + 5 && b > w.a - 5 && lo < w.top && hi > w.sill - 2);
}

export interface FaceOpts { near?: number; gap?: number; corner?: boolean; elevation?: number; depthOut?: number }

/** Place a piece with its back to a face; tries positions from `near` (or the middle) outwards. */
export function placeOnFace(ctx: RoomCtx, catalogId: string, f: Face, opts: FaceOpts = {}): Draft | null {
  const { w, d, h } = size(catalogId);
  const lo = opts.elevation ?? getCatalogItem(catalogId)?.mount ?? 0;
  const min = f.from + w / 2 + 3, max = f.to - w / 2 - 3;
  if (max < min) return null;
  const mid = Math.min(max, Math.max(min, opts.near ?? (f.from + f.to) / 2));
  const us = opts.corner ? [min, max] : [mid];
  if (!opts.corner) for (let k = 15; mid - k >= min || mid + k <= max; k += 15) {
    if (mid - k >= min) us.push(mid - k);
    if (mid + k <= max) us.push(mid + k);
  }
  for (const u of us) {
    if (!clearOfOpenings(f, u - w / 2, u + w / 2, lo, lo + h)) continue;
    const draft: Draft = {
      catalogId, position: facePoint(f, u, f.half + (opts.depthOut ?? d) / 2 + (opts.gap ?? 2)), rotation: facing(f.nIn),
      ...(opts.elevation !== undefined ? { elevation: opts.elevation } : {}),
    };
    if (fits(ctx, draft)) return draft;
  }
  return null;
}

/** Commit a draft onto the scratch floor (so later placements avoid it); returns it with its id. */
export function commit(ctx: RoomCtx, draft: Draft): FurnitureItem {
  const item = asItem(draft, nextId());
  ctx.work.furniture.push(item);
  return item;
}

/** Faces ordered for a large piece: no door on it, then fewest windows, then longest. */
export function solidFaces(ctx: RoomCtx, except: string[] = []): Face[] {
  return ctx.faces.filter(f => !except.includes(f.wallId))
    .sort((a, b) => (a.doors.length - b.doors.length) || (a.windows.length - b.windows.length) || (faceLength(b) - faceLength(a)));
}

/** First catalogue choice that fits against any of the faces. */
export function placeFirst(ctx: RoomCtx, choices: string[], faces: Face[], opts: FaceOpts = {}): { item: FurnitureItem; face: Face } | null {
  for (const id of choices) for (const f of faces) {
    const d = placeOnFace(ctx, id, f, opts);
    if (d) return { item: commit(ctx, d), face: f };
  }
  return null;
}

/** Place relative to an anchor item: offset in the anchor's frame (right = +x, front = +y). */
export function placeRelative(ctx: RoomCtx, catalogId: string, anchor: FurnitureItem, right: number, front: number, turn = 0, elevation?: number): FurnitureItem | null {
  const a = (anchor.rotation * Math.PI) / 180;
  const r = { x: Math.cos(a), y: Math.sin(a) }, fw = { x: -Math.sin(a), y: Math.cos(a) };
  const draft: Draft = { catalogId, position: add(add(anchor.position, r, right), fw, front), rotation: (anchor.rotation + turn + 360) % 360,
    ...(elevation !== undefined ? { elevation } : {}) };
  return fits(ctx, draft) ? commit(ctx, draft) : null;
}

const frontOf = (item: FurnitureItem): Point => { const a = (item.rotation * Math.PI) / 180; return { x: -Math.sin(a), y: Math.cos(a) }; };

// --- Recipes ------------------------------------------------------------------------------

export interface Pieces { sofa: string; coffeeTable: string; tv: string; chair: string; bed: string; wardrobe: string; diningTable: string; diningChair: string }

export interface Furnished { anchor?: FurnitureItem; anchorFace?: Face; table?: FurnitureItem }

/** Furnish an empty room of the given kind; returns the anchor piece (sofa, bed, table…). */
export function furnishRoom(ctx: RoomCtx, p: Pieces): Furnished {
  const area = polygonArea(ctx.polygon) / 10000;
  switch (ctx.kind) {
    case 'living': {
      const sofa = placeFirst(ctx, area > 22 ? ['sofa_corner', p.sofa, 'sofa', 'loveseat'] : [p.sofa, 'sofa', 'loveseat'], solidFaces(ctx));
      if (!sofa) return {};
      const s = size(sofa.item.catalogId), ct = size(p.coffeeTable);
      const table = [40, 32, 50].map(gap => placeRelative(ctx, p.coffeeTable, sofa.item, 0, s.d / 2 + gap + ct.d / 2)).find(Boolean)
        ?? placeRelative(ctx, 'coffee_table_glass', sofa.item, 0, s.d / 2 + 35 + size('coffee_table_glass').d / 2)
        ?? placeRelative(ctx, 'side_table', sofa.item, 0, s.d / 2 + 40 + 25);
      // TV on the wall facing the sofa, opposite it.
      const fw = frontOf(sofa.item);
      const opposite = ctx.faces.filter(f => f.nIn.x * fw.x + f.nIn.y * fw.y < -0.95);
      for (const f of opposite) {
        const unit = placeOnFace(ctx, p.tv, f, { near: faceU(f, sofa.item.position) }) ?? placeOnFace(ctx, 'tv_stand', f, { near: faceU(f, sofa.item.position) });
        if (!unit) continue;
        const u = commit(ctx, unit);
        placeRelative(ctx, 'television', u, 0, -size(u.catalogId).d / 2 + 8, 0, size(u.catalogId).h);
        break;
      }
      if (table) {
        const cw = size(p.chair);
        const off = size(table.catalogId).w / 2 + 35 + cw.w / 2;
        // Armchair beside the coffee table, turned towards it.
        placeRelative(ctx, p.chair, table, off, 0, 90) ?? placeRelative(ctx, p.chair, table, -off, 0, -90);
        if (area > 18) placeRelative(ctx, p.chair, table, -off, 0, -90);
      }
      placeRelative(ctx, 'floor_lamp', sofa.item, size(sofa.item.catalogId).w / 2 + 25, -s.d / 2 + 22);
      return { anchor: sofa.item, anchorFace: sofa.face, table: table ?? undefined };
    }
    case 'master': case 'bedroom': case 'kids': {
      const beds = ctx.kind === 'kids' ? ['bed_twin', 'bunk_bed'] : area >= 10 || ctx.kind === 'master' ? [p.bed, 'bed_queen', 'bed_twin'] : ['bed_twin', p.bed];
      const bed = placeFirst(ctx, beds, solidFaces(ctx));
      if (!bed) return {};
      const b = size(bed.item.catalogId), ns = size('nightstand');
      const u = faceU(bed.face, bed.item.position);
      for (const side of [-1, 1]) {
        const d = placeOnFace(ctx, 'nightstand', bed.face, { near: u + side * (b.w / 2 + ns.w / 2 + 4) });
        if (d && Math.abs(faceU(bed.face, d.position) - (u + side * (b.w / 2 + ns.w / 2 + 4))) < 1) commit(ctx, d);
      }
      placeFirst(ctx, [p.wardrobe, 'wardrobe', 'dresser'], solidFaces(ctx, [bed.face.wallId]));
      if (area > 14 && ctx.kind === 'master') placeFirst(ctx, ['dresser'], solidFaces(ctx, [bed.face.wallId]));
      if (ctx.kind === 'kids' && area > 9) {
        const desk = placeFirst(ctx, ['desk'], solidFaces(ctx, [bed.face.wallId]));
        if (desk) placeRelative(ctx, 'office_chair', desk.item, 0, size('desk').d / 2 + 15, 180);
      }
      return { anchor: bed.item, anchorFace: bed.face };
    }
    case 'dining': {
      const c = centroid(ctx.polygon);
      if (!c) return {};
      const xs = ctx.polygon.map(q => q.x), ys = ctx.polygon.map(q => q.y);
      const turn = Math.max(...ys) - Math.min(...ys) > Math.max(...xs) - Math.min(...xs) ? 90 : 0;
      const along = turn ? { x: 0, y: 1 } : { x: 1, y: 0 };
      const spots = [0, -30, 30, -60, 60].map(k => add(c, along, k));
      for (const [id, at] of [p.diningTable, 'dining_table', 'dining_table_round'].flatMap(id => spots.map(at => [id, at] as const))) {
        const draft: Draft = { catalogId: id, position: at, rotation: turn };
        if (!fits(ctx, draft)) continue;
        const mark = ctx.work.furniture.length;
        const table = commit(ctx, draft);
        const t = size(id), ch = size(p.diningChair);
        const seats: [number, number, number][] = id === 'dining_table_round'
          ? [[0, t.d / 2 + ch.d / 2 + 2, 180], [0, -(t.d / 2 + ch.d / 2 + 2), 0], [t.w / 2 + ch.d / 2 + 2, 0, 90], [-(t.w / 2 + ch.d / 2 + 2), 0, -90]]
          : [-1, 1].flatMap(x => [-1, 1].map(y => [x * t.w / 4, y * (t.d / 2 + ch.d / 2 + 2), y > 0 ? 180 : 0] as [number, number, number]))
            .concat(t.w >= 150 ? [[t.w / 2 + ch.d / 2 + 2, 0, 90], [-(t.w / 2 + ch.d / 2 + 2), 0, -90]] : []);
        let placed = 0;
        for (const [x, y, rot] of seats) if (placeRelative(ctx, p.diningChair, table, x, y, rot)) placed++;
        if (placed < 2) { ctx.work.furniture.length = mark; continue; }   // too tight: try a smaller table
        return { table };
      }
      return {};
    }
    case 'kitchen': {
      const runs = [['fridge', 'counter', 'stove', 'counter', 'sink_k'], ['counter', 'stove', 'counter', 'sink_k'], ['counter', 'stove', 'sink_k'], ['stove', 'sink_k']];
      const faces = [...ctx.faces].sort((a, b) => faceLength(b) - faceLength(a));
      for (const run of runs) for (const f of faces) {
        const total = run.reduce((s, id) => s + size(id).w, 0);
        if (faceLength(f) < total + 6) continue;
        for (let start = f.from + 3; start + total <= f.to - 3; start += 15) {
          const drafts: Draft[] = [];
          let u = start, ok = true;
          for (const id of run) {
            const w = size(id).w;
            const d = placeOnFace(ctx, id, { ...f, from: u - 3, to: u + w + 3 });
            if (!d) { ok = false; break; }
            drafts.push(d); u += w;
          }
          if (!ok) continue;
          const items = drafts.map(d => commit(ctx, d));
          // Wall cabinets over the counters, a chimney hood over the stove (where no window is in the way).
          for (const it of items) {
            const ufit = faceU(f, it.position);
            if (it.catalogId === 'stove') { const hd = placeOnFace(ctx, 'range_hood', { ...f, from: ufit - 33, to: ufit + 33 }); if (hd) commit(ctx, hd); }
            if (it.catalogId === 'counter') {
              // Wall cabinets side by side along the counter.
              const cw = size('upper_cabinet').w, n = Math.max(1, Math.floor(size('counter').w / cw));
              for (let k = 0; k < n; k++) {
                const c = ufit + (k - (n - 1) / 2) * cw;
                const up = placeOnFace(ctx, 'upper_cabinet', { ...f, from: c - cw / 2 - 3, to: c + cw / 2 + 3 });
                if (up) commit(ctx, up);
              }
            }
          }
          if (!run.includes('fridge')) placeFirst(ctx, ['fridge'], [...solidFaces(ctx, [f.wallId]), f], { corner: true });
          return { anchor: items[0], anchorFace: f };
        }
      }
      return {};
    }
    case 'bathroom': {
      const toilet = placeFirst(ctx, ['toilet'], solidFaces(ctx));
      const vanity = placeFirst(ctx, ['vanity_cabinet', 'sink_b'], solidFaces(ctx));
      if (vanity) { const m = placeOnFace(ctx, 'bathroom_mirror', { ...vanity.face, from: faceU(vanity.face, vanity.item.position) - 33, to: faceU(vanity.face, vanity.item.position) + 33 }); if (m) commit(ctx, m); }
      if (area >= 3.2) placeFirst(ctx, ['shower'], solidFaces(ctx), { corner: true });
      return { anchor: toilet?.item };
    }
    case 'study': {
      const desk = placeFirst(ctx, ['desk'], [...ctx.faces].sort((a, b) => b.windows.length - a.windows.length || faceLength(b) - faceLength(a)));
      if (desk) placeRelative(ctx, 'office_chair', desk.item, 0, size('desk').d / 2 + 15, 180);
      placeFirst(ctx, ['bookshelf'], solidFaces(ctx, desk ? [desk.face.wallId] : []));
      return { anchor: desk?.item, anchorFace: desk?.face };
    }
    case 'circulation': {
      if (area >= 4) placeFirst(ctx, ['bench'], solidFaces(ctx));
      if (/foyer|entr/i.test(ctx.room.name)) placeFirst(ctx, ['coat_rack'], solidFaces(ctx), { corner: true });
      return {};
    }
    case 'utility':
      placeFirst(ctx, ['washer_dryer'], solidFaces(ctx), { corner: true });
      return {};
  }
}

/** A free corner spot for a plant or a lamp (both ends of each face). */
export function placeInCorner(ctx: RoomCtx, catalogId: string): FurnitureItem | null {
  return placeFirst(ctx, [catalogId], solidFaces(ctx), { corner: true })?.item ?? null;
}

/** Furniture already standing in the room (anything but floor layers and fittings). */
export function roomPieces(ctx: RoomCtx): FurnitureItem[] {
  return ctx.work.furniture.filter(f => !LAYER_ITEMS.has(f.catalogId) && pointInPolygon(f.position, ctx.polygon));
}
