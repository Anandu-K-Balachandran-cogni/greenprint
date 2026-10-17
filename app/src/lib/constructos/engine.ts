// Browser client for the Greenprint engine (via the same-origin /api/engine gateway).

export type Unit = 'mm' | 'cm' | 'm' | 'in' | 'ft';
export type Facing = 'north' | 'south' | 'east' | 'west' | 'north_east' | 'north_west' | 'south_east' | 'south_west';

export const ROOM_TYPES = [
  'bedroom', 'master_bedroom', 'bathroom', 'toilet', 'kitchen', 'living', 'dining', 'family', 'study',
  'prayer', 'utility', 'store', 'laundry', 'balcony', 'foyer', 'corridor', 'garage', 'home_office', 'guest_room', 'other',
] as const;
export type RoomType = (typeof ROOM_TYPES)[number];

export interface BriefRoom {
  id: string;
  type: RoomType;
  name: string;
  floor_index: number | null;
  requirement: 'hard' | 'preferred';
  min_area_m2: number | null;
  attached_bathroom: boolean;
  source: string | null;
}
export interface Statement { statement: string; source: string | null }
export interface Clarification { field: string; question: string; essential: boolean }
export interface Brief {
  schema_version: string;
  original_text: string;
  plot: { width: number | null; depth: number | null; unit: Unit | null; width_mm: number | null; depth_mm: number | null; status: 'stated' | 'assumed' | 'missing' };
  storeys: number | null;
  rooms: BriefRoom[];
  parking_spaces: number;
  open_kitchen: boolean;
  entrance_facing: Facing | null;
  entrance_status: 'stated' | 'assumed' | 'unknown';
  preferences: Statement[];
  assumptions: Statement[];
  clarifications: Clarification[];
  conflicts: Statement[];
  extracted_by: string | null;
}
export interface BriefVersion { version: number; hash: string; confirmed_at: string; brief: Brief }
/** Where the plot is, pinned on Google Maps. */
export interface Site { lat: number; lng: number; address: string; city: string; country: string; pinned_at?: string }

export interface EngineProject {
  id: string;
  name: string;
  created_at: string;
  brief_draft: Brief | null;
  brief: BriefVersion | null;
  brief_version: number;
  site?: Site | null;
  layout_runs: { id: string; created_at: string; brief_version: number; status: RunStatus; statuses: string[]; kind: string; request: string | null; parent: { run_id: string; candidate: number } | null }[];
}
export interface FieldError { field: string; message: string }

export interface FeasibilityCheck { code: string; status: 'pass' | 'warn' | 'fail' | 'info'; title: string; message: string; measured: number | null; limit: number | null; unit: string | null }
export interface SpaceBudget { id: string; name: string; type: string; floor: number; min_m2: number; target_m2: number; min_dims_m: string; source: string }
export interface FloorBudget { floor: number; name: string; min_m2: number; target_m2: number; buildable_m2: number; min_load_pct: number; target_load_pct: number; spaces: SpaceBudget[] }
export interface Feasibility {
  verdict: 'fits' | 'tight' | 'does_not_fit' | 'incomplete';
  headline: string;
  plot: { width_m: number; depth_m: number; area_m2: number } | null;
  setbacks: { front_mm: number; rear_mm: number; side_mm: number } | null;
  buildable: { width_m: number; depth_m: number; area_m2: number } | null;
  compact: boolean;
  floors: FloorBudget[];
  checks: FeasibilityCheck[];
  assumptions: string[];
  note: string;
}

