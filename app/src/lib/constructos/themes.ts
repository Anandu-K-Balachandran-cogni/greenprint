// Interior themes as data, and the planner that applies one to rooms: wall paint (with an accent
// wall), floor and ceiling, a recolour of the furniture by material role, furniture for empty
// rooms, and the soft finishing — curtains or blinds on every window, a rug, a ceiling light or
// fan, a plant, art, and (in the Indian themes) a pooja unit.
//
// planTheme is pure: it works on a copy of the floor and returns the changes, so the editor can
// apply them as one undo step (and a future agent can preview them before accepting).

import type { Floor, FurnitureItem, FurniturePalette, Point, Room, Wall } from '$lib/models/types';
import { resolveRoomGeometry } from '$lib/utils/roomDetection';
import { roomCeilingHeight } from '$lib/utils/wallProfiles';
import { pointInPolygon } from '$lib/utils/hitTesting';
import { roomType } from './routingSource';
import { spanUpdates, curtainPlans, hasCurtainNear, footprint } from './roomDesign';
import {
  centroid, commit, fits, furnishRoom, faceU, placeFirst, placeInCorner, placeRelative, placeOnFace, polygonArea, roomContext, roomPieces,
  size, solidFaces, type Draft, type Face, type Pieces, type RoomCtx, type RoomKind,
} from './furnish';

export interface Theme {
  id: string; name: string; blurb: string;
  walls: string; accent: string; ceiling: string; wetWalls: string;
  /** Floor material ids (materials.ts) for living spaces, bedrooms, kitchens/bathrooms and halls. */
  floors: { living: string; bedroom: string; wet: string; hall: string };
  palette: FurniturePalette;
  curtain: 'curtain' | 'sheer_curtain'; curtainColor: string; blind: 'roller_blind' | 'roman_blind'; blindColor: string;
  rug: string; rugShape: 'rug' | 'round_rug'; art: string;
  /** Ceiling fixture per room group (catalogue ids). */
  lights: { living: string; bedroom: string; dining: string; other: string };
  pieces: Pieces;
  plants: boolean; pooja: boolean;
}

const BASE: Pieces = { sofa: 'sofa_long', coffeeTable: 'coffee_table', tv: 'tv_unit', chair: 'accent_chair', bed: 'bed_queen',
  wardrobe: 'wardrobe_wide', diningTable: 'dining_table', diningChair: 'dining_chair' };

