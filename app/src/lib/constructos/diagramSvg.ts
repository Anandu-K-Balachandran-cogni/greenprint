// Deterministic SVG rendering of a service diagram from its stored coordinates.
// Pure function: the same payload always renders the same markup. Text is escaped
// and no scripts, links or external resources are emitted.

export interface DiagramEndpoint { id: string; kind: string; room_id: string | null; floor: number; x_mm: number; y_mm: number; mount: string; circuit: string | null; label: string }
export interface DiagramRoute { endpoint_id: string; circuit: string; points: [number, number, number][]; length_mm: number; bends: number; transitions: number; j: number; chosen: string }
export interface DiagramTotals { length_mm: number; bends: number; transitions: number; j: number }
export interface Diagram {
  schema_version: string; id: string; kind: 'electrical' | 'plumbing'; editor_project_id: string; source_hash: string; created_at: string;
  policy: { version: string; bend_penalty_mm: number; transition_penalty_mm: number; track_offset_mm: number; algorithm: string };
  floors: { index: number; name: string }[];
  rooms: { id: string; name: string; type: string; floor: number; polygon: [number, number][]; outdoor: boolean }[];
  walls: { floor: number; x1: number; y1: number; x2: number; y2: number; thickness_mm: number }[];
  doors: { floor: number; x_mm: number; y_mm: number; width_mm: number; entrance: boolean }[];
  endpoints: DiagramEndpoint[];
  routes: DiagramRoute[];
  controls: [string, string][];
  circuits: { id: string; label: string; floor: number | null; endpoint_ids: string[] }[];
  metrics: {
    baseline: DiagramTotals; selected: DiagramTotals; improvement_pct: number | null; unique_corridor_mm: number;
    per_connection_mm: number; endpoints_total: number; endpoints_connected: number; penetrations_available: number;
  };
  unresolved: { endpoint_id: string | null; reason: string }[];
  assumptions: string[];
  status: 'complete' | 'partial';
  validation: string[];
  preferences?: { text: string | null; clarification: string | null; rejected: string[] };
  network_metrics?: Record<'supply' | 'drainage', Diagram['metrics']> | null;
  gravity?: 'not_applicable' | 'unverified';
}

/** Line style per circuit family: colour plus a distinct dash, so meaning never relies on colour alone. */
export const CIRCUIT_STYLE: Record<string, { color: string; dash: string; width: number; name: string }> = {
  L: { color: '#d97706', dash: '240 120', width: 30, name: 'Lighting supply (to switchboards)' },
  P: { color: '#dc2626', dash: '', width: 30, name: 'Socket circuits 6 A' },
  K: { color: '#7c3aed', dash: '', width: 45, name: 'Kitchen power 16 A' },
  W: { color: '#0891b2', dash: '240 80 40 80', width: 35, name: 'Water heater 16 A' },
  AC: { color: '#2563eb', dash: '40 80', width: 35, name: 'Air conditioning' },
  U: { color: '#059669', dash: '160 60 40 60 40 60', width: 40, name: 'Utility / EV 16 A' },
  CW: { color: '#2563eb', dash: '', width: 40, name: 'Cold water supply' },
  DR: { color: '#92400e', dash: '200 90', width: 70, name: 'Drainage (arrows: flow towards the exit)' },
};
const family = (circuit: string) => circuit.replace(/\d+$/, '');

const SYMBOL_TEXT: Record<string, string> = {
  socket: '6A', power_socket: '16A', ac_point: 'AC', ev_charger: 'EV', geyser: 'WH', exhaust: 'EF', switchboard: 'S', fan: 'F',
  wc: 'WC', basin: 'WB', shower: 'SH', sink: 'KS', washing_machine: 'WM', tap: 'T', floor_drain: 'FD',
};

type Pt = [number, number, number];

/** Merge consecutive collinear steps of a polyline into [start, end] runs. */
function straightRuns(pts: Pt[]): [Pt, Pt][] {
  const runs: [Pt, Pt][] = [];
  let start = 0;
  for (let i = 1; i < pts.length; i++) {
    const next = pts[i + 1];
    const cross = next ? (pts[i][1] - pts[start][1]) * (next[2] - pts[i][2]) - (pts[i][2] - pts[start][2]) * (next[1] - pts[i][1]) : 1;
    if (Math.abs(cross) < 1) continue; // still straight
    runs.push([pts[start], pts[i]]);
    start = i;
  }
  return runs;
}

