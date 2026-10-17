// Green performance of a home from its own model: daylight and cross-ventilation per room, window heat
// gain by compass direction, rainwater harvesting, rooftop solar and indicative embodied carbon, plus
// window fixes that are checked by recomputing. Climate inputs (zone, rainfall, sunshine, tariff) come
// from the engine, which looks them up per city with Gemini grounded in Google Search.
//
// Pure: runs in the editor and, bundled, in the engine for the sustainability advisor agent.

import type { Floor, Point, Window } from '$lib/models/types';
import { resolveRoomGeometry } from '$lib/utils/roomDetection';
import { roomCeilingHeight } from '$lib/utils/wallProfiles';
import { EXTERIOR_WALL_CM } from './compiler';
import { faceLength, polygonArea, roomContext, type Face } from './furnish';
import { roomKind } from './themes';

export type Zone = 'hot_dry' | 'warm_humid' | 'composite' | 'temperate' | 'cold';
export type Compass = 'north' | 'north_east' | 'east' | 'south_east' | 'south' | 'south_west' | 'west' | 'north_west';
export const COMPASS: Compass[] = ['north', 'north_east', 'east', 'south_east', 'south', 'south_west', 'west', 'north_west'];

export interface Climate {
  city: string; zone: Zone; rainfall_mm: number; sun_hours: number; tariff: number; currency: string;
  hemisphere: 'north' | 'south';
  /** Measured yearly yield at the plot (kWh per kWp, after system losses), from Google's Solar API when the plot is pinned and covered. */
  site_yield?: number;
}

/** Window (opening) area as a share of floor area for habitable rooms, by climate — National Building Code
 *  of India guidance on lighting and ventilation of rooms (dry hot 1/10, wet hot 1/6, intermediate 1/8, cold 1/12). */
export const WFR: Record<Zone, number> = { hot_dry: 1 / 10, warm_humid: 1 / 6, composite: 1 / 8, temperate: 1 / 8, cold: 1 / 12 };

const HABITABLE = new Set(['living', 'dining', 'master', 'bedroom', 'kids', 'kitchen', 'study']);
const LITRES_PER_PERSON_DAY = 135;   // BIS domestic water norm
const RUNOFF = 0.8;                  // flat RCC roof
const KWP_PER_M2 = 0.1;              // ~10 m² of shade-free roof per kWp
const PERFORMANCE_RATIO = 0.77;
// Indicative embodied-carbon factors (kg CO2e): fired-clay brick masonry, fly-ash brick and AAC block masonry
// per m³; RCC with OPC and with fly-ash (PPC) cement per m³; reinforcement steel per kg.
export const CARBON = { brick: 430, flyash_brick: 260, aac: 200, rcc_opc: 320, rcc_ppc: 260, steel: 2.4 };

/** Compass direction of a plan vector, given the compass direction the plan's front (+y, the road side) faces. */
export function compassOf(dir: Point, front: Compass): Compass {
  const i = COMPASS.indexOf(front);
  const alpha = (Math.atan2(dir.x, dir.y) * 180) / Math.PI;          // 0° at +y, +90° at +x
  return COMPASS[(((i - Math.round(alpha / 45)) % 8) + 8) % 8];
}

export type WindowFix =
  | { kind: 'widen'; windowId: string; wallId: string; from_cm: number; to_cm: number; position: number; room: string }
  | { kind: 'add'; wallId: string; position: number; width_cm: number; height_cm: number; sill_cm: number; room: string; facing: Compass | null };

export interface RoomGreen {
  room_id: string; name: string; kind: string; floor_m2: number; window_m2: number; wfr: number; required_wfr: number;
  daylight_ok: boolean; cross_ventilation: boolean; window_facings: Compass[]; hot_facings: Compass[];
  shading: string[]; fix?: WindowFix; wfr_after_fix?: number;
}