export const THEMES: Theme[] = [
  {
    id: 'indian-contemporary', name: 'Indian Contemporary', blurb: 'Warm whites, a rust accent, sheesham wood, mustard and brass; fans for the heat.',
    walls: '#f6efe4', accent: '#b5562f', ceiling: '#ffffff', wetWalls: '#f3f0ea',
    floors: { living: 'marble-white', bedroom: 'light-oak', wet: 'ceramic-gray', hall: 'marble-white' },
    palette: { fabric: '#d9a441', wood: '#7a4a2a', metal: '#b08d57' },
    curtain: 'curtain', curtainColor: '#c9773f', blind: 'roller_blind', blindColor: '#efe6d8', rug: '#a33b2a', rugShape: 'rug', art: '#2f6f73',
    lights: { living: 'ceiling_fan', bedroom: 'ceiling_fan', dining: 'pendant_light', other: 'ceiling_light' },
    pieces: { ...BASE, diningChair: 'dining_chair_cushion' }, plants: true, pooja: true,
  },
  {
    id: 'warm-traditional', name: 'Warm Traditional', blurb: 'Teak, brass and maroon on butter-yellow walls; chandeliers and a pooja unit.',
    walls: '#f3e3c3', accent: '#7b2d26', ceiling: '#faf3e3', wetWalls: '#f4ecdc',
    floors: { living: 'walnut', bedroom: 'walnut', wet: 'ceramic-gray', hall: 'marble-white' },
    palette: { fabric: '#8c1c13', wood: '#5c3317', metal: '#c9a227' },
    curtain: 'curtain', curtainColor: '#7b2d26', blind: 'roman_blind', blindColor: '#d6b98c', rug: '#8c1c13', rugShape: 'rug', art: '#c9a227',
    lights: { living: 'chandelier', bedroom: 'ceiling_fan', dining: 'chandelier', other: 'ceiling_light' },
    pieces: { ...BASE, sofa: 'sofa', coffeeTable: 'coffee_table', chair: 'chair', diningChair: 'dining_chair' }, plants: true, pooja: true,
  },
  {
    id: 'scandinavian', name: 'Scandinavian', blurb: 'White walls, birch wood, soft greys and black metal; light and calm.',
    walls: '#ffffff', accent: '#a7b8a8', ceiling: '#ffffff', wetWalls: '#ffffff',
    floors: { living: 'light-oak', bedroom: 'light-oak', wet: 'ceramic-white', hall: 'light-oak' },
    palette: { fabric: '#d6d3d1', wood: '#e0c9a6', metal: '#2b2b2b' },
    curtain: 'sheer_curtain', curtainColor: '#f5f0e8', blind: 'roller_blind', blindColor: '#f5f5f4', rug: '#e7e5e4', rugShape: 'rug', art: '#9fb4a3',
    lights: { living: 'pendant_light', bedroom: 'pendant_light', dining: 'pendant_light', other: 'ceiling_light' },
    pieces: { ...BASE, sofa: 'sofa', chair: 'accent_chair' }, plants: true, pooja: false,
  },
  {
    id: 'modern-minimal', name: 'Modern Minimal', blurb: 'Pale greys with a charcoal wall, glass and chrome, recessed lighting.',
    walls: '#f3f4f6', accent: '#374151', ceiling: '#ffffff', wetWalls: '#f9fafb',
    floors: { living: 'porcelain', bedroom: 'laminate', wet: 'porcelain', hall: 'porcelain' },
    palette: { fabric: '#9ca3af', wood: '#c8b8a6', metal: '#d1d5db' },
    curtain: 'sheer_curtain', curtainColor: '#e5e7eb', blind: 'roller_blind', blindColor: '#e5e7eb', rug: '#6b7280', rugShape: 'rug', art: '#111827',
    lights: { living: 'recessed_light', bedroom: 'recessed_light', dining: 'pendant_light', other: 'recessed_light' },
    pieces: { ...BASE, coffeeTable: 'coffee_table_glass', diningTable: 'dining_table_glass', diningChair: 'dining_chair_cushion' }, plants: false, pooja: false,
  },
  {
    id: 'industrial', name: 'Industrial', blurb: 'Concrete floors, a brick-red wall, cognac leather, dark wood and black steel.',
    walls: '#d1d5db', accent: '#8b4a2b', ceiling: '#e5e7eb', wetWalls: '#e5e7eb',
    floors: { living: 'concrete', bedroom: 'walnut', wet: 'slate', hall: 'concrete' },
    palette: { fabric: '#7a4b2a', wood: '#4a3426', metal: '#1f2937' },
    curtain: 'curtain', curtainColor: '#57534e', blind: 'roller_blind', blindColor: '#78716c', rug: '#57534e', rugShape: 'rug', art: '#b45309',
    lights: { living: 'pendant_light', bedroom: 'pendant_light', dining: 'pendant_light', other: 'ceiling_light' },
    pieces: { ...BASE, sofa: 'sofa', chair: 'chair' }, plants: false, pooja: false,
  },
  {
    id: 'coastal', name: 'Coastal', blurb: 'Airy whites and sea blues, driftwood tones, sheer curtains and a ceiling fan.',
    walls: '#f8fafc', accent: '#5b8fb9', ceiling: '#ffffff', wetWalls: '#f0f9ff',
    floors: { living: 'bamboo', bedroom: 'light-oak', wet: 'ceramic-white', hall: 'bamboo' },
    palette: { fabric: '#e0eef8', wood: '#e8dcc4', metal: '#cbd5e1' },
    curtain: 'sheer_curtain', curtainColor: '#ffffff', blind: 'roman_blind', blindColor: '#dbeafe', rug: '#93c5fd', rugShape: 'round_rug', art: '#1d4e89',
    lights: { living: 'ceiling_fan', bedroom: 'ceiling_fan', dining: 'pendant_light', other: 'ceiling_light' },
    pieces: { ...BASE, diningTable: 'dining_table_round', diningChair: 'dining_chair' }, plants: true, pooja: false,
  },
  {
    id: 'japandi', name: 'Japandi', blurb: 'Greige and olive, warm oak, low clean lines and roman blinds.',
    walls: '#efe9e1', accent: '#6b705c', ceiling: '#f5f0e6', wetWalls: '#f2eee8',
    floors: { living: 'light-oak', bedroom: 'bamboo', wet: 'ceramic-gray', hall: 'light-oak' },
    palette: { fabric: '#d6cfc4', wood: '#b08968', metal: '#3f3f46' },
    curtain: 'curtain', curtainColor: '#e7dfd2', blind: 'roman_blind', blindColor: '#d6cfc4', rug: '#cbbfae', rugShape: 'rug', art: '#3f3f46',
    lights: { living: 'pendant_light', bedroom: 'pendant_light', dining: 'pendant_light', other: 'recessed_light' },
    pieces: { ...BASE, sofa: 'sofa', coffeeTable: 'coffee_table', chair: 'accent_chair', diningChair: 'dining_chair' }, plants: true, pooja: false,
  },
  {
    id: 'boho', name: 'Boho', blurb: 'Terracotta and mustard, rattan-toned wood, a round rug and plenty of plants.',
    walls: '#fdf6ec', accent: '#c06c3e', ceiling: '#ffffff', wetWalls: '#fbf3e8',
    floors: { living: 'light-oak', bedroom: 'laminate', wet: 'ceramic-white', hall: 'light-oak' },
    palette: { fabric: '#d08c60', wood: '#a47148', metal: '#b08d57' },
    curtain: 'curtain', curtainColor: '#e9c46a', blind: 'roman_blind', blindColor: '#e9c46a', rug: '#c2703d', rugShape: 'round_rug', art: '#2a9d8f',
    lights: { living: 'pendant_light', bedroom: 'pendant_light', dining: 'pendant_light', other: 'ceiling_light' },
    pieces: { ...BASE, sofa: 'sofa', chair: 'chair' }, plants: true, pooja: false,
  },
];