export class EngineError extends Error {
  constructor(public status: number, message: string, public detail: unknown = null) { super(message); }
}

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api/engine/${path}`, {
    method: init?.method ?? (init?.body === undefined ? 'GET' : 'POST'),
    headers: init?.body === undefined ? undefined : { 'content-type': 'application/json' },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail ?? data?.message ?? data;
    const message = typeof detail === 'string' ? detail : detail?.message ?? `Engine request failed (${res.status})`;
    throw new EngineError(res.status, message, detail);
  }
  return data as T;
}

export const engine = {
  health: () => call<{ status: string; ai_configured: boolean; ai_backend: string }>('health'),
  models: () => call<{ reasoning: string; fast: string; source: string }>('ai/models'),
  listProjects: () => call<{ id: string; name: string; created_at: string; brief_version: number }[]>('projects'),
  createProject: (name: string) => call<EngineProject>('projects', { body: { name } }),
  getProject: (id: string) => call<EngineProject>(`projects/${id}`),
  setSite: (id: string, site: Site) => call<Site>(`projects/${id}/site`, { method: 'PUT', body: site }),
  extractBrief: (id: string, text: string) => call<Brief>(`projects/${id}/brief/extract`, { body: { text } }),
  /** Gemini reads hand sketches or plot drawings (plus any typed words, any language) into an English brief. */
  readSketch: (id: string, body: { text?: string; images: { data: string; mime: 'image/jpeg' | 'image/png' | 'image/webp' }[] }) =>
    call<SketchReading>(`projects/${id}/brief/read`, { body }),
  checkBrief: (id: string, brief: Brief) => call<{ brief: Brief; errors: FieldError[]; feasibility: Feasibility }>(`projects/${id}/brief/check`, { body: brief }),
  confirmBrief: (id: string, brief: Brief, expectedVersion: number) =>
    call<BriefVersion>(`projects/${id}/brief/confirm`, { body: { brief, expected_version: expectedVersion } }),
};

export function roomTypeLabel(type: RoomType): string {
  return type.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
}

export function nextRoomId(rooms: BriefRoom[], type: RoomType): string {
  const prefix = type.replace(/_/g, '-');
  let n = 1;
  while (rooms.some(r => r.id === `${prefix}-${n}`)) n++;
  return `${prefix}-${n}`;
}

// --- Layouts -------------------------------------------------------------------

export type SolverStatus = 'OPTIMAL' | 'FEASIBLE' | 'INFEASIBLE' | 'UNKNOWN' | 'MODEL_INVALID';
export type PlanSide = 'left' | 'right' | 'front' | 'rear';

export interface PlacedSpace {
  id: string; type: string; name: string; floor: number;
  x_mm: number; y_mm: number; w_mm: number; h_mm: number; area_m2: number;
  exterior_sides: PlanSide[]; filler: boolean;
}
export interface PlacedPassage { a: string; b: string; kind: 'door' | 'opening' | 'entrance' | 'garage' | 'stair'; floor: number; side: PlanSide | null; overlap_mm: number }
export interface Allocation {
  schema_version: string;
  status: SolverStatus;
  alternative: number;
  seed: number;
  solve_seconds: number;
  objective: number | null;
  grid_mm: number;
  plot: { width_mm: number; depth_mm: number };
  setbacks: { front_mm: number; rear_mm: number; side_mm: number };
  footprint: { x_mm: number; y_mm: number; width_mm: number; depth_mm: number } | null;
  spaces: PlacedSpace[];
  passages: PlacedPassage[];
  assumptions: string[];
  message: string | null;
}
export interface Finding { code: string; severity: 'error' | 'warning'; message: string; object_ids: string[]; measured: number | null; required: number | null }
export interface Evidence { check: string; title: string; status: 'pass' | 'fail' | 'warn'; detail: string }
export interface RoomMetric { id: string; name: string; floor: number; area_m2: number; min_m2: number; target_m2: number; width_m: number; depth_m: number }
export interface LayoutMetrics {
  plot_m2: number; footprint_m2: number; ground_coverage_pct: number; built_up_m2: number; floor_area_ratio: number;
  circulation_pct: number; terrace_m2: number; rooms_at_or_above_target: number; rooms_total: number; rooms: RoomMetric[];
}
export interface CheckReport { status: 'PASS' | 'FAIL'; findings: Finding[]; checked: string[]; evidence: Evidence[]; metrics: LayoutMetrics | null }
export interface Change {
  action: 'set_min_area' | 'set_floor' | 'set_zone' | 'require_exterior' | 'add_room' | 'remove_room' | 'require_adjacent';
  space_id?: string | null; other_space_id?: string | null; value_m2?: number | null; floor?: number | null;
  zone?: 'front' | 'middle' | 'rear' | null; room_type?: string | null; name?: string | null;
}
export interface Claim { statement: string; evidence: string[] }
export interface Review {
  model: string; summary: string; strengths: Claim[]; concerns: (Claim & { severity: 'minor' | 'major' })[];
  suggestions: (Claim & { change: Change | null })[]; rejected_claims: number; rejected_changes: string[];
  facts: { id: string; text: string }[];
}
export interface LayoutCandidate { allocation: Allocation; report: CheckReport | null; review?: Review }
export type RunStatus = 'queued' | 'running' | 'complete' | 'failed' | 'interrupted';
export interface RunJob { queued_at: string; started_at: string | null; finished_at: string | null; heartbeat_at: string; progress: { done: number; total: number }; error: string | null }
export interface LayoutRun {
  id: string; created_at: string; brief_version: number; brief_hash: string;
  status: RunStatus; job: RunJob | null;
  kind?: 'generation' | 'revision'; parent?: { run_id: string; candidate: number };
  request?: string | null; change_descriptions?: string[]; explanation?: string; rejected?: string[]; interpreted_by?: string | null;
  program: { storeys: number; assumptions: string[]; spaces: { id: string; name: string; type: string; floor: number }[] };
  candidates: LayoutCandidate[];
}

export const layouts = {
  generate: (projectId: string, expectedBriefVersion: number, alternatives = 2) =>
    call<LayoutRun>(`projects/${projectId}/layouts`, { body: { expected_brief_version: expectedBriefVersion, alternatives } }),
  get: (projectId: string, runId: string) => call<LayoutRun>(`projects/${projectId}/layouts/${runId}`),
  review: (projectId: string, runId: string, k: number) => call<Review>(`projects/${projectId}/layouts/${runId}/candidates/${k}/review`, { body: {} }),
  revise: (projectId: string, runId: string, k: number, body: { request?: string; changes?: Change[] }) =>
    call<{ status: 'ok'; run: LayoutRun } | { status: 'clarify'; question: string; rejected: string[] }>(
      `projects/${projectId}/layouts/${runId}/candidates/${k}/revise`, { body }),
};

// --- Service diagrams -------------------------------------------------------------

export interface DiagramSummary {
  id: string; kind: 'electrical' | 'plumbing'; created_at: string; editor_project_id: string; source_hash: string;
  status: 'complete' | 'partial'; improvement_pct: number | null; endpoints_connected: number; endpoints_total: number;
}

export const diagrams = {
  list: (projectId: string, editorId: string) => call<DiagramSummary[]>(`projects/${projectId}/diagrams?editor=${encodeURIComponent(editorId)}`),
  get: (projectId: string, diagramId: string) => call<import('./diagramSvg').Diagram>(`projects/${projectId}/diagrams/${diagramId}`),
  electrical: (projectId: string, body: { source: import('./routingSource').RoutingSource; db_room?: string | null; preferences?: string | null }) =>
    call<import('./diagramSvg').Diagram>(`projects/${projectId}/diagrams/electrical`, { body }),
  plumbing: (projectId: string, body: { source: import('./routingSource').RoutingSource; entry_room?: string | null; exit_room?: string | null }) =>
    call<import('./diagramSvg').Diagram>(`projects/${projectId}/diagrams/plumbing`, { body }),
  exportUrl: (projectId: string, diagramId: string, fmt: ExportFormat) => `/api/engine/projects/${projectId}/diagrams/${diagramId}/export/${fmt}`,
};

export type ExportFormat = 'pdf' | 'dxf';

// --- Agents (Neuro-SAN networks) -----------------------------------------------------

export interface SketchReading { english_brief: string; language: string; image_notes: string[]; unclear: string[] }

export interface InteriorBudget {
  tier: string; total: number; subtotal: number; contingency: number; paint_m2: number; notes: string[];
  groups: Record<string, number>; lines: { item: string; group: string; count: number; each: number; amount: number }[];
}
/** An interior design the agent proposed: the brief (re-planned in the browser to preview) and what it found. */
export interface InteriorDesign {
  id: string; created_at: string; editor_project_id: string | null;
  brief: import('./design').DesignBrief;
  scope: { room_ids: string[] | null; furnish: boolean; home_has_pooja: boolean };
  plan: { skipped: { room: string; item: string; reason: string }[]; issues: string[]; added: { id: string; name: string; count: number }[] };
  budget: InteriorBudget;
}

export interface AgentReply {
  answer: string; chat_context: Record<string, unknown> | null; diagrams: Partial<Record<'plumbing' | 'electrical', string>>;
  revision_run_id: string | null; estimate_id: string | null; green_id?: string | null; design?: InteriorDesign | null; tools_called: string[]; seconds: number;
}
export type AgentName = 'constructos' | 'architect' | 'electrical' | 'plumbing' | 'cost' | 'interior' | 'green';

export const agents = {
  chat: (projectId: string, agent: AgentName, body: {
    message: string; source?: import('./routingSource').RoutingSource; layout?: { run_id: string; candidate: number } | null;
    interior?: { floor: import('$lib/models/types').Floor; room_ids: string[] | null; furnish: boolean; home_has_pooja: boolean; editor_project_id: string };
    floors?: import('$lib/models/types').Floor[];
    chat_context?: Record<string, unknown> | null;
  }) => call<AgentReply>(`projects/${projectId}/agents/${agent}/chat`, { body }),
};

export interface RenderResult { id: string; created_at: string; kind: 'room' | 'view'; room: string | null; model: string; image_path: string }

export const renders = {
  create: (projectId: string, body: { image: string; mime: 'image/jpeg'; kind: 'room' | 'view'; room?: string; style?: string; editor_project_id?: string }) =>
    call<RenderResult>(`projects/${projectId}/renders`, { body }),
  list: (projectId: string, editorId: string) => call<RenderResult[]>(`projects/${projectId}/renders?editor=${encodeURIComponent(editorId)}`),
};

export interface GreenResult {
  id: string; created_at: string; editor_project_id: string | null; front: string | null; front_source: 'given' | 'brief' | null;
  climate: { city: string; country: string; zone: string; rainfall_mm: number; sun_hours: number; tariff: number; currency: string;
             hemisphere: string; schemes: string; sources: { title: string; url: string }[]; model: string };
  report: import('./green').GreenReport;
  site?: Site | null;
  solar_site?: ({ available: true; quality: string; imagery_date: string; yield_kwh_per_kwp: number; unshaded_kwh_per_kwp: number; shade_loss_pct: number; source: string })
    | { available: false; reason: string } | null;
}

export const green = {
  check: (projectId: string, body: { floors: import('$lib/models/types').Floor[]; city: string; facing?: string; editor_project_id: string }) =>
    call<GreenResult>(`projects/${projectId}/green`, { body }),
  list: (projectId: string, editorId: string) => call<{ id: string; created_at: string; city: string; score: number }[]>(`projects/${projectId}/green?editor=${encodeURIComponent(editorId)}`),
  get: (projectId: string, id: string) => call<GreenResult>(`projects/${projectId}/green/${id}`),
};

export const designs = {
  list: (projectId: string, editorId: string) =>
    call<{ id: string; created_at: string; title: string | null; total: number }[]>(`projects/${projectId}/designs?editor=${encodeURIComponent(editorId)}`),
  get: (projectId: string, id: string) => call<InteriorDesign>(`projects/${projectId}/designs/${id}`),
};

/** Download a server-rendered drawing (PDF or DXF) with the filename the engine chose. */
export async function downloadExport(path: string, init?: { body: unknown }): Promise<string> {
  const res = await fetch(path.startsWith('/') ? path : `/api/engine/${path}`, init
    ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(init.body) }
    : undefined);
  if (!res.ok) {
    let message = `Export failed (${res.status})`;
    try { const d = (await res.json()).detail; message = typeof d === 'string' ? d : d?.message ?? message; } catch { /* not JSON */ }
    throw new EngineError(res.status, message);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') ?? '')?.[1] ?? 'constructos-export';
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

export interface EstimateLine { group: string; item: string; quantity: number; unit: string; rate: number; amount: number; basis: string }
export interface Estimate {
  id: string; created_at: string; tier: 'basic' | 'standard' | 'premium'; location: 'non_metro' | 'metro'; source_hash: string;
  quantities: { built_up_sqft: number; built_up_m2: number } & Record<string, unknown>;
  lines: EstimateLine[]; groups: Record<string, number>; subtotal: number; contingency: number; total: number; per_sqft: number;
  benchmark: { low: number; high: number; verdict: 'within' | 'below' | 'above'; basis: string }; assumptions: string[];
}
export const estimates = {
  create: (projectId: string, body: { source: import('./routingSource').RoutingSource; tier: Estimate['tier']; location: Estimate['location'] }) =>
    call<Estimate>(`projects/${projectId}/estimates`, { body }),
  list: (projectId: string, editorId: string) => call<{ id: string; created_at: string }[]>(`projects/${projectId}/estimates?editor=${encodeURIComponent(editorId)}`),
  get: (projectId: string, id: string) => call<Estimate>(`projects/${projectId}/estimates/${id}`),
};

export const planExport = (projectId: string, fmt: ExportFormat, source: import('./routingSource').RoutingSource) =>
  downloadExport(`projects/${projectId}/exports/plan/${fmt}`, { body: { source } });

export const ACTIVE_RUN: RunStatus[] = ['queued', 'running'];