export interface GreenReport {
  zone: Zone; front: Compass | null;
  rooms: RoomGreen[];
  water: { roof_m2: number; harvest_litres_year: number; demand_litres_year: number; share: number; tank_litres: number; occupants: number };
  solar: { usable_roof_m2: number; max_kwp: number; kwp: number; kwh_year: number; use_kwh_year: number; share: number; saving_year: number; currency: string;
           yield_kwh_per_kwp: number; yield_source: 'site' | 'climate' };
  carbon: { masonry_m3: number; concrete_m3: number; steel_kg: number; total_t: number;
            swaps: { change: string; saving_t: number; pct: number }[] };
  score: { total: number; after_fixes: number; parts: { name: string; points: number; max: number; note: string }[] };
}

interface Open { a: number; b: number; sill: number; top: number; window?: Window }

function windowsOnFace(floor: Floor, f: Face): Open[] {
  const wall = floor.walls.find(w => w.id === f.wallId)!;
  const len = Math.hypot(wall.end.x - wall.start.x, wall.end.y - wall.start.y);
  return floor.windows.filter(w => w.wallId === f.wallId).map(w => ({
    a: w.position * len - w.width / 2, b: w.position * len + w.width / 2, sill: w.sillHeight ?? 90, top: (w.sillHeight ?? 90) + w.height, window: w,
  })).filter(o => o.b > f.from && o.a < f.to);
}

const openArea = (o: Open, f: Face) => Math.max(0, Math.min(o.b, f.to) - Math.max(o.a, f.from)) * (o.top - o.sill) / 1e4;

/** Free stretches on a face: inside the room's span, 30 cm from its ends, clear of doors and windows. */
function freeStretches(f: Face, opens: Open[]): [number, number][] {
  const busy = [...f.doors.map(([a, b]) => [a - 15, b + 15] as [number, number]), ...opens.map(o => [o.a - 30, o.b + 30] as [number, number])]
    .sort((x, y) => x[0] - y[0]);
  const out: [number, number][] = [];
  let cur = f.from + 30;
  for (const [a, b] of busy) { if (a > cur) out.push([cur, Math.min(a, f.to - 30)]); cur = Math.max(cur, b); }
  if (f.to - 30 > cur) out.push([cur, f.to - 30]);
  return out.filter(([a, b]) => b - a > 40);
}

function isExterior(floor: Floor, f: Face): boolean {
  return (floor.walls.find(w => w.id === f.wallId)?.thickness ?? 0) >= EXTERIOR_WALL_CM - 1;
}

