// Reduce the saved editor model to what service routing needs, with a fingerprint.
//
// Diagrams are generated from the architecture the user actually saved (including
// manual edits), never from the solver's rectangles. The fingerprint is a SHA-256
// of the canonical routing source; any architectural change gives a new
// fingerprint, which makes earlier diagrams out of date.

import type { Floor, Project, Room, Wall } from '$lib/models/types';
import { resolveRoomGeometry } from '$lib/utils/roomDetection';
import { EXTERIOR_WALL_CM } from './compiler';

export interface SourceRoom { id: string; name: string; type: string; floor: number; polygon: [number, number][]; outdoor: boolean }
export interface SourceDoor { floor: number; x_mm: number; y_mm: number; width_mm: number; entrance: boolean; kind?: string; height_mm?: number }
export interface SourceWall { floor: number; x1: number; y1: number; x2: number; y2: number; thickness_mm: number; height_mm?: number }
export interface SourceWindow { floor: number; width_mm: number; height_mm: number; kind: string }
export interface RoutingSource {
  schema_version: 'constructos.routing-source.v1';
  editor_project_id: string;
  source_hash: string;
  floors: { index: number; name: string }[];
  rooms: SourceRoom[];
  doors: SourceDoor[];
  walls: SourceWall[];
  /** Cost quantities only (not part of source_hash, which fingerprints routing geometry). */
  windows?: SourceWindow[];
  /** Fingerprint of everything that affects quantities (geometry + openings + wall heights). */
  quantities_hash?: string;
}

const OUR_TYPES = ['master-bedroom', 'guest-room', 'home-office', 'bedroom', 'bathroom', 'toilet', 'kitchen', 'living', 'dining',
  'family', 'study', 'prayer', 'utility', 'store', 'laundry', 'balcony', 'foyer', 'corridor', 'garage', 'hall', 'stair', 'terrace', 'parking'];
const EDITOR_TYPES: Record<string, string> = {
  livingRoom: 'living', bedroom: 'bedroom', kitchen: 'kitchen', bathroom: 'bathroom', diningRoom: 'dining', laundryRoom: 'laundry',
  office: 'study', hallway: 'hall', garage: 'garage', closet: 'store', pantry: 'store', entryway: 'foyer',
};
const NAME_HINTS: [RegExp, string][] = [
  [/master|parent|main bed/i, 'master_bedroom'], [/bed/i, 'bedroom'], [/bath|wash|wc|toilet/i, 'bathroom'], [/kitchen/i, 'kitchen'],
  [/living|drawing|lounge/i, 'living'], [/dining/i, 'dining'], [/stair/i, 'stair'], [/park|garage|car/i, 'garage'],
  [/terrace|balcony|deck/i, 'terrace'], [/hall|corridor|passage|lobby/i, 'hall'], [/foyer|entr/i, 'foyer'], [/study|office/i, 'study'],
];

/** Our space type for an editor room: generated IDs first, then editor metadata, then the name. */
export function roomType(room: Room): string {
  if (room.floorOpening) return 'stair';
  const prefix = OUR_TYPES.find(t => room.id === t || room.id.startsWith(`${t}-`));
  if (prefix) return prefix === 'parking' ? 'garage' : prefix.replace(/-/g, '_');
  if (room.roomType === 'outdoor') return 'terrace';
  if (room.roomType === 'garage') return 'garage';
  const fromDetails = room.details?.roomType ? EDITOR_TYPES[room.details.roomType] : undefined;
  if (fromDetails) return fromDetails;
  return NAME_HINTS.find(([re]) => re.test(room.name))?.[1] ?? 'other';
}

const mm = (cm: number) => Math.round(cm * 10);

type FloorPart = Omit<RoutingSource, 'schema_version' | 'editor_project_id' | 'source_hash' | 'floors' | 'quantities_hash'>;

function floorSource(floor: Floor, index: number): FloorPart {
  const walls = new Map<string, Wall>(floor.walls.map(w => [w.id, w]));
  const rooms: SourceRoom[] = [];
  for (const { room, polygon } of resolveRoomGeometry(floor, floor.rooms)) {
    if (polygon.length < 3) continue;
    const type = roomType(room);
    rooms.push({
      id: room.id, name: room.name || type, type, floor: index, outdoor: type === 'terrace' || room.roomType === 'outdoor',
      polygon: polygon.map(p => [mm(p.x), mm(p.y)] as [number, number]),
    });
  }
  const doors: SourceDoor[] = [];
  for (const d of floor.doors) {
    const w = walls.get(d.wallId);
    if (!w) continue;
    doors.push({
      floor: index, x_mm: mm(w.start.x + (w.end.x - w.start.x) * d.position), y_mm: mm(w.start.y + (w.end.y - w.start.y) * d.position),
      width_mm: mm(d.width), entrance: w.thickness >= EXTERIOR_WALL_CM && d.type !== 'garage',
    });
  }
  const windows: SourceWindow[] = floor.windows.filter(x => walls.has(x.wallId))
    .map(x => ({ floor: index, width_mm: mm(x.width), height_mm: mm(x.height), kind: x.type }));
  return {
    rooms, doors, windows,
    walls: floor.walls.map(w => ({ floor: index, x1: mm(w.start.x), y1: mm(w.start.y), x2: mm(w.end.x), y2: mm(w.end.y), thickness_mm: mm(w.thickness) })),
  };
}

/** Door and wall details that matter for quantities but not for routing (kept out of source_hash). */
function quantityDetails(floor: Floor): { doors: { kind: string; height_mm: number }[]; wallHeights: number[] } {
  const walls = new Map<string, Wall>(floor.walls.map(w => [w.id, w]));
  return {
    doors: floor.doors.filter(d => walls.has(d.wallId)).map(d => ({ kind: d.type, height_mm: mm(d.height) })),
    wallHeights: floor.walls.map(w => mm(w.height)),
  };
}

async function sha256(text: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
  return 'sha256-' + [...bytes].map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function routingSource(project: Project): Promise<RoutingSource> {
  const floors = [...project.floors].sort((a, b) => a.level - b.level);
  const parts = floors.map((f, i) => floorSource(f, i));
  const body = {
    floors: floors.map((f, i) => ({ index: i, name: f.name })),
    rooms: parts.flatMap(p => p.rooms), doors: parts.flatMap(p => p.doors), walls: parts.flatMap(p => p.walls),
  };
  const source_hash = await sha256(JSON.stringify(body));
  const details = floors.map(quantityDetails);
  const windows = parts.flatMap(p => p.windows ?? []);
  const doors = body.doors.map((d, i) => ({ ...d, ...(details.flatMap(x => x.doors)[i] ?? {}) }));
  const walls = body.walls.map((w, i) => ({ ...w, height_mm: details.flatMap(x => x.wallHeights)[i] }));
  const quantities_hash = await sha256(JSON.stringify({ source_hash, windows, doors, walls }));
  return { schema_version: 'constructos.routing-source.v1', editor_project_id: project.id, source_hash, ...body, doors, walls, windows, quantities_hash };
}