export const getTheme = (id: string) => THEMES.find(t => t.id === id);

/** Room kind from the Greenprint space type (or the editor's room type and name). */
export function roomKind(room: Room): RoomKind | null {
  const t = roomType(room);
  if (t === 'living' || t === 'family') return 'living';
  if (t === 'dining') return 'dining';
  if (t === 'master_bedroom') return 'master';
  if (t === 'bedroom' || t === 'guest_room') return /kid|child/i.test(room.name) ? 'kids' : 'bedroom';
  if (t === 'kitchen') return 'kitchen';
  if (t === 'bathroom' || t === 'toilet') return 'bathroom';
  if (t === 'study' || t === 'home_office') return 'study';
  if (t === 'hall' || t === 'foyer' || t === 'corridor' || t === 'lobby') return 'circulation';
  if (t === 'utility' || t === 'laundry' || t === 'store') return 'utility';
  return null;   // garage, terrace, stair and unknown spaces keep their finish
}

const WET: RoomKind[] = ['kitchen', 'bathroom', 'utility'];
const TEXTILES = new Set(['curtain', 'sheer_curtain', 'roller_blind', 'roman_blind', 'rug', 'round_rug', 'runner_rug', 'cushion']);
const FIXTURES = new Set(['ceiling_light', 'chandelier', 'recessed_light', 'pendant_light', 'ceiling_fan']);
/** Procedural ceiling fixtures are modelled hanging from 260 cm; lift them to the real ceiling. */
const PROCEDURAL_CEILING = 260;

