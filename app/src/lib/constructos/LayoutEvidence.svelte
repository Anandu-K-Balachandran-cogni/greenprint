<script lang="ts">
  import type { CheckReport } from './engine';

  let { report }: { report: CheckReport } = $props();
  const ICON = { pass: '✓', warn: '!', fail: '✕' } as const;
  const STYLE = { pass: 'bg-emerald-100 text-emerald-700', warn: 'bg-amber-100 text-amber-800', fail: 'bg-red-100 text-red-700' } as const;
  const m = $derived(report.metrics);
  const floorLabel = (f: number) => (f === 0 ? 'G' : `F${f}`);
</script>

<details class="rounded-lg border border-gray-200 p-3" open={report.status === 'FAIL'} data-testid="layout-evidence">
  <summary class="text-sm font-medium text-gray-900 cursor-pointer">
    {report.status === 'PASS' ? 'Why this design works' : 'Why this design fails'} · {report.evidence.filter(e => e.status !== 'fail').length}/{report.evidence.length} checks
  </summary>
  {#if m}
    <dl class="grid grid-cols-3 gap-2 mt-3 text-xs" aria-label="Design metrics">
      <div class="bg-gray-50 rounded p-2"><dt class="text-gray-500">Built-up area</dt><dd class="font-semibold text-gray-900" data-testid="metric-built-up">{m.built_up_m2.toFixed(1)} m²</dd></div>
      <div class="bg-gray-50 rounded p-2"><dt class="text-gray-500">Ground coverage</dt><dd class="font-semibold text-gray-900" data-testid="metric-coverage">{m.ground_coverage_pct.toFixed(0)}% of plot</dd></div>
      <div class="bg-gray-50 rounded p-2"><dt class="text-gray-500" title="Built-up area ÷ plot area">Floor area ratio</dt><dd class="font-semibold text-gray-900">{m.floor_area_ratio.toFixed(2)}</dd></div>
      <div class="bg-gray-50 rounded p-2"><dt class="text-gray-500">Circulation</dt><dd class="font-semibold text-gray-900">{m.circulation_pct.toFixed(0)}% of built-up</dd></div>
      <div class="bg-gray-50 rounded p-2"><dt class="text-gray-500">Open terrace</dt><dd class="font-semibold text-gray-900">{m.terrace_m2.toFixed(1)} m²</dd></div>
      <div class="bg-gray-50 rounded p-2"><dt class="text-gray-500">Rooms at typical size</dt><dd class="font-semibold text-gray-900">{m.rooms_at_or_above_target} of {m.rooms_total}</dd></div>
    </dl>
  {/if}
  <ul class="mt-3 space-y-1.5" aria-label="Measured checks">
    {#each report.evidence as e (e.check)}
      <li class="flex gap-2 text-xs" data-check={e.check} data-status={e.status}>
        <span class="shrink-0 w-4 h-4 rounded-full font-bold flex items-center justify-center text-[10px] {STYLE[e.status]}" aria-label={e.status}>{ICON[e.status]}</span>
        <span><strong class="font-medium text-gray-900">{e.title}.</strong> <span class="text-gray-700">{e.detail}</span></span>
      </li>
    {/each}
  </ul>
  {#if m?.rooms.length}
    <table class="w-full text-xs mt-3">
      <thead><tr class="text-left text-gray-500"><th class="py-1">Room</th><th>Size</th><th>Area</th><th>Min.</th><th>Typical</th></tr></thead>
      <tbody>
        {#each m.rooms as r (`${r.floor}-${r.id}`)}
          <tr class="border-t border-gray-100">
            <td class="py-1 text-gray-900">{floorLabel(r.floor)} · {r.name}</td><td>{r.width_m.toFixed(1)} × {r.depth_m.toFixed(1)} m</td>
            <td class="font-medium {r.area_m2 + 1e-6 >= r.target_m2 * 0.95 ? 'text-emerald-700' : 'text-gray-900'}">{r.area_m2.toFixed(1)} m²</td>
            <td>{r.min_m2}</td><td>{r.target_m2}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</details>
