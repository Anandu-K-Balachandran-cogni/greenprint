// A design brief: what the interior designer agent (or a user) decides — a base theme, colour and
// finish overrides, preferred pieces, and per-room additions and omissions. Code turns it into
// placed, checked changes (planDesign), so the agent never positions furniture itself.

import type { Floor, Point } from '$lib/models/types';
import { furnitureCatalog, getCatalogItem } from '$lib/utils/furnitureCatalog';
import { floorMaterials } from '$lib/utils/materials';
import { resolveRoomGeometry } from '$lib/utils/roomDetection';
import { roomCeilingHeight } from '$lib/utils/wallProfiles';
import { pointInPolygon } from '$lib/utils/hitTesting';
import { clearanceIssues, LAYER_ITEMS } from './roomDesign';
import { polygonArea, roomContext } from './furnish';
import { THEMES, applyThemeChanges, getTheme, planTheme, roomKind, type RoomRequest, type Theme, type ThemeChanges } from './themes';

export interface DesignBrief {
  base_theme: string;
  title?: string;
  rationale?: string;
  colors?: Partial<Record<'walls' | 'accent' | 'ceiling' | 'wet_walls' | 'fabric' | 'wood' | 'metal' | 'curtain' | 'blind' | 'rug' | 'art', string>>;
  floors?: Partial<Record<'living' | 'bedroom' | 'wet' | 'hall', string>>;
  curtain_style?: 'curtain' | 'sheer_curtain';
  blind_style?: 'roller_blind' | 'roman_blind';
  rug_shape?: 'rug' | 'round_rug';
  lights?: Partial<Record<'living' | 'bedroom' | 'dining' | 'other', string>>;
  pieces?: Partial<Record<'sofa' | 'coffee_table' | 'tv' | 'chair' | 'bed' | 'wardrobe' | 'dining_table' | 'dining_chair', string>>;
  plants?: boolean;
  pooja?: boolean;
  rooms?: { room_id: string; add?: string[]; omit?: string[]; accent?: string }[];
}

const HEX = /^#[0-9a-f]{6}$/i;
const LIGHTS = new Set(['ceiling_light', 'chandelier', 'recessed_light', 'pendant_light', 'ceiling_fan']);
const PIECE_CATEGORY: Record<string, RegExp> = {
  sofa: /^(sofa|sofa_long|sofa_corner|loveseat)$/, coffee_table: /^(coffee_table|coffee_table_glass|side_table)$/, tv: /^(tv_unit|tv_stand)$/,
  chair: /^(chair|accent_chair)$/, bed: /^(bed_queen|bed_twin|bunk_bed)$/, wardrobe: /^(wardrobe|wardrobe_wide|dresser)$/,
  dining_table: /^(dining_table|dining_table_round|dining_table_glass)$/, dining_chair: /^(dining_chair|dining_chair_cushion)$/,
};

/** Indoor catalogue pieces a design may use. */
export const INDOOR_CATEGORIES = ['Living Room', 'Bedroom', 'Kitchen', 'Bathroom', 'Office', 'Dining', 'Decor', 'Lighting'];
export const designCatalog = () => furnitureCatalog.filter(f => !f.symbol && INDOOR_CATEGORIES.includes(f.category));