export interface NewFurniture extends Draft { palette?: FurniturePalette }
export interface ThemeChanges {
  walls: { wallId: string; patch: Partial<Wall> }[];
  rooms: { roomId: string; patch: { floorTexture?: string; ceilingColor?: string } }[];
  restyle: { id: string; patch: Partial<FurnitureItem> }[];
  add: NewFurniture[];
  summary: { rooms: string[]; furnished: string[]; added: number; restyled: number };
  /** Per room: what was asked for and could not be placed, and why. */
  skipped: { roomId: string; room: string; item: string; reason: string }[];
}

/** Per-room requests on top of a theme (from the interior designer agent or the user). */
export interface RoomRequest { add?: string[]; omit?: string[]; accent?: string }

export interface PlanOptions {
  roomIds?: string[]; furnish?: boolean;
  /** Another floor already has a pooja unit (one per home). */
  homeHasPooja?: boolean;
  rooms?: Record<string, RoomRequest>;
}

const PAIRS: Record<string, { with: RegExp; place: (ctx: RoomCtx, id: string, partner: FurnitureItem) => FurnitureItem | null }> = {
  office_chair: { with: /^desk$/, place: (ctx, id, p) => placeRelative(ctx, id, p, 0, size(p.catalogId).d / 2 + 15, 180) },
  bar_stool: { with: /^kitchen_island$/, place: (ctx, id, p) => [-1, 1, 0].map(k => placeRelative(ctx, id, p, k * 45, size(p.catalogId).d / 2 + 22, 180)).find(Boolean) ?? null },
  ottoman: { with: /^coffee_table/, place: (ctx, id, p) => [1, -1].map(k => placeRelative(ctx, id, p, k * (size(p.catalogId).w / 2 + 40), 0)).find(Boolean) ?? null },
  dining_chair: { with: /^dining_table/, place: (ctx, id, p) => [1, -1].map(k => placeRelative(ctx, id, p, k * (size(p.catalogId).w / 2 + 27), 0, k * 90)).find(Boolean) ?? null },
};
const CORNER_ITEMS = new Set(['potted_plant', 'floor_plant', 'floor_lamp', 'coat_rack', 'ottoman', 'accent_chair', 'chair']);

