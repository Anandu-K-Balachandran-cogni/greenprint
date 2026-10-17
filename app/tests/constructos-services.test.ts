import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { compileAllocation } from '$lib/constructos/compiler';
import type { Allocation } from '$lib/constructos/engine';
import { renderDiagramSvg, type Diagram } from '$lib/constructos/diagramSvg';
import { roomType, routingSource } from '$lib/constructos/routingSource';

const twoStorey: Allocation = JSON.parse(readFileSync(new URL('./fixtures/constructos/two-storey-allocation.json', import.meta.url), 'utf8'));
const meta = { projectId: 'cos-test', name: 'Test', link: { engineProjectId: 'e', layoutRunId: 'r', candidate: 0, briefVersion: 1, briefHash: 'h' } };

describe('routing source from the saved editor model', () => {
  it('keeps room identities and types, in millimetres, with doors and walls', async () => {
    const { project } = compileAllocation(twoStorey, meta, new Date('2026-10-12T00:00:00Z'));
    const src = await routingSource(project);
    expect(src.floors.map(f => f.name)).toEqual(['Ground floor', 'First floor']);
    // Neighbouring open terraces have no wall between them, so the editor sees one terrace.
    const roofed = (ids: string[]) => ids.filter(id => !id.includes('terrace')).sort();
    expect(roofed(src.rooms.map(r => `${r.floor}:${r.id}`))).toEqual(roofed(twoStorey.spaces.map(s => `${s.floor}:${s.id}`)));
    expect(src.rooms.some(r => r.type === 'terrace')).toBe(true);
    const types = Object.fromEntries(src.rooms.map(r => [r.id, r.type]));
    expect(types).toMatchObject({ 'master-bedroom-1': 'master_bedroom', 'parking-1': 'garage', 'stair-1': 'stair', 'kitchen-1': 'kitchen' });
    expect(src.rooms.filter(r => r.outdoor).every(r => r.type === 'terrace')).toBe(true);
    const parking = twoStorey.spaces.find(s => s.id === 'parking-1')!;
    const poly = src.rooms.find(r => r.id === 'parking-1')!.polygon;
    const xs = poly.map(p => p[0]);
    expect(Math.max(...xs) - Math.min(...xs)).toBe(parking.w_mm);
    expect(src.doors.length).toBe(project.floors.reduce((n, f) => n + f.doors.length, 0));
    expect(src.doors.filter(d => d.entrance).length).toBeGreaterThan(0);
    expect(src.source_hash).toMatch(/^sha256-[0-9a-f]{64}$/);
  });

  it('fingerprint is stable for the same plan and changes when a wall moves', async () => {
    const { project } = compileAllocation(twoStorey, meta, new Date('2026-10-12T00:00:00Z'));
    const a = await routingSource(project);
    const b = await routingSource(structuredClone(project));
    expect(b.source_hash).toBe(a.source_hash);
    const moved = structuredClone(project);
    moved.floors[0].walls[3].start.x += 30;
    moved.floors[0].walls[3].end.x += 30;
    expect((await routingSource(moved)).source_hash).not.toBe(a.source_hash);
  });

  it('infers types for manually drawn rooms from editor metadata or the name', () => {
    expect(roomType({ id: 'room-3-123', name: 'Guest Bedroom', walls: [], floorTexture: '', area: 1 })).toBe('bedroom');
    expect(roomType({ id: 'room-4', name: 'Room 4', walls: [], floorTexture: '', area: 1, details: { roomType: 'kitchen' } })).toBe('kitchen');
    expect(roomType({ id: 'room-5', name: 'Room 5', walls: [], floorTexture: '', area: 1 })).toBe('other');
  });
});