function distToSegment(px: number, py: number, w: { x1: number; y1: number; x2: number; y2: number }): number {
  const dx = w.x2 - w.x1, dy = w.y2 - w.y1, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((px - w.x1) * dx + (py - w.y1) * dy) / l2)) : 0;
  return Math.hypot(px - (w.x1 + t * dx), py - (w.y1 + t * dy));
}

export function esc(text: string): string {
  return text.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
}

function symbol(e: DiagramEndpoint): string {
  const { x_mm: x, y_mm: y } = e;
  switch (e.kind) {
    case 'db':
      return `<g data-kind="db"><rect x="${x - 300}" y="${y - 180}" width="600" height="360" fill="#111827"/><text x="${x}" y="${y + 70}" text-anchor="middle" font-size="200" font-weight="700" fill="#fff">DB</text></g>`;
    case 'riser':
      return `<g data-kind="riser"><circle cx="${x}" cy="${y}" r="200" fill="#fff" stroke="#111827" stroke-width="40"/><path d="M${x} ${y + 110} V${y - 110} M${x - 80} ${y - 30} L${x} ${y - 110} L${x + 80} ${y - 30}" stroke="#111827" stroke-width="40" fill="none"/></g>`;
    case 'water_entry':
      return `<g data-kind="water_entry"><path d="M${x} ${y - 260} L${x + 240} ${y + 160} L${x - 240} ${y + 160} Z" fill="#dbeafe" stroke="#1d4ed8" stroke-width="35"/><text x="${x}" y="${y + 100}" text-anchor="middle" font-size="130" font-weight="700" fill="#1d4ed8">WE</text></g>`;
    case 'drain_exit':
      return `<g data-kind="drain_exit"><rect x="${x - 230}" y="${y - 230}" width="460" height="460" fill="#fef3c7" stroke="#92400e" stroke-width="40"/><path d="M${x - 230} ${y - 230} L${x + 230} ${y + 230} M${x + 230} ${y - 230} L${x - 230} ${y + 230}" stroke="#92400e" stroke-width="25"/><text x="${x}" y="${y + 340}" text-anchor="middle" font-size="130" font-weight="700" fill="#92400e">MH</text></g>`;
    case 'stack':
      return `<g data-kind="stack"><circle cx="${x}" cy="${y}" r="230" fill="#fff" stroke="#111827" stroke-width="30"/><circle cx="${x}" cy="${y}" r="160" fill="none" stroke="#111827" stroke-width="30"/><text x="${x}" y="${y + 50}" text-anchor="middle" font-size="130" font-weight="700" fill="#111827">${esc(e.label.replace('Stack ', ''))}</text></g>`;
    case 'light':
      return `<g data-kind="light"><circle cx="${x}" cy="${y}" r="170" fill="#fff" stroke="#111827" stroke-width="30"/><path d="M${x - 120} ${y - 120} L${x + 120} ${y + 120} M${x + 120} ${y - 120} L${x - 120} ${y + 120}" stroke="#111827" stroke-width="30"/></g>`;
    default: {
      const t = SYMBOL_TEXT[e.kind] ?? '?';
      const r = e.kind === 'switchboard' ? 0 : 150;
      const shape = e.kind === 'switchboard'
        ? `<rect x="${x - 150}" y="${y - 150}" width="300" height="300" fill="#fef3c7" stroke="#92400e" stroke-width="30"/>`
        : `<circle cx="${x}" cy="${y}" r="${r + 20}" fill="#fff" stroke="#111827" stroke-width="30"/>`;
      return `<g data-kind="${e.kind}">${shape}<text x="${x}" y="${y + 55}" text-anchor="middle" font-size="${t.length > 2 ? 110 : 150}" font-weight="700" fill="#111827">${t}</text></g>`;
    }
  }
}