/** Plan a theme for the given rooms (all themed rooms on the floor by default). */
export function planTheme(floor: Floor, theme: Theme, opts: PlanOptions = {}): ThemeChanges {
  const work: Floor = structuredClone(floor);
  const originalIds = new Set(floor.furniture.map(f => f.id));
  const geometry = resolveRoomGeometry(work, work.rooms);
  const polygons = new Map(geometry.map(g => [g.room.id, g.polygon]));
  const out: ThemeChanges = { walls: [], rooms: [], restyle: [], add: [], summary: { rooms: [], furnished: [], added: 0, restyled: 0 }, skipped: [] };
  const touchedWalls = new Set<string>();
  let poojaPlaced = !!opts.homeHasPooja || work.furniture.some(f => f.catalogId === 'pooja_unit');
  const targets = geometry.filter(g => (!opts.roomIds || opts.roomIds.includes(g.room.id)) && g.polygon.length >= 3 && roomKind(g.room));
  // Pooja unit goes in the dining room if there is one, else the living room.
  const poojaRoom = theme.pooja && !poojaPlaced
    ? (targets.find(g => roomKind(g.room) === 'dining') ?? targets.find(g => roomKind(g.room) === 'living'))?.room.id : undefined;

  for (const { room, polygon } of targets) {
    const kind = roomKind(room)!;
    const ceiling = roomCeilingHeight(room.walls, work.walls) ?? 270;
    const ctx = roomContext(work, room, polygon, polygons, kind, ceiling);
    out.summary.rooms.push(room.name);

    // Furniture first, so the accent wall can follow the bed or sofa.
    let anchor: { item?: FurnitureItem; face?: Face } = {};
    let table: FurnitureItem | undefined;
    if (opts.furnish !== false && roomPieces(ctx).length === 0) {
      const f = furnishRoom(ctx, theme.pieces);
      anchor = { item: f.anchor, face: f.anchorFace }; table = f.table;
      if (roomPieces(ctx).length) out.summary.furnished.push(room.name);
    } else {
      const pieces = roomPieces(ctx);
      const main = pieces.find(p => /^bed|bunk/.test(p.catalogId)) ?? pieces.find(p => /^sofa|loveseat/.test(p.catalogId));
      // The face the bed or sofa stands against: close behind it, within the face's stretch.
      if (main) anchor = { item: main, face: ctx.faces.find(f => distanceToFace(f, main.position) < size(main.catalogId).d / 2 + f.half + 25
        && faceU(f, main.position) > f.from && faceU(f, main.position) < f.to) };
      table = pieces.find(p => /coffee_table|dining_table/.test(p.catalogId));
    }

    // Requests for this room: leave out pieces the furnishing added, then add the extra pieces asked for.
    const request = opts.rooms?.[room.id] ?? {};
    if (request.omit?.length) {
      const drop = new Set(work.furniture.filter(f => !originalIds.has(f.id) && request.omit!.includes(f.catalogId) && pointInPolygon(f.position, polygon)).map(f => f.id));
      // A TV goes with its unit, a chair with its desk.
      for (const f of work.furniture) if (drop.has(f.id) && /tv_unit|tv_stand/.test(f.catalogId))
        for (const t of work.furniture) if (t.catalogId === 'television' && Math.hypot(t.position.x - f.position.x, t.position.y - f.position.y) < 40) drop.add(t.id);
      work.furniture = work.furniture.filter(f => !drop.has(f.id));
      if (anchor.item && drop.has(anchor.item.id)) anchor = {};
      if (table && drop.has(table.id)) table = undefined;
    }
    for (const id of request.add ?? []) {
      // Pieces that belong with another: a chair at its desk, a stool at the counter, an ottoman by the table.
      const partner = PAIRS[id] && work.furniture.find(f => PAIRS[id].with.test(f.catalogId) && pointInPolygon(f.position, polygon));
      const paired = partner ? PAIRS[id].place(ctx, id, partner) : null;
      const placed = paired ?? (CORNER_ITEMS.has(id)
        ? placeInCorner(ctx, id) ?? placeFirst(ctx, [id], solidFaces(ctx))?.item
        : placeFirst(ctx, [id], solidFaces(ctx))?.item ?? placeInCorner(ctx, id));
      if (!placed) out.skipped.push({ roomId: room.id, room: room.name, item: id,
        reason: 'no free spot that keeps doors, windows and the other furniture clear' });
    }

    // Walls: the room's stretch of every face; an accent behind the bed or sofa (or the longest solid wall).
    const accentFace = ['living', 'master', 'bedroom', 'kids', 'dining'].includes(kind)
      ? (anchor.face ?? solidFaces(ctx).find(f => !f.doors.length && !f.windows.length)) : undefined;
    const main = WET.includes(kind) ? theme.wetWalls : theme.walls;
    const accent = request.accent ?? theme.accent;
    for (const u of spanUpdates(work, ctx.walls, polygon, room.id, main, accentFace ? { wallId: accentFace.wallId, color: accent } : undefined)) {
      const w = work.walls.find(x => x.id === u.wallId)!;
      Object.assign(w, u.patch); touchedWalls.add(w.id);
    }
    const floorTexture = kind === 'master' || kind === 'bedroom' || kind === 'kids' || kind === 'study' ? theme.floors.bedroom
      : WET.includes(kind) ? theme.floors.wet : kind === 'circulation' ? theme.floors.hall : theme.floors.living;
    out.rooms.push({ roomId: room.id, patch: { floorTexture, ceilingColor: theme.ceiling } });

    // Window dressing: blinds in wet rooms, the theme's curtains elsewhere.
    for (const plan of curtainPlans(work, ctx.walls, polygon, ceiling)) {
      if (hasCurtainNear(work, plan.position) || work.furniture.some(f => /blind/.test(f.catalogId) && Math.hypot(f.position.x - plan.position.x, f.position.y - plan.position.y) < 40)) continue;
      const win = work.windows.find(w => w.id === plan.windowId)!;
      if (WET.includes(kind)) {
        const sill = win.sillHeight ?? 90;
        commit(ctx, { catalogId: theme.blind, position: plan.position, rotation: plan.rotation, width: Math.round(win.width + 10),
          height: Math.round(win.height + 8), depth: 6, elevation: Math.max(0, sill - 3), color: theme.blindColor });
      } else {
        commit(ctx, { catalogId: theme.curtain, position: plan.position, rotation: plan.rotation, width: plan.width, height: plan.height,
          depth: plan.depth, color: theme.curtainColor });
      }
    }

    // A rug under the seating, the bed's foot, or the dining table.
    const rugAnchor = table ?? (anchor.item && /^bed|bunk/.test(anchor.item.catalogId) ? anchor.item : undefined);
    if (['living', 'master', 'bedroom', 'kids', 'dining', 'study'].includes(kind) && !work.furniture.some(f => /rug/.test(f.catalogId) && pointInPolygon(f.position, polygon))) {
      placeRug(ctx, theme, rugAnchor ?? anchor.item);
    }

    // One ceiling fixture at the centre.
    const c = centroid(polygon);
    if (c && !work.furniture.some(f => FIXTURES.has(f.catalogId) && pointInPolygon(f.position, polygon))) {
      const id = kind === 'living' ? theme.lights.living : kind === 'dining' ? theme.lights.dining
        : kind === 'master' || kind === 'bedroom' || kind === 'kids' ? theme.lights.bedroom : theme.lights.other;
      const lift = id === 'ceiling_fan' ? ceiling - size(id).h - 3 : Math.max(0, ceiling - PROCEDURAL_CEILING);
      commit(ctx, { catalogId: id, position: c, rotation: 0, elevation: Math.round(lift) });
    }

    // Finishing: art on the accent wall, a plant in a corner, the pooja unit.
    const inRoom = (re: RegExp) => work.furniture.some(f => re.test(f.catalogId) && pointInPolygon(f.position, polygon));
    if (accentFace && kind !== 'kitchen' && !inRoom(/^wall_art$/)) {
      const at = anchor.item ? faceU(accentFace, anchor.item.position) : (accentFace.from + accentFace.to) / 2;
      const art = placeOnFace(ctx, 'wall_art', accentFace, { near: at, gap: 1 });
      if (art) commit(ctx, { ...art, color: theme.art });
    }
    if (theme.plants && !inRoom(/plant/) && ['living', 'dining', 'master', 'study', 'circulation'].includes(kind) && polygonArea(polygon) > 60000) placeInCorner(ctx, kind === 'living' ? 'floor_plant' : 'potted_plant');
    if (room.id === poojaRoom && !poojaPlaced) {
      const p = solidFaces(ctx).map(f => placeOnFace(ctx, 'pooja_unit', f)).find(Boolean);
      if (p) { commit(ctx, p); poojaPlaced = true; }
    }

    // Recolour every piece in the room to the theme.
    for (const f of work.furniture) {
      if (!pointInPolygon(f.position, polygon)) continue;
      if (TEXTILES.has(f.catalogId)) {
        const color = /blind/.test(f.catalogId) ? theme.blindColor : /curtain/.test(f.catalogId) ? theme.curtainColor : theme.rug;
        f.color = color;
      } else if (!FIXTURES.has(f.catalogId) && f.catalogId !== 'wall_art') {
        f.palette = { ...theme.palette };
      }
    }
  }

  // Collect: wall paints, new pieces (not in the original floor), and restyles of existing ones.
  for (const id of touchedWalls) out.walls.push({ wallId: id, patch: { paints: work.walls.find(w => w.id === id)!.paints } });
  for (const f of work.furniture) {
    if (!originalIds.has(f.id)) {
      out.add.push({ catalogId: f.catalogId, position: { x: Math.round(f.position.x), y: Math.round(f.position.y) }, rotation: f.rotation,
        ...(f.width ? { width: f.width } : {}), ...(f.depth ? { depth: f.depth } : {}), ...(f.height ? { height: f.height } : {}),
        ...(f.elevation !== undefined ? { elevation: f.elevation } : {}), ...(f.color ? { color: f.color } : {}), ...(f.palette ? { palette: f.palette } : {}) });
    } else {
      const before = floor.furniture.find(x => x.id === f.id)!;
      const patch: Partial<FurnitureItem> = {};
      if (f.color !== before.color) patch.color = f.color;
      if (JSON.stringify(f.palette) !== JSON.stringify(before.palette)) patch.palette = f.palette;
      if (Object.keys(patch).length) out.restyle.push({ id: f.id, patch });
    }
  }
  out.summary.added = out.add.length;
  out.summary.restyled = out.restyle.length;
  return out;
}

