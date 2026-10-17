<script lang="ts">
  import type { Feasibility } from './engine';

  let { report, outdated = false }: { report: Feasibility; outdated?: boolean } = $props();

  const VERDICT = {
    fits: { label: 'Fits', box: 'bg-emerald-50 border-emerald-200 text-emerald-900', chip: 'bg-emerald-600' },
    tight: { label: 'Tight fit', box: 'bg-amber-50 border-amber-200 text-amber-900', chip: 'bg-amber-500' },
    does_not_fit: { label: 'Does not fit', box: 'bg-red-50 border-red-200 text-red-900', chip: 'bg-red-600' },
    incomplete: { label: 'Incomplete', box: 'bg-gray-50 border-gray-200 text-gray-800', chip: 'bg-gray-500' },
  } as const;
  const ICON = { pass: '✓', warn: '!', fail: '✕', info: 'i' } as const;
  const ICON_STYLE = { pass: 'bg-emerald-100 text-emerald-700', warn: 'bg-amber-100 text-amber-800', fail: 'bg-red-100 text-red-700', info: 'bg-slate-100 text-slate-600' } as const;
  const v = $derived(VERDICT[report.verdict]);
  const barColor = (pct: number) => (pct > 100 ? 'bg-red-500' : pct > 85 ? 'bg-amber-500' : 'bg-emerald-500');
</script>

<section class="rounded-xl border p-5 space-y-4 {v.box}" aria-labelledby="feasibility-heading" data-testid="feasibility">
  <div class="flex items-start justify-between gap-3">
    <div>
      <h2 id="feasibility-heading" class="font-semibold">Feasibility</h2>
      <p class="text-sm mt-1" data-testid="feasibility-headline">{report.headline}</p>
    </div>
    <span class="shrink-0 text-xs font-semibold text-white px-2.5 py-1 rounded-full {v.chip}" data-testid="feasibility-verdict">{v.label}</span>
  </div>
  {#if outdated}<p class="text-xs font-medium" role="status">The brief has changed since this assessment. Click “Check brief” to update it.</p>{/if}

  {#if report.buildable && report.plot}
    <dl class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
      <div class="bg-white/70 rounded-lg p-3"><dt class="text-xs text-gray-500">Plot</dt><dd class="font-semibold text-gray-900">{report.plot.width_m.toFixed(1)} × {report.plot.depth_m.toFixed(1)} m · {report.plot.area_m2.toFixed(0)} m²</dd></div>
      <div class="bg-white/70 rounded-lg p-3"><dt class="text-xs text-gray-500">Buildable per floor</dt><dd class="font-semibold text-gray-900" data-testid="buildable">{report.buildable.width_m.toFixed(1)} × {report.buildable.depth_m.toFixed(1)} m · {report.buildable.area_m2.toFixed(0)} m²</dd></div>
      {#if report.setbacks}<div class="bg-white/70 rounded-lg p-3"><dt class="text-xs text-gray-500">Assumed setbacks</dt><dd class="font-semibold text-gray-900">F {report.setbacks.front_mm / 1000} · R {report.setbacks.rear_mm / 1000} · S {report.setbacks.side_mm / 1000} m</dd></div>{/if}
      <div class="bg-white/70 rounded-lg p-3"><dt class="text-xs text-gray-500">Room sizing</dt><dd class="font-semibold text-gray-900">{report.compact ? 'Compact (NBC minimums)' : 'Typical sizes'}</dd></div>
    </dl>

    <div class="space-y-3">
      {#each report.floors as f (f.floor)}
        <div class="bg-white/70 rounded-lg p-3" data-testid="floor-budget-{f.floor}">
          <div class="flex justify-between text-sm"><span class="font-medium text-gray-900">{f.name}</span>
            <span class="text-gray-700">needs {f.min_m2.toFixed(0)} m² minimum · {f.target_m2.toFixed(0)} m² typical · of {f.buildable_m2.toFixed(0)} m²</span></div>
          <div class="mt-2 h-2.5 rounded-full bg-gray-200 overflow-hidden relative" role="img" aria-label="{f.name} uses {f.min_load_pct.toFixed(0)}% of the buildable area at minimum sizes">
            <div class="h-full {barColor(f.min_load_pct)}" style="width: {Math.min(100, f.min_load_pct)}%"></div>
            <div class="absolute top-0 h-full border-r-2 border-slate-700/60" style="left: {Math.min(100, f.target_load_pct)}%" title="Typical sizes"></div>
          </div>
          <p class="text-xs text-gray-600 mt-1">{f.min_load_pct.toFixed(0)}% at minimum sizes · {f.target_load_pct.toFixed(0)}% at typical sizes (marker)</p>
          <details class="mt-2">
            <summary class="text-xs text-gray-700 cursor-pointer">Room budget ({f.spaces.length} spaces)</summary>
            <table class="w-full text-xs mt-2">
              <thead><tr class="text-left text-gray-500"><th class="py-1">Space</th><th>Min. size</th><th>Min. m²</th><th>Typical m²</th><th>Size from</th></tr></thead>
              <tbody>
                {#each f.spaces as s (s.id)}
                  <tr class="border-t border-gray-100"><td class="py-1 text-gray-900">{s.name}</td><td>{s.min_dims_m} m</td><td>{s.min_m2}</td><td>{s.target_m2}</td><td>{s.source}</td></tr>
                {/each}
              </tbody>
            </table>
          </details>
        </div>
      {/each}
    </div>
  {/if}

  {#if report.checks.length}
    <ul class="space-y-2" aria-label="Feasibility checks">
      {#each report.checks as c}
        <li class="flex gap-2 text-sm" data-check={c.code} data-status={c.status}>
          <span class="shrink-0 w-5 h-5 rounded-full text-xs font-bold flex items-center justify-center {ICON_STYLE[c.status]}" aria-label={c.status}>{ICON[c.status]}</span>
          <span><strong class="font-medium text-gray-900">{c.title}.</strong> <span class="text-gray-800">{c.message}</span></span>
        </li>
      {/each}
    </ul>
  {/if}
  <p class="text-xs text-gray-600">{report.note}</p>
</section>