describe('diagram SVG', () => {
  const diagram = (): Diagram => ({
    schema_version: 'constructos.diagram.v1', id: 'd1', kind: 'electrical', editor_project_id: 'x', source_hash: 'sha256-abcdef0123456789abcdef', created_at: '2026-10-12T10:00:00Z',
    policy: { version: 'routing.policy.v1', bend_penalty_mm: 500, transition_penalty_mm: 2000, track_offset_mm: 150, algorithm: '' },
    floors: [{ index: 0, name: 'Ground floor' }],
    rooms: [{ id: 'r1', name: 'Kitchen <script>alert(1)</script>', type: 'kitchen', floor: 0, polygon: [[0, 0], [3000, 0], [3000, 3000], [0, 3000]], outdoor: false }],
    walls: [{ floor: 0, x1: 0, y1: 0, x2: 3000, y2: 0, thickness_mm: 230 }], doors: [],
    endpoints: [
      { id: 'e1', kind: 'db', room_id: 'r1', floor: 0, x_mm: 150, y_mm: 1000, mount: 'wall', circuit: null, label: 'Distribution board' },
      { id: 'e2', kind: 'power_socket', room_id: 'r1', floor: 0, x_mm: 2850, y_mm: 1000, mount: 'wall', circuit: 'K', label: 'Power point 16 A' },
      { id: 'e3', kind: 'switchboard', room_id: 'r1', floor: 0, x_mm: 150, y_mm: 2000, mount: 'wall', circuit: 'L0', label: 'Switchboard' },
      { id: 'e4', kind: 'light', room_id: 'r1', floor: 0, x_mm: 1500, y_mm: 1500, mount: 'ceiling', circuit: 'L0', label: 'Light' },
    ],
    routes: [{ endpoint_id: 'e2', circuit: 'K', points: [[0, 150, 1000], [0, 150, 150], [0, 2850, 150], [0, 2850, 1000]], length_mm: 4250, bends: 2, transitions: 0, j: 5250, chosen: 'optimised' }],
    controls: [['e3', 'e4']], circuits: [{ id: 'K', label: 'Kitchen power 16 A', floor: null, endpoint_ids: ['e2'] }],
    metrics: { baseline: { length_mm: 4250, bends: 2, transitions: 0, j: 5250 }, selected: { length_mm: 4250, bends: 2, transitions: 0, j: 5250 }, improvement_pct: 0, unique_corridor_mm: 4250, per_connection_mm: 4250, endpoints_total: 2, endpoints_connected: 2, penetrations_available: 0 },
    unresolved: [], assumptions: [], status: 'complete', validation: [],
  });

  it('draws symbols, distinct route and control styles, legend and source, and escapes text', () => {
    const svg = renderDiagramSvg(diagram(), 0);
    expect(svg).not.toMatch(/<script|onload=|href=/i);
    expect(svg).toContain('Kitchen &lt;script&gt;');
    expect(svg).toContain('data-kind="db"');
    expect(svg).toContain('data-circuit="K"');
    expect(svg).toMatch(/data-control="e3\|e4"[^>]*stroke-dasharray="60 60"/);
    expect(svg).toContain('M150 150L150 1000'); // segments are stored in canonical order
    expect(svg).toContain('Switch control (not a cable run)');
    expect(svg).toContain('Source sha256-abcdef012345…');
    expect(renderDiagramSvg(diagram(), 0)).toBe(svg);
  });
});

describe('drainage arrows', () => {
  it('marks each long straight run once, pointing downstream', async () => {
    const { renderDiagramSvg } = await import('$lib/constructos/diagramSvg');
    const pts: [number, number, number][] = [[0, 0, 0], [0, 300, 0], [0, 600, 0], [0, 900, 0], [0, 1200, 0], [0, 1200, 300], [0, 1200, 600], [0, 1200, 900], [0, 1200, 1200]];
    const d = {
      schema_version: 'x', id: 'd', kind: 'plumbing', editor_project_id: 'x', source_hash: 'sha256-0', created_at: '2026-10-12T00:00:00Z',
      policy: { version: 'v', bend_penalty_mm: 500, transition_penalty_mm: 2000, track_offset_mm: 150, algorithm: '' },
      floors: [{ index: 0, name: 'G' }], rooms: [{ id: 'r', name: 'R', type: 'bathroom', floor: 0, polygon: [[0, 0], [2000, 0], [2000, 2000], [0, 2000]], outdoor: false }],
      walls: [], doors: [], endpoints: [], controls: [], circuits: [], unresolved: [], assumptions: [], status: 'complete', validation: [],
      routes: [{ endpoint_id: 'e', circuit: 'DR0', points: pts, length_mm: 2400, bends: 1, transitions: 0, j: 2900, chosen: 'optimised' }],
      metrics: { baseline: { length_mm: 0, bends: 0, transitions: 0, j: 0 }, selected: { length_mm: 0, bends: 0, transitions: 0, j: 0 }, improvement_pct: null, unique_corridor_mm: 0, per_connection_mm: 0, endpoints_total: 0, endpoints_connected: 0, penetrations_available: 0 },
    } as const;
    const svg = renderDiagramSvg(d as never, 0);
    const arrows = [...svg.matchAll(/data-flow="" d="M([\d.-]+) ([\d.-]+)L([\d.-]+) ([\d.-]+)/g)];
    expect(arrows).toHaveLength(2);
    // first run goes +x: the tip (3rd/4th numbers) lies to the right of the tail
    expect(Number(arrows[0][3])).toBeGreaterThan(Number(arrows[0][1]));
    // second run goes +y
    expect(Number(arrows[1][4])).toBeGreaterThan(Number(arrows[1][2]));
  });
});