export function renderDiagramSvg(d: Diagram, floor: number, opts: { title?: string } = {}): string {
  const rooms = d.rooms.filter(r => r.floor === floor);
  const xs = rooms.flatMap(r => r.polygon.map(p => p[0])), ys = rooms.flatMap(r => r.polygon.map(p => p[1]));
  if (!xs.length) return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>';
  const pad = 1200, legendW = 6800;
  const [x0, y0, x1, y1] = [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
  const w = x1 - x0 + legendW, h = Math.max(y1 - y0, 7600) + 1400;
  const out: string[] = [];
  out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x0} ${y0} ${w} ${h}" width="${Math.round(w / 20)}" height="${Math.round(h / 20)}" font-family="Helvetica, Arial, sans-serif" data-floor="${floor}">`);
  out.push(`<rect x="${x0}" y="${y0}" width="${w}" height="${h}" fill="#ffffff"/>`);
  // Subdued architectural background.
  for (const r of rooms) {
    out.push(`<polygon points="${r.polygon.map(p => p.join(',')).join(' ')}" fill="${r.outdoor ? '#f0fdf4' : '#f8fafc'}" stroke="none"/>`);
  }
  for (const wl of d.walls.filter(x => x.floor === floor)) {
    out.push(`<line x1="${wl.x1}" y1="${wl.y1}" x2="${wl.x2}" y2="${wl.y2}" stroke="#cbd5e1" stroke-width="${Math.max(60, wl.thickness_mm)}" stroke-linecap="square"/>`);
  }
  // Door openings as gaps in their host wall.
  const floorWalls = d.walls.filter(x => x.floor === floor);
  for (const dr of d.doors.filter(x => x.floor === floor)) {
    const host = floorWalls.find(wl => distToSegment(dr.x_mm, dr.y_mm, wl) < 60);
    if (!host) continue;
    const len = Math.hypot(host.x2 - host.x1, host.y2 - host.y1) || 1;
    const [ux, uy] = [(host.x2 - host.x1) / len, (host.y2 - host.y1) / len];
    const half = dr.width_mm / 2;
    out.push(`<line x1="${dr.x_mm - ux * half}" y1="${dr.y_mm - uy * half}" x2="${dr.x_mm + ux * half}" y2="${dr.y_mm + uy * half}" stroke="#ffffff" stroke-width="${Math.max(60, host.thickness_mm) + 20}"/>`);
  }
  for (const r of rooms) {
    const cx = r.polygon.reduce((s, p) => s + p[0], 0) / r.polygon.length, cy = r.polygon.reduce((s, p) => s + p[1], 0) / r.polygon.length;
    const width = Math.max(...r.polygon.map(p => p[0])) - Math.min(...r.polygon.map(p => p[0]));
    const size = Math.min(230, (width * 1.6) / Math.max(4, r.name.length));
    out.push(`<text x="${cx}" y="${cy - 450}" text-anchor="middle" font-size="${Math.round(size)}" fill="#94a3b8">${esc(r.name)}</text>`);
  }
  // Routes: unique segments per circuit, so shared runs are drawn once.
  const byCircuit = new Map<string, Set<string>>();
  for (const route of d.routes) {
    const segs = byCircuit.get(route.circuit) ?? new Set<string>();
    byCircuit.set(route.circuit, segs);
    for (let i = 0; i + 1 < route.points.length; i++) {
      const [a, b] = [route.points[i], route.points[i + 1]];
      if (a[0] !== floor || b[0] !== floor) continue;
      const k = a[1] < b[1] || (a[1] === b[1] && a[2] <= b[2]) ? `${a[1]},${a[2]},${b[1]},${b[2]}` : `${b[1]},${b[2]},${a[1]},${a[2]}`;
      segs.add(k);
    }
  }
  for (const [circuit, segs] of byCircuit) {
    const st = CIRCUIT_STYLE[family(circuit)] ?? CIRCUIT_STYLE.P;
    const path = [...segs].map(s => { const [ax, ay, bx, by] = s.split(','); return `M${ax} ${ay}L${bx} ${by}`; }).join('');
    // Supply shares tracks with drainage; draw it slightly offset so neither hides the other.
    const offset = family(circuit) === 'CW' ? ' transform="translate(70 70)"' : '';
    if (path) out.push(`<path data-circuit="${esc(circuit)}" d="${path}"${offset} fill="none" stroke="${st.color}" stroke-width="${st.width}" stroke-dasharray="${st.dash}" stroke-linecap="round"/>`);
  }
  // Drainage direction: one arrowhead in the middle of each straight run, pointing downstream.
  const arrowed = new Set<string>();
  for (const route of d.routes.filter(r => family(r.circuit) === 'DR')) {
    for (const [a, b] of straightRuns(route.points.filter(p => p[0] === floor))) {
      const len = Math.hypot(b[1] - a[1], b[2] - a[2]);
      const key = `${a[1]},${a[2]},${b[1]},${b[2]}`;
      if (len < 900 || arrowed.has(key)) continue;
      arrowed.add(key);
      const [ux, uy] = [(b[1] - a[1]) / len, (b[2] - a[2]) / len];
      const [mx, my] = [(a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
      const tip = [mx + ux * 140, my + uy * 140], l = [mx - ux * 120 - uy * 110, my - uy * 120 + ux * 110], r = [mx - ux * 120 + uy * 110, my - uy * 120 - ux * 110];
      out.push(`<path data-flow="" d="M${l[0]} ${l[1]}L${tip[0]} ${tip[1]}L${r[0]} ${r[1]}Z" fill="#92400e"/>`);
    }
  }
  // Control associations (switch → light): thin dashed arcs, not conductor routes.
  const byId = new Map(d.endpoints.map(e => [e.id, e]));
  for (const [s, t] of d.controls) {
    const a = byId.get(s), b = byId.get(t);
    if (!a || !b || a.floor !== floor) continue;
    const mx = (a.x_mm + b.x_mm) / 2 + (b.y_mm - a.y_mm) * 0.2, my = (a.y_mm + b.y_mm) / 2 - (b.x_mm - a.x_mm) * 0.2;
    out.push(`<path data-control="${esc(s)}|${esc(t)}" d="M${a.x_mm} ${a.y_mm}Q${mx} ${my} ${b.x_mm} ${b.y_mm}" fill="none" stroke="#64748b" stroke-width="15" stroke-dasharray="60 60"/>`);
  }
  for (const e of d.endpoints.filter(x => x.floor === floor)) out.push(symbol(e));

  // Legend and title block.
  const lx = x1 + 300, ly = y0 + 300;
  out.push(`<g data-legend=""><rect x="${lx}" y="${ly}" width="${legendW - 600}" height="${h - 900}" fill="#fff" stroke="#cbd5e1" stroke-width="20"/>`);
  out.push(`<text x="${lx + 250}" y="${ly + 450}" font-size="260" font-weight="700" fill="#111827">Legend</text>`);
  let yy = ly + 850;
  const used = new Set(d.routes.map(r => family(r.circuit)));
  for (const [fam, st] of Object.entries(CIRCUIT_STYLE)) {
    if (!used.has(fam)) continue;
    out.push(`<line x1="${lx + 250}" y1="${yy - 70}" x2="${lx + 1050}" y2="${yy - 70}" stroke="${st.color}" stroke-width="${st.width + 10}" stroke-dasharray="${st.dash}"/>`);
    out.push(`<text x="${lx + 1200}" y="${yy}" font-size="190" fill="#111827">${esc(st.name)}</text>`);
    yy += 380;
  }
  if (d.controls.length) {
    out.push(`<path d="M${lx + 250} ${yy - 70}Q${lx + 650} ${yy - 250} ${lx + 1050} ${yy - 70}" stroke="#64748b" stroke-width="15" stroke-dasharray="60 60" fill="none"/><text x="${lx + 1200}" y="${yy}" font-size="190" fill="#111827">Switch control (not a cable run)</text>`);
    yy += 480;
  }
  for (const kind of ['db', 'riser', 'switchboard', 'light', 'fan', 'socket', 'power_socket', 'exhaust', 'geyser', 'ac_point', 'ev_charger',
    'water_entry', 'drain_exit', 'stack', 'wc', 'basin', 'shower', 'sink', 'washing_machine', 'tap', 'floor_drain']) {
    const sample = d.endpoints.find(e => e.kind === kind);
    if (!sample) continue;
    out.push(symbol({ ...sample, x_mm: lx + 650, y_mm: yy - 70 }));
    out.push(`<text x="${lx + 1200}" y="${yy}" font-size="190" fill="#111827">${esc(sample.kind === 'switchboard' ? 'Switchboard' : sample.label.replace(/ — .*/, ''))}</text>`);
    yy += 420;
  }
  out.push(`</g>`);
  const floorName = d.floors.find(f => f.index === floor)?.name ?? `Floor ${floor}`;
  const title = opts.title ?? `${d.kind === 'electrical' ? 'Electrical' : 'Plumbing'} layout — ${floorName}`;
  out.push(`<text x="${x0 + 300}" y="${y0 + h - 650}" font-size="300" font-weight="700" fill="#111827">${esc(title)}</text>`);
  out.push(`<text x="${x0 + 300}" y="${y0 + h - 300}" font-size="180" fill="#475569">Source ${esc(d.source_hash.slice(0, 19))}… · ${esc(d.created_at.slice(0, 16).replace('T', ' '))} UTC · indicative planning diagram, not an installation drawing${d.gravity === 'unverified' ? ' · gravity falls not verified' : ''} · units mm</text>`);
  out.push('</svg>');
  return out.join('');
}
