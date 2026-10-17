<script lang="ts">
  import type { Allocation } from './engine';

  let { allocation, floor = 0, label = 'Layout preview' }: { allocation: Allocation; floor?: number; label?: string } = $props();

  const COLORS: Record<string, string> = {
    living: '#fde68a', family: '#fde68a', dining: '#fed7aa', kitchen: '#fdba74', foyer: '#e5e7eb', hall: '#e5e7eb',
    corridor: '#e5e7eb', master_bedroom: '#bfdbfe', bedroom: '#c7d2fe', guest_room: '#c7d2fe', bathroom: '#a5f3fc',
    toilet: '#a5f3fc', garage: '#d1d5db', stair: '#ddd6fe', terrace: '#dcfce7', study: '#fbcfe8', home_office: '#fbcfe8',
  };

  const D = $derived(allocation.plot.depth_mm);
  const spaces = $derived(allocation.spaces.filter(s => s.floor === floor));
  const doors = $derived(allocation.passages.filter(p => p.floor === floor));
  const byId = $derived(new Map(spaces.map(s => [s.id, s])));
  const pad = 600;

  // Mark each passage at the middle of the shared edge (plan frame: road at the bottom).
  function doorMark(a: string, b: string): { x: number; y: number } | null {
    const child = byId.get(b);
    if (!child) return null;
    if (a === 'exterior') return { x: child.x_mm + child.w_mm / 2, y: D - child.y_mm };
    const p = byId.get(a);
    if (!p) return null;
    const [ax0, ax1, ay0, ay1] = [p.x_mm, p.x_mm + p.w_mm, D - p.y_mm - p.h_mm, D - p.y_mm];
    const [bx0, bx1, by0, by1] = [child.x_mm, child.x_mm + child.w_mm, D - child.y_mm - child.h_mm, D - child.y_mm];
    if (ax1 === bx0 || bx1 === ax0) return { x: ax1 === bx0 ? ax1 : ax0, y: (Math.max(ay0, by0) + Math.min(ay1, by1)) / 2 };
    if (ay1 === by0 || by1 === ay0) return { x: (Math.max(ax0, bx0) + Math.min(ax1, bx1)) / 2, y: ay1 === by0 ? ay1 : ay0 };
    return null;
  }
</script>

<svg viewBox="{-pad} {-pad} {allocation.plot.width_mm + 2 * pad} {D + 2 * pad}" role="img" aria-label={label} class="w-full h-auto bg-white">
  <rect x="0" y="0" width={allocation.plot.width_mm} height={D} fill="#f8fafc" stroke="#94a3b8" stroke-width="40" stroke-dasharray="200 120" />
  <text x={allocation.plot.width_mm / 2} y={D + 450} text-anchor="middle" font-size="320" fill="#64748b">Road / front</text>
  {#each spaces as s (s.id)}
    {@const y = D - s.y_mm - s.h_mm}
    <g data-space-id={s.id}>
      <rect x={s.x_mm} {y} width={s.w_mm} height={s.h_mm} fill={COLORS[s.type] ?? '#f1f5f9'} stroke="#1e293b" stroke-width={s.type === 'terrace' ? 20 : 60} />
      <text x={s.x_mm + s.w_mm / 2} y={y + s.h_mm / 2 - 60} text-anchor="middle" font-size={Math.min(380, s.w_mm / 6, (s.w_mm * 1.7) / Math.max(4, s.name.length))} fill="#0f172a" font-weight="600">{s.name}</text>
      <text x={s.x_mm + s.w_mm / 2} y={y + s.h_mm / 2 + 320} text-anchor="middle" font-size={Math.min(300, s.w_mm / 7)} fill="#334155">{s.area_m2.toFixed(1)} m²</text>
    </g>
  {/each}
  {#each doors as p}
    {@const m = doorMark(p.a, p.b)}
    {#if m}<circle cx={m.x} cy={m.y} r={p.kind === 'garage' ? 220 : 140} fill={p.kind === 'opening' ? '#16a34a' : p.kind === 'entrance' ? '#dc2626' : '#2563eb'} />{/if}
  {/each}
</svg>
