<script lang="ts">
  // Green performance of the saved plan for its city: climate facts found by Gemini with Google Search
  // (with sources), a score, per-room daylight and ventilation with window fixes that can be applied,
  // and rainwater, rooftop solar and embodied-carbon estimates.
  import { engine, green, EngineError, type GreenResult, type Site } from './engine';
  import PlotPin from './PlotPin.svelte';
  import { applyWindowFixes, COMPASS, type WindowFix } from './green';
  import type { CompiledProject } from './compiler';
  import { localStore } from '$lib/services/datastore';

  let { projectId, editorId, onPlanChanged = () => {}, greenId = null }: {
    projectId: string; editorId: string; onPlanChanged?: () => void; greenId?: string | null;
  } = $props();

  let city = $state('');
  let facing = $state('');
  let busy = $state(false);
  let applying = $state(false);
  let error = $state<string | null>(null);
  let result = $state<GreenResult | null>(null);
  let applied = $state(0);
  let site = $state<Site | null>(null);
  let siteLoaded = $state(false);
  let showMap = $state(false);
  let coverageInfo = $state(false);
  const siteCity = (s: Site) => [s.city, s.country].filter(Boolean).join(', ');

  $effect(() => {
    void engine.getProject(projectId).then(p => {
      site = p.site ?? null; siteLoaded = true;
      if (site && !city) city = siteCity(site);
    }).catch(() => { siteLoaded = true; });
  });

  const label = (s: string) => s.replace(/_/g, '-');
  // The typed city (or the pin's) and the place the search resolved, compared loosely.
  const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[^a-z]/g, '');
  const sameCity = (typed: string, found: string) => !typed.trim() || norm(typed).includes(norm(found)) || norm(found).includes(norm(typed.split(',')[0]));
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const num = (v: number) => v.toLocaleString('en-IN');
  const fixes = $derived(result ? result.report.rooms.flatMap(r => (r.fix ? [r.fix] : [])) as WindowFix[] : []);
  const fixText = (f: WindowFix) => f.kind === 'widen' ? `widen the window ${f.from_cm} → ${f.to_cm} cm`
    : `add a ${f.width_cm} × ${f.height_cm} cm window${f.facing ? ` on the ${label(f.facing)} wall` : ''}`;

  async function load(id?: string | null) {
    const list = await green.list(projectId, editorId);
    const pick = id ?? list.at(-1)?.id;
    if (pick) { result = await green.get(projectId, pick); city = result.climate.city; }
  }
  $effect(() => { void load(greenId).catch(() => { result = null; }); });

  async function run() {
    if (!city.trim() || busy) return;
    busy = true; error = null;
    try {
      const project = await localStore.load(editorId) as CompiledProject;
      result = await green.check(projectId, { floors: project.floors, city: city.trim(), facing: facing || undefined, editor_project_id: editorId });
    } catch (e) {
      error = e instanceof EngineError ? e.message : 'The green check failed. Please try again.';
    } finally { busy = false; }
  }

  // Apply the suggested window changes to the saved plan, then check again.
  async function applyFixes() {
    if (!fixes.length || applying) return;
    applying = true; error = null;
    try {
      const project = await localStore.load(editorId) as CompiledProject;
      await localStore.save({ ...project, floors: applyWindowFixes(project.floors, fixes), updatedAt: new Date() });
      applied = fixes.length;
      onPlanChanged();
      await run();
    } catch (e) {
      error = e instanceof EngineError ? e.message : 'Could not change the plan.';
    } finally { applying = false; }
  }
</script>