function distanceToFace(f: Face, p: Point): number {
  return Math.abs((p.x - f.start.x) * f.nIn.x + (p.y - f.start.y) * f.nIn.y);
}

function placeRug(ctx: RoomCtx, theme: Theme, anchor?: FurnitureItem) {
  const area = polygonArea(ctx.polygon) / 10000;
  let at: Point | null = null, rotation = 0, w = area > 18 ? 240 : 200, d = area > 18 ? 170 : 140;
  if (anchor) {
    const a = size(anchor.catalogId);
    rotation = anchor.rotation;
    if (/^bed|bunk/.test(anchor.catalogId)) {
      // Under the lower two-thirds of the bed, showing on both sides.
      const r = (anchor.rotation * Math.PI) / 180, fw = { x: -Math.sin(r), y: Math.cos(r) };
      at = { x: anchor.position.x + fw.x * a.d * 0.2, y: anchor.position.y + fw.y * a.d * 0.2 };
      w = a.w + 80; d = Math.round(a.d * 0.75);
    } else if (/dining_table/.test(anchor.catalogId)) {
      at = anchor.position; w = a.w + 110; d = a.d + 110;
    } else if (/sofa|loveseat/.test(anchor.catalogId)) {
      const r = (anchor.rotation * Math.PI) / 180, fw = { x: -Math.sin(r), y: Math.cos(r) };
      at = { x: anchor.position.x + fw.x * (a.d / 2 + 60), y: anchor.position.y + fw.y * (a.d / 2 + 60) };
    } else at = anchor.position;
  }
  at ??= centroid(ctx.polygon);
  if (!at) return;
  const shape = theme.rugShape;
  for (let k = 0; k < 5; k++) {
    const sw = Math.round(shape === 'round_rug' ? Math.min(w, d) : w), sd = Math.round(shape === 'round_rug' ? Math.min(w, d) : d);
    const draft: Draft = { catalogId: shape, position: at, rotation, width: sw, depth: sd, color: theme.rug };
    // Keep a 15 cm border of floor visible between the rug and the walls.
    const inset = footprint({ id: 'r', catalogId: shape, position: at, rotation, scale: { x: 1, y: 1, z: 1 }, width: sw + 30, depth: sd + 30 });
    if (fits(ctx, draft) && inset.every(p => pointInPolygon(p, ctx.polygon))) { commit(ctx, draft); return; }
    w *= 0.85; d *= 0.85;
    if (Math.min(w, d) < 90) return;
  }
}

/** Apply planned changes to a floor copy (tests and previews; the editor applies through the store). */
export function applyThemeChanges(floor: Floor, changes: ThemeChanges): Floor {
  const out: Floor = structuredClone(floor);
  for (const { wallId, patch } of changes.walls) Object.assign(out.walls.find(w => w.id === wallId)!, patch);
  for (const { roomId, patch } of changes.rooms) { const r = out.rooms.find(x => x.id === roomId); if (r) Object.assign(r, patch); }
  for (const { id, patch } of changes.restyle) Object.assign(out.furniture.find(f => f.id === id)!, patch);
  changes.add.forEach((d, i) => out.furniture.push({ id: `themed-${i}-${Math.random().toString(36).slice(2, 8)}`, scale: { x: 1, y: 1, z: 1 }, ...d }));
  return out;
}