/** Problems with a brief (unknown ids, bad colours); an empty list means it can be planned. */
export function briefProblems(brief: DesignBrief, floor?: Floor): string[] {
  const p: string[] = [];
  if (!getTheme(brief.base_theme)) p.push(`base_theme "${brief.base_theme}" is not one of ${THEMES.map(t => t.id).join(', ')}`);
  for (const [k, v] of Object.entries(brief.colors ?? {})) if (v !== undefined && !HEX.test(v)) p.push(`colors.${k} must be a #rrggbb colour`);
  for (const [k, v] of Object.entries(brief.floors ?? {})) if (v && !floorMaterials.some(m => m.id === v)) p.push(`floors.${k} "${v}" is not a floor material`);
  const choice = (key: 'curtain_style' | 'blind_style' | 'rug_shape', allowed: string[]) => {
    const v = brief[key];
    if (v !== undefined && !allowed.includes(v)) p.push(`${key} "${v}" must be one of ${allowed.join(', ')}`);
  };
  choice('curtain_style', ['curtain', 'sheer_curtain']); choice('blind_style', ['roller_blind', 'roman_blind']); choice('rug_shape', ['rug', 'round_rug']);
  for (const [k, v] of Object.entries(brief.lights ?? {})) if (v && !LIGHTS.has(v)) p.push(`lights.${k} "${v}" must be one of ${[...LIGHTS].join(', ')}`);
  for (const [k, v] of Object.entries(brief.pieces ?? {})) if (v && !(PIECE_CATEGORY[k]?.test(v))) p.push(`pieces.${k} "${v}" is not a ${k.replace('_', ' ')}`);
  const indoor = new Set(designCatalog().map(f => f.id));
  const roomIds = floor ? new Set(floor.rooms.map(r => r.id)) : null;
  for (const r of brief.rooms ?? []) {
    if (roomIds && !roomIds.has(r.room_id)) p.push(`rooms: "${r.room_id}" is not a room on this floor`);
    for (const id of [...(r.add ?? []), ...(r.omit ?? [])]) if (!indoor.has(id)) p.push(`rooms.${r.room_id}: "${id}" is not an indoor catalogue piece`);
    if (r.accent && !HEX.test(r.accent)) p.push(`rooms.${r.room_id}.accent must be a #rrggbb colour`);
  }
  return p;
}

/** The theme a brief describes: its base theme with the overrides applied. */
export function briefTheme(brief: DesignBrief): Theme {
  const base = getTheme(brief.base_theme) ?? THEMES[0];
  const c = brief.colors ?? {}, pc = brief.pieces ?? {};
  return {
    ...base, id: `custom-${base.id}`, name: brief.title || base.name,
    walls: c.walls ?? base.walls, accent: c.accent ?? base.accent, ceiling: c.ceiling ?? base.ceiling, wetWalls: c.wet_walls ?? base.wetWalls,
    floors: { ...base.floors, ...stripUndefined(brief.floors ?? {}) },
    palette: { fabric: c.fabric ?? base.palette.fabric, wood: c.wood ?? base.palette.wood, metal: c.metal ?? base.palette.metal },
    curtain: brief.curtain_style ?? base.curtain, curtainColor: c.curtain ?? base.curtainColor,
    blind: brief.blind_style ?? base.blind, blindColor: c.blind ?? base.blindColor,
    rug: c.rug ?? base.rug, rugShape: brief.rug_shape ?? base.rugShape, art: c.art ?? base.art,
    lights: { ...base.lights, ...stripUndefined(brief.lights ?? {}) },
    pieces: {
      sofa: pc.sofa ?? base.pieces.sofa, coffeeTable: pc.coffee_table ?? base.pieces.coffeeTable, tv: pc.tv ?? base.pieces.tv,
      chair: pc.chair ?? base.pieces.chair, bed: pc.bed ?? base.pieces.bed, wardrobe: pc.wardrobe ?? base.pieces.wardrobe,
      diningTable: pc.dining_table ?? base.pieces.diningTable, diningChair: pc.dining_chair ?? base.pieces.diningChair,
    },
    plants: brief.plants ?? base.plants, pooja: brief.pooja ?? base.pooja,
  };
}

function stripUndefined<T extends object>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')) as Partial<T>;
}

export interface DesignOptions { roomIds?: string[]; furnish?: boolean; homeHasPooja?: boolean }

export interface DesignReport {
  changes: ThemeChanges;
  rooms: { room_id: string; name: string; kind: string; pieces: string[]; paint_m2: number; wall_color: string; accent?: string; floor: string }[];
  skipped: ThemeChanges['skipped'];
  /** Clearance problems after applying (always empty: the solver only places what fits). */
  issues: string[];
  added: { id: string; name: string; count: number }[];
}