export function analyseGreen(floors: Floor[], climate: Climate, front: Compass | null): GreenReport {
  const required = WFR[climate.zone];
  const equator: Compass[] = climate.hemisphere === 'north' ? ['south', 'south_east', 'south_west'] : ['north', 'north_east', 'north_west'];
  const west: Compass[] = ['west', 'south_west', 'north_west'];
  const rooms: RoomGreen[] = [];
  let bedrooms = 0;
  for (const floor of floors) {
    const geometry = resolveRoomGeometry(floor, floor.rooms);
    const polygons = new Map(geometry.map(g => [g.room.id, g.polygon]));
    for (const { room, polygon } of geometry) {
      const kind = roomKind(room);
      if (!kind || !HABITABLE.has(kind) || polygon.length < 3) continue;
      if (['master', 'bedroom', 'kids'].includes(kind)) bedrooms++;
      const ctx = roomContext(structuredClone(floor), room, polygon, polygons, kind, roomCeilingHeight(room.walls, floor.walls) ?? 300);
      const floorM2 = polygonArea(polygon) / 1e4;
      let area = 0;
      const outward: { dir: Point; facing: Compass | null; m2: number }[] = [];
      for (const f of ctx.faces) {
        for (const o of windowsOnFace(floor, f)) {
          const m2 = openArea(o, f);
          area += m2;
          const dir = { x: -f.nIn.x, y: -f.nIn.y };
          outward.push({ dir, facing: front ? compassOf(dir, front) : null, m2 });
        }
      }
      const cross = outward.some((p, i) => outward.some((q, j) => j > i && p.dir.x * q.dir.x + p.dir.y * q.dir.y < 0.5));
      const facings = [...new Set(outward.map(o => o.facing).filter((x): x is Compass => !!x))];
      const hot = facings.filter(c => west.includes(c) || equator.includes(c));
      const shading = hot.map(c => west.includes(c)
        ? `${c.replace('_', '-')} window: vertical fins or a 900 mm sunshade (chajja) against low afternoon sun`
        : `${c.replace('_', '-')} window: a 600 mm horizontal sunshade (chajja) blocks high summer sun and lets winter sun in`);
      const r: RoomGreen = {
        room_id: room.id, name: room.name, kind, floor_m2: round(floorM2, 1), window_m2: round(area, 2), wfr: round(area / floorM2, 3),
        required_wfr: round(required, 3), daylight_ok: area / floorM2 >= required - 1e-6, cross_ventilation: cross,
        window_facings: facings, hot_facings: hot, shading,
      };
      if (!r.daylight_ok || !cross) {
        const fix = proposeFix(floor, ctx.faces, r, required, front, !cross);
        if (fix) { r.fix = fix.fix; r.wfr_after_fix = round(fix.area / floorM2, 3); }
      }
      rooms.push(r);
    }
  }

  // Water and solar from the roof (the ground-floor footprint collects all the rain that falls on the house).
  const ground = floors.reduce((a, f) => ((f.level ?? 0) < (a.level ?? 0) ? f : a), floors[0]);
  const roof = ground ? resolveRoomGeometry(ground, ground.rooms).reduce((s, g) => s + polygonArea(g.polygon) / 1e4, 0) : 0;
  const occupants = Math.max(2, bedrooms * 2);
  const demand = occupants * LITRES_PER_PERSON_DAY * 365;
  const harvest = roof * climate.rainfall_mm * RUNOFF;
  const usable = roof * 0.5;
  const maxKwp = Math.floor(usable * KWP_PER_M2 * 2) / 2;
  const use = Math.max(1800, bedrooms * 1200);
  // Size the system to the household's use (net metering rarely pays for big surpluses), within the roof.
  const perKwp = climate.site_yield ?? climate.sun_hours * 365 * PERFORMANCE_RATIO;
  const kwp = Math.min(maxKwp, Math.ceil((use / perKwp) * 2) / 2);
  const kwh = kwp * perKwp;

  // Indicative embodied carbon from walls, slabs and reinforcement.
  let masonry = 0;
  for (const floor of floors) for (const w of floor.walls) {
    const len = Math.hypot(w.end.x - w.start.x, w.end.y - w.start.y) / 100;
    const h = Math.max(w.startHeight ?? w.height, w.endHeight ?? w.height) / 100;
    const openings = [...floor.doors.filter(d => d.wallId === w.id).map(d => d.width * (d.height ?? 210)),
                      ...floor.windows.filter(x => x.wallId === w.id).map(x => x.width * x.height)].reduce((s, a) => s + a / 1e4, 0);
    masonry += Math.max(0, len * h - openings) * (w.thickness / 100);
  }
  const builtUp = floors.reduce((s, f) => s + resolveRoomGeometry(f, f.rooms).reduce((t, g) => t + polygonArea(g.polygon) / 1e4, 0), 0);
  const concrete = builtUp * (0.125 + 0.08);
  const steel = builtUp * 40;
  const total = (masonry * CARBON.brick + concrete * CARBON.rcc_opc + steel * CARBON.steel) / 1000;
  const swap = (change: string, saving: number) => ({ change, saving_t: round(saving / 1000, 1), pct: Math.round((saving / 1000 / total) * 100) });

  const habitable = rooms.length || 1;
  const parts = [
    { name: 'Daylight', max: 30, points: 30 * rooms.filter(r => r.daylight_ok).length / habitable,
      note: `${rooms.filter(r => r.daylight_ok).length} of ${rooms.length} rooms meet ${Math.round(required * 100)}% window-to-floor area` },
    { name: 'Cross-ventilation', max: 20, points: 20 * rooms.filter(r => r.cross_ventilation).length / habitable,
      note: `${rooms.filter(r => r.cross_ventilation).length} of ${rooms.length} rooms have windows on two sides` },
    { name: 'Sun and shading', max: 15, points: front ? 15 * (1 - rooms.filter(r => r.hot_facings.length).length / habitable / 2) : 7.5,
      note: front ? `${rooms.filter(r => r.hot_facings.length).length} rooms have west or ${equator[0]}-facing windows that need sunshades`
                  : 'orientation unknown: state which way the entrance faces' },
    { name: 'Rainwater', max: 15, points: 15 * Math.min(1, harvest / demand / 0.5), note: `roof rain covers ${Math.round((harvest / demand) * 100)}% of yearly water use` },
    { name: 'Rooftop solar', max: 10, points: 10 * Math.min(1, kwh / use), note: `${kwp} kWp covers ${Math.round((kwh / use) * 100)}% of yearly electricity` },
    { name: 'Materials', max: 10, points: 3, note: 'fired-clay bricks and OPC concrete assumed; low-carbon swaps raise this' },
  ].map(p => ({ ...p, points: round(p.points, 1) }));
  const score = Math.round(parts.reduce((s, p) => s + p.points, 0));
  const fixedDaylight = rooms.filter(r => r.daylight_ok || (r.wfr_after_fix ?? 0) >= required - 1e-6).length;
  const fixedCross = rooms.filter(r => r.cross_ventilation || r.fix?.kind === 'add').length;
  const after = Math.round(score - parts[0].points - parts[1].points - parts[5].points + 30 * fixedDaylight / habitable + 20 * fixedCross / habitable + 8);

  return {
    zone: climate.zone, front, rooms,
    water: { roof_m2: round(roof, 1), harvest_litres_year: Math.round(harvest / 1000) * 1000, demand_litres_year: Math.round(demand / 1000) * 1000,
             share: round(harvest / demand, 2), tank_litres: Math.round(Math.min(harvest * 0.1, occupants * LITRES_PER_PERSON_DAY * 20) / 500) * 500, occupants },
    solar: { usable_roof_m2: round(usable, 1), max_kwp: maxKwp, kwp, kwh_year: Math.round(kwh / 10) * 10, use_kwh_year: use, share: round(kwh / use, 2),
             saving_year: Math.round((Math.min(kwh, use) * climate.tariff) / 100) * 100, currency: climate.currency,
             yield_kwh_per_kwp: Math.round(perKwp), yield_source: climate.site_yield ? 'site' : 'climate' },
    carbon: { masonry_m3: round(masonry, 1), concrete_m3: round(concrete, 1), steel_kg: Math.round(steel), total_t: round(total, 1), swaps: [
      swap('AAC blocks instead of fired-clay bricks', masonry * (CARBON.brick - CARBON.aac)),
      swap('Fly-ash bricks instead of fired-clay bricks', masonry * (CARBON.brick - CARBON.flyash_brick)),
      swap('Fly-ash (PPC) cement in all concrete', concrete * (CARBON.rcc_opc - CARBON.rcc_ppc)),
    ] },
    score: { total: score, after_fixes: Math.max(score, Math.min(100, after)), parts },
  };
}