<section class="bg-white rounded-xl border border-gray-200 p-4 space-y-4 min-w-0" aria-label="Green performance">
  <div class="rounded-lg border border-emerald-200 bg-emerald-50/40 p-3 space-y-2">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="text-sm font-medium text-gray-800">Where is the plot?
        {#if site}<span class="ml-1 text-xs font-normal text-emerald-800" data-testid="site-summary">📍 {site.address || siteCity(site)}</span>{/if}</p>
      <button type="button" class="px-3 py-1 rounded-lg border border-emerald-600 text-emerald-700 text-xs font-medium" onclick={() => { showMap = !showMap; }}>
        {showMap ? 'Hide map' : site ? 'Move the pin' : 'Pin your plot on the map'}</button>
    </div>
    {#if showMap && siteLoaded}
      <PlotPin {projectId} {site} onPinned={(s) => { site = s; city = siteCity(s); showMap = false; }} />
    {/if}
    <p class="text-[11px] text-gray-500 flex items-center gap-1" data-testid="solar-coverage-note">Sun and shade on your plot are measured in selected areas.
      <button type="button" class="w-4 h-4 rounded-full border border-gray-400 text-[10px] leading-none text-gray-600 hover:bg-gray-100"
        aria-label="Where is this available?" aria-expanded={coverageInfo} onclick={() => { coverageInfo = !coverageInfo; }}>i</button></p>
    {#if coverageInfo}
      <p class="text-[11px] text-gray-600 bg-white border border-gray-200 rounded p-2" data-testid="solar-coverage-info">
        Available across most of India, and in many cities in Asia-Pacific, Europe and the Americas. Not yet in places such as Sri Lanka,
        Bangladesh, Nepal, Malaysia, Vietnam or South Korea. Where it is not available, your city's climate is used instead.</p>
    {/if}
  </div>
  <div class="flex flex-wrap items-end gap-3">
    <label class="text-xs font-medium text-gray-700">City
      <input class="mt-1 block rounded-lg border border-gray-300 px-2 py-1.5 text-sm w-48" bind:value={city} placeholder="e.g. Chennai, Pune, Singapore"
        aria-label="City" onkeydown={(e) => { if (e.key === 'Enter') run(); }} /></label>
    <label class="text-xs font-medium text-gray-700">Entrance faces
      <select class="mt-1 block rounded-lg border border-gray-300 px-2 py-1.5 text-sm" bind:value={facing} aria-label="Entrance faces">
        <option value="">As in the brief</option>
        {#each COMPASS as c}<option value={c}>{label(c)}</option>{/each}
      </select></label>
    <button type="button" class="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50" disabled={busy || !city.trim()} onclick={run}>
      {busy ? 'Checking…' : result ? 'Check again' : 'Check green performance'}</button>
  </div>
  {#if busy}<p class="text-xs text-gray-500" role="status">Looking up {city}'s climate and checking every room…</p>{/if}
  {#if error}<p role="alert" class="text-sm text-red-700">{error}</p>{/if}

  {#if result}
    {@const r = result.report}
    {@const c = result.climate}
    <div class="grid gap-3 sm:grid-cols-3">
      <div class="rounded-lg bg-emerald-700 text-white p-3">
        <p class="text-xs text-white/70">Green score</p>
        <p class="text-3xl font-bold" data-testid="green-score">{r.score.total}<span class="text-base font-normal text-white/70"> / 100</span></p>
        {#if r.score.after_fixes > r.score.total}<p class="text-xs text-white/80">{r.score.after_fixes} with the suggested fixes</p>{/if}
      </div>
      <div class="rounded-lg border p-3 sm:col-span-2 text-xs text-gray-700 space-y-1" data-testid="green-climate">
        <p class="text-sm font-semibold text-gray-900">{c.city}, {c.country} · {label(c.zone)} climate</p>
        <p>Rain {num(c.rainfall_mm)} mm a year · sun {c.sun_hours} kWh/m²/day · electricity {c.tariff} {c.currency}/kWh{result.front ? ` · entrance faces ${label(result.front)}` : ''}</p>
        {#if c.schemes && !/none found/i.test(c.schemes)}<p class="text-emerald-800">{c.schemes}</p>{/if}
        {#if c.sources.length}
          <p class="text-gray-500">Sources:
            {#each c.sources as s, i}<a href={s.url} target="_blank" rel="noopener" class="underline">{s.title}</a>{i < c.sources.length - 1 ? ', ' : ''}{/each}</p>
        {:else}
          <p class="text-amber-700">No sources found for this place; treat these figures as approximate.</p>
        {/if}
        {#if !sameCity(city, c.city)}<p class="text-amber-700" data-testid="closest-match">Closest match found: {c.city}, {c.country}. Check the city name if this is not your plot's city.</p>{/if}
      </div>
    </div>

    <ul class="grid sm:grid-cols-2 gap-x-6 gap-y-1 text-xs" data-testid="green-parts">
      {#each r.score.parts as p}
        <li>
          <div class="flex justify-between"><span class="font-medium">{p.name}</span><span>{p.points} / {p.max}</span></div>
          <div class="h-1.5 rounded bg-gray-100"><div class="h-1.5 rounded bg-emerald-500" style="width:{(p.points / p.max) * 100}%"></div></div>
          <p class="text-gray-500">{p.note}</p>
        </li>
      {/each}
    </ul>

    <table class="w-full text-xs" aria-label="Rooms">
      <thead><tr class="text-left text-gray-500"><th class="py-1">Room</th><th>Window / floor</th><th>Daylight</th><th>Cross-breeze</th><th>Windows face</th><th>Suggestion</th></tr></thead>
      <tbody>
        {#each r.rooms as room}
          <tr class="border-t border-gray-100 align-top">
            <td class="py-1 font-medium">{room.name}</td>
            <td>{pct(room.wfr)} <span class="text-gray-400">(needs {pct(room.required_wfr)})</span></td>
            <td>{room.daylight_ok ? '✓' : '✗'}</td>
            <td>{room.cross_ventilation ? '✓' : '✗'}</td>
            <td>{room.window_facings.map(label).join(', ') || '—'}</td>
            <td class="text-gray-700">{#if room.fix}{fixText(room.fix)} → {pct(room.wfr_after_fix ?? room.wfr)}{:else if !room.daylight_ok}the walls leave no room for a bigger window; light colours and a glazed door help{/if}
              {#each room.shading as s}<span class="block text-amber-700">{s}</span>{/each}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if applied}
      <p class="text-xs text-emerald-700" data-testid="green-applied">Applied {applied} window change{applied > 1 ? 's' : ''} to the plan and checked again.</p>
    {/if}
    {#if fixes.length}
      <div class="flex flex-wrap items-center gap-3">
        <button type="button" class="px-3 py-1.5 rounded-lg border border-emerald-600 text-emerald-700 text-sm font-medium disabled:opacity-50"
          disabled={applying || busy} onclick={applyFixes}>{applying ? 'Changing the plan…' : `Apply window fixes (${fixes.length})`}</button>
        <span class="text-xs text-gray-500">Changes the windows in the saved plan and checks again; the cost estimate will update.</span>
      </div>
    {/if}

    <div class="grid gap-3 sm:grid-cols-3 text-xs">
      <div class="rounded-lg border p-3" data-testid="green-water"><p class="font-semibold text-sm">Rainwater</p>
        <p>{num(r.water.harvest_litres_year)} L a year from {r.water.roof_m2} m² of roof — {pct(r.water.share)} of a {r.water.occupants}-person household's use.</p>
        <p class="text-gray-500">A {num(r.water.tank_litres)} L tank, with the overflow to a recharge pit.</p></div>
      <div class="rounded-lg border p-3" data-testid="green-solar"><p class="font-semibold text-sm">Rooftop solar</p>
        <p>{r.solar.kwp} kWp makes about {num(r.solar.kwh_year)} kWh a year ({pct(r.solar.share)} of use), saving about {num(r.solar.saving_year)} {r.solar.currency} a year.</p>
        <p class="text-gray-500">The roof could host up to {r.solar.max_kwp} kWp.</p>
        {#if result.solar_site?.available}
          <p class="mt-1 text-emerald-800" data-testid="solar-site">Measured at your plot: {num(r.solar.yield_kwh_per_kwp)} kWh per kWp a year; nearby buildings and trees cut the sun by {result.solar_site.shade_loss_pct}%.</p>
        {:else if result.solar_site}
          <p class="mt-1 text-gray-500" data-testid="solar-site">{result.solar_site.reason} Using your city's climate ({num(r.solar.yield_kwh_per_kwp)} kWh per kWp a year).</p>
        {:else}
          <p class="mt-1 text-gray-400">Pin your plot to measure its own sun and shade.</p>
        {/if}</div>
      <div class="rounded-lg border p-3" data-testid="green-carbon"><p class="font-semibold text-sm">Embodied carbon</p>
        <p>About {r.carbon.total_t} t CO₂e in walls, concrete and steel.</p>
        <ul class="text-gray-600">{#each r.carbon.swaps as s}<li>{s.change}: −{s.saving_t} t ({s.pct}%)</li>{/each}</ul>
        <p class="text-gray-400">Indicative factors.</p></div>
    </div>
  {:else if !busy}
    <p class="text-sm text-gray-600 p-6 text-center" data-testid="no-green">Enter the city to check daylight, cross-ventilation, sun, rainwater, solar and materials for its climate.</p>
  {/if}
</section>