/** Plan a brief on a floor and check the result. */
export function planDesign(floor: Floor, brief: DesignBrief, opts: DesignOptions = {}): DesignReport {
  const theme = briefTheme(brief);
  const rooms: Record<string, RoomRequest> = {};
  for (const r of brief.rooms ?? []) rooms[r.room_id] = { add: r.add, omit: r.omit, accent: r.accent };
  const changes = planTheme(floor, theme, { roomIds: opts.roomIds, furnish: opts.furnish, homeHasPooja: opts.homeHasPooja, rooms });
  const after = applyThemeChanges(floor, changes);
  const issues: string[] = [];
  const out: DesignReport['rooms'] = [];
  const name = (id: string) => getCatalogItem(id)?.name ?? id;
  for (const { room, polygon } of resolveRoomGeometry(after, after.rooms)) {
    if (!changes.rooms.some(r => r.roomId === room.id)) continue;
    for (const i of clearanceIssues(after, polygon)) issues.push(`${room.name}: ${i.message}`);
    const spans = after.walls.flatMap(w => (w.paints ?? []).filter(p => p.roomId === room.id).map(p => ({ ...p, h: Math.max(w.startHeight ?? w.height, w.endHeight ?? w.height) })));
    const counts = new Map<string, number>();
    for (const s of spans) counts.set(s.color, (counts.get(s.color) ?? 0) + (s.to - s.from));
    const [wallColor] = [...counts].sort((a, b) => b[1] - a[1])[0] ?? [''];
    out.push({
      room_id: room.id, name: room.name, kind: roomKind(room) ?? 'other',
      pieces: after.furniture.filter(f => pointInPolygon(f.position, polygon) && !LAYER_ITEMS.has(f.catalogId)).map(f => name(f.catalogId)),
      paint_m2: Math.round(spans.reduce((s, p) => s + (p.to - p.from) * (p.h - 8), 0) / 1000) / 10,
      wall_color: wallColor, accent: spans.find(s => s.color !== wallColor)?.color, floor: room.floorTexture,
    });
  }
  const added = new Map<string, number>();
  for (const a of changes.add) added.set(a.catalogId, (added.get(a.catalogId) ?? 0) + 1);
  return { changes, rooms: out, skipped: changes.skipped, issues, added: [...added].map(([id, count]) => ({ id, name: name(id), count })) };
}

/** Facts about the rooms a designer needs: kind, size, openings, what is there now. */
export function roomFacts(floor: Floor, roomIds?: string[]) {
  const geometry = resolveRoomGeometry(floor, floor.rooms);
  const polygons = new Map(geometry.map(g => [g.room.id, g.polygon]));
  return geometry.filter(g => (!roomIds || roomIds.includes(g.room.id)) && g.polygon.length >= 3).map(({ room, polygon }) => {
    const kind = roomKind(room);
    const xs = polygon.map((p: Point) => p.x), ys = polygon.map((p: Point) => p.y);
    const ctx = roomContext(structuredClone(floor), room, polygon, polygons, kind ?? 'living', roomCeilingHeight(room.walls, floor.walls) ?? 270);
    // Only the openings on this room's stretch of each wall.
    const doors = ctx.faces.flatMap(f => f.doors.filter(([x, y]) => y > f.from && x < f.to));
    const windows = ctx.faces.flatMap(f => f.windows.filter(w => w.b > f.from && w.a < f.to));
    const free = ctx.faces.reduce((s, f) => s + (f.to - f.from), 0) - doors.reduce((a, [x, y]) => a + (y - x), 0) - windows.reduce((a, w) => a + (w.b - w.a), 0);
    return {
      room_id: room.id, name: room.name, kind: kind ?? 'not styled (garage, terrace or stair)',
      area_m2: Math.round(polygonArea(polygon) / 1000) / 10,
      size_cm: [Math.round(Math.max(...xs) - Math.min(...xs)), Math.round(Math.max(...ys) - Math.min(...ys))],
      ceiling_cm: roomCeilingHeight(room.walls, floor.walls) ?? null,
      doors: doors.length,
      windows: windows.map(w => ({ width_cm: Math.round(w.b - w.a), sill_cm: w.sill })),
      free_wall_cm: Math.max(0, Math.round(free)),
      furniture: floor.furniture.filter(f => pointInPolygon(f.position, polygon)).map(f => getCatalogItem(f.catalogId)?.name ?? f.catalogId),
      floor: room.floorTexture, ceiling: room.ceilingColor ?? '#f5f5f0',
    };
  });
}