/** A window change that brings the room to the daylight target (and cross-ventilation when asked), checked by recomputing. */
function proposeFix(floor: Floor, faces: Face[], r: RoomGreen, required: number, front: Compass | null, needCross: boolean):
  { fix: WindowFix; area: number } | null {
  const target = required * r.floor_m2;
  const exterior = faces.filter(f => isExterior(floor, f));
  const withWindows = new Set(faces.filter(f => windowsOnFace(floor, f).length).map(f => f.wallId));
  // Cross-ventilation (or a room without windows): add a window on an exterior face that has none.
  if (needCross || r.window_m2 === 0) {
    for (const f of exterior.filter(x => !withWindows.has(x.wallId)).sort((a, b) => faceLength(b) - faceLength(a))) {
      const free = freeStretches(f, windowsOnFace(floor, f)).sort((a, b) => (b[1] - b[0]) - (a[1] - a[0]))[0];
      if (!free) continue;
      const missing = Math.max(0.6, target - r.window_m2);
      const width = Math.round(Math.min(free[1] - free[0], Math.max(60, (missing * 1e4) / 120)) / 5) * 5;
      if (width < 60) continue;
      const wall = floor.walls.find(w => w.id === f.wallId)!;
      const len = Math.hypot(wall.end.x - wall.start.x, wall.end.y - wall.start.y);
      const centre = (free[0] + free[1]) / 2;
      return { fix: { kind: 'add', wallId: f.wallId, position: round(centre / len, 4), width_cm: width, height_cm: 120, sill_cm: 90, room: r.name,
                      facing: front ? compassOf({ x: -f.nIn.x, y: -f.nIn.y }, front) : null }, area: r.window_m2 + (width * 120) / 1e4 };
    }
    if (r.window_m2 === 0) return null;
  }
  if (r.daylight_ok) return null;
  // Daylight: widen the largest window as far as its wall allows.
  let best: { f: Face; o: Open } | null = null;
  for (const f of faces) for (const o of windowsOnFace(floor, f)) if (!best || (o.b - o.a) > (best.o.b - best.o.a)) best = { f, o };
  if (!best?.o.window) return null;
  const { f, o } = best as { f: Face; o: Open & { window: Window } };
  const others = windowsOnFace(floor, f).filter(x => x !== o);
  const left = Math.max(f.from + 30, ...f.doors.filter(([, b]) => b <= o.a).map(([, b]) => b + 15), ...others.filter(x => x.b <= o.a).map(x => x.b + 30));
  const right = Math.min(f.to - 30, ...f.doors.filter(([a]) => a >= o.b).map(([a]) => a - 15), ...others.filter(x => x.a >= o.b).map(x => x.a - 30));
  const h = o.top - o.sill;
  const want = (o.b - o.a) + ((target - r.window_m2) * 1e4) / h;
  const width = Math.min(right - left, want);
  if (width <= (o.b - o.a) + 5) return null;
  // Keep the widened window inside its clear stretch of wall.
  const centre = Math.min(Math.max((o.a + o.b) / 2, left + width / 2), right - width / 2);
  const wall = floor.walls.find(w => w.id === f.wallId)!;
  const len = Math.hypot(wall.end.x - wall.start.x, wall.end.y - wall.start.y);
  const to = Math.floor(width / 5) * 5;
  return { fix: { kind: 'widen', windowId: o.window.id, wallId: f.wallId, from_cm: Math.round(o.b - o.a), to_cm: to, position: round(centre / len, 4), room: r.name },
           area: r.window_m2 + ((to - (o.b - o.a)) * h) / 1e4 };
}

/** Apply window fixes to editor floors (returns copies). */
export function applyWindowFixes(floors: Floor[], fixes: WindowFix[]): Floor[] {
  const out: Floor[] = structuredClone(floors);
  for (const fix of fixes) {
    const floor = out.find(f => f.walls.some(w => w.id === fix.wallId));
    if (!floor) continue;
    if (fix.kind === 'widen') {
      const win = floor.windows.find(w => w.id === fix.windowId);
      if (!win) continue;
      win.position = fix.position;
      win.width = fix.to_cm;
    } else {
      floor.windows.push({ id: `win-green-${Math.random().toString(36).slice(2, 9)}`, wallId: fix.wallId, position: fix.position,
                           width: fix.width_cm, height: fix.height_cm, sillHeight: fix.sill_cm, type: 'casement' });
    }
  }
  return out;
}

function round(v: number, d: number) { const k = 10 ** d; return Math.round(v * k) / k; }
