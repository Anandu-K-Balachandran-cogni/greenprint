<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import FeasibilityPanel from '$lib/constructos/FeasibilityPanel.svelte';
  import {
    engine, layouts, EngineError, ROOM_TYPES, roomTypeLabel, nextRoomId,
    type Brief, type BriefVersion, type Feasibility, type EngineProject, type FieldError, type RoomType, type Unit, type Facing,
  } from '$lib/constructos/engine';

  const MM_PER: Record<Unit, number> = { mm: 1, cm: 10, m: 1000, in: 25.4, ft: 304.8 };
  const FACINGS: Facing[] = ['north', 'south', 'east', 'west', 'north_east', 'north_west', 'south_east', 'south_west'];

  let project = $state<EngineProject | null>(null);
  let brief = $state<Brief | null>(null);
  let confirmed = $state<BriefVersion | null>(null);
  let dirty = $state(false);
  let errors = $state<FieldError[]>([]);
  let loadError = $state<string | null>(null);
  let actionError = $state<string | null>(null);
  let busy = $state<'check' | 'confirm' | 'generate' | null>(null);
  let newRoomType = $state<RoomType>('bathroom');
  let feasibility = $state<Feasibility | null>(null);
  let feasibilityOutdated = $state(false);

  const projectId = $derived(page.params.id ?? '');
  // Room areas are entered in the unit system of the plot; the brief always stores m².
  const SQFT_PER_M2 = 10.7639;
  const imperial = $derived(brief?.plot.unit === 'ft' || brief?.plot.unit === 'in');
  const areaUnit = $derived(imperial ? 'sq ft' : 'm²');
  function areaShown(m2: number | null) {
    return m2 == null ? '' : String(Math.round((imperial ? m2 * SQFT_PER_M2 : m2) * 10) / 10);
  }
  function setArea(room: { min_area_m2: number | null }, raw: string) {
    const v = raw.trim() === '' ? null : Number(raw);
    room.min_area_m2 = v == null || !Number.isFinite(v) ? null : Math.round((imperial ? v / SQFT_PER_M2 : v) * 100) / 100;
    changed();
  }
  const floorCount = $derived(Math.max(1, brief?.storeys ?? 1));
  const essential = $derived(brief?.clarifications.filter(c => c.essential) ?? []);
  const optional = $derived(brief?.clarifications.filter(c => !c.essential) ?? []);
  const plotMm = $derived.by(() => {
    const p = brief?.plot;
    if (!p?.width || !p.depth || !p.unit) return null;
    return { w: Math.floor(p.width * MM_PER[p.unit]), d: Math.floor(p.depth * MM_PER[p.unit]) };
  });
  const roomSummary = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const r of brief?.rooms ?? []) counts.set(r.type, (counts.get(r.type) ?? 0) + 1);
    return [...counts].map(([t, n]) => `${n} × ${roomTypeLabel(t as RoomType).toLowerCase()}`).join(', ');
  });

  onMount(load);

  async function load() {
    let loaded: EngineProject;
    try {
      loaded = await engine.getProject(projectId);
    } catch (e) {
      loadError = e instanceof EngineError ? e.message : 'Could not reach the Greenprint engine.';
      return;
    }
    // Plain JSON copies: the editable brief must not alias the confirmed version.
    project = loaded;
    confirmed = loaded.brief;
    brief = structuredClone(loaded.brief?.brief ?? loaded.brief_draft);
    if (!brief) { loadError = 'This project has no brief yet.'; return; }
    void assess();
  }

  function changed() { dirty = true; errors = []; if (feasibility) feasibilityOutdated = true; }

  /** Deterministic feasibility of the brief as currently shown; never changes the brief. */
  async function assess() {
    if (!brief) return;
    try {
      feasibility = (await engine.checkBrief(projectId, $state.snapshot(brief) as Brief)).feasibility;
      feasibilityOutdated = false;
    } catch { /* the check button reports engine errors */ }
  }
  function floorLabel(i: number) { return i === 0 ? 'Ground floor' : i === 1 ? 'First floor' : `Floor ${i}`; }
  function fieldErrors(field: string) { return errors.filter(e => e.field === field || e.field.startsWith(`${field}.`)); }

  function addRoom() {
    if (!brief) return;
    brief.rooms.push({
      id: nextRoomId(brief.rooms, newRoomType), type: newRoomType, name: roomTypeLabel(newRoomType),
      floor_index: null, requirement: 'hard', min_area_m2: null, attached_bathroom: false, source: 'Added by user',
    });
    changed();
  }

  function removeRoom(id: string) {
    if (!brief) return;
    brief.rooms = brief.rooms.filter(r => r.id !== id);
    changed();
  }

  async function check() {
    if (!brief) return;
    busy = 'check'; actionError = null;
    try {
      const result = await engine.checkBrief(projectId, $state.snapshot(brief) as Brief);
      brief = result.brief;
      errors = result.errors;
      feasibility = result.feasibility;
      feasibilityOutdated = false;
    } catch (e) {
      actionError = e instanceof EngineError ? e.message : 'Check failed.';
    } finally { busy = null; }
  }

  async function generate() {
    if (!confirmed || dirty) return;
    busy = 'generate'; actionError = null;
    try {
      const run = await layouts.generate(projectId, confirmed.version, 2);
      await goto(`/build/${projectId}/layouts/${run.id}`);
    } catch (e) {
      actionError = e instanceof EngineError ? e.message : 'Layout generation failed.';
      busy = null;
    }
  }

  async function confirm() {
    if (!brief || !project) return;
    busy = 'confirm'; actionError = null;
    try {
      const saved = await engine.confirmBrief(projectId, $state.snapshot(brief) as Brief, confirmed?.version ?? 0);
      confirmed = saved;
      brief = structuredClone(saved.brief);
      dirty = false; errors = [];
      void assess();
    } catch (e) {
      if (e instanceof EngineError && e.status === 422) {
        errors = (e.detail as { errors: FieldError[] }).errors ?? [];
        actionError = 'Some required information is missing.';
      } else if (e instanceof EngineError && e.status === 409) {
        actionError = 'This brief was confirmed elsewhere. Reload to see the latest version before confirming.';
      } else {
        actionError = e instanceof EngineError ? e.message : 'Confirmation failed.';
      }
    } finally { busy = null; }
  }
</script>

<svelte:head><title>Greenprint — Requirements</title></svelte:head>

<div class="min-h-screen bg-gray-50">
  <header class="bg-gradient-to-r from-slate-800 to-slate-700">
    <div class="max-w-5xl mx-auto px-6 py-5 flex items-center justify-between gap-4">
      <div>
        <a href="/build" class="text-xs text-white/60 hover:text-white">← Greenprint</a>
        <h1 class="text-2xl font-bold text-white">{project?.name ?? 'Requirements'}</h1>
      </div>
      <div class="text-right text-sm" aria-live="polite">
        {#if confirmed}
          <p class="text-emerald-300 font-medium" data-testid="brief-status">Brief v{confirmed.version} confirmed{dirty ? ' · unsaved changes' : ''}</p>
        {:else}
          <p class="text-amber-300 font-medium" data-testid="brief-status">Draft — not confirmed</p>
        {/if}
      </div>
    </div>
  </header>

  <main class="max-w-5xl mx-auto px-6 py-8 space-y-6">
    {#if loadError}
      <p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{loadError}</p>
    {:else if !brief}
      <p role="status" class="text-gray-500">Loading…</p>
    {:else}
      <section class="bg-white rounded-xl border border-gray-200 p-5">
        <h2 class="text-sm font-semibold text-gray-500 uppercase tracking-wide">Original brief</h2>
        <blockquote class="mt-2 text-gray-800 whitespace-pre-wrap">{brief.original_text}</blockquote>
        <p class="mt-3 text-sm text-gray-600" data-testid="room-summary">
          {brief.storeys ?? '?'} floor(s) · {roomSummary || 'no rooms'} · {brief.parking_spaces} parking · {brief.open_kitchen ? 'open kitchen' : 'closed kitchen'}
        </p>
      </section>

      {#if essential.length || brief.conflicts.length}
        <section class="bg-amber-50 border border-amber-200 rounded-xl p-5 space-y-3" aria-labelledby="attention-heading">
          <h2 id="attention-heading" class="font-semibold text-amber-900">Needs your attention</h2>
          {#if essential.length}
            <ul class="list-disc pl-5 text-sm text-amber-900" aria-label="Required answers">
              {#each essential as c}<li><strong>Required:</strong> {c.question}</li>{/each}
            </ul>
          {/if}
          {#if brief.conflicts.length}
            <ul class="list-disc pl-5 text-sm text-amber-900" aria-label="Conflicts">
              {#each brief.conflicts as c}<li><strong>Conflict:</strong> {c.statement}</li>{/each}
            </ul>
          {/if}
        </section>
      {/if}

      <section class="bg-white rounded-xl border border-gray-200 p-5 grid gap-4 md:grid-cols-2" aria-labelledby="site-heading">
        <h2 id="site-heading" class="md:col-span-2 font-semibold text-gray-900">Site and building</h2>
        <fieldset class="space-y-2">
          <legend class="text-sm font-medium text-gray-700">Plot</legend>
          <div class="flex gap-2 items-end">
            <label class="flex-1"><span class="text-xs text-gray-500">Width</span>
              <input type="number" min="0" step="any" class="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="Plot width" bind:value={brief.plot.width} oninput={changed} /></label>
            <span class="pb-2 text-gray-400">×</span>
            <label class="flex-1"><span class="text-xs text-gray-500">Depth</span>
              <input type="number" min="0" step="any" class="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="Plot depth" bind:value={brief.plot.depth} oninput={changed} /></label>
            <label><span class="text-xs text-gray-500">Unit</span>
              <select class="block rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="Plot unit" bind:value={brief.plot.unit} onchange={changed}>
                <option value={null}>—</option>
                {#each Object.keys(MM_PER) as u}<option value={u}>{u}</option>{/each}
              </select></label>
          </div>
          <p class="text-xs text-gray-500" data-testid="plot-mm">{plotMm ? `${plotMm.w.toLocaleString()} × ${plotMm.d.toLocaleString()} mm` : 'Plot size required'}</p>
          {#each fieldErrors('plot') as e}<p class="text-xs text-red-700">{e.message}</p>{/each}
        </fieldset>
        <div class="grid grid-cols-2 gap-3">
          <label><span class="text-sm font-medium text-gray-700">Floors</span>
            <input type="number" min="1" max="6" class="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="Number of floors" bind:value={brief.storeys} oninput={changed} />
            {#each fieldErrors('storeys') as e}<span class="text-xs text-red-700">{e.message}</span>{/each}</label>
          <label><span class="text-sm font-medium text-gray-700">Parking spaces</span>
            <input type="number" min="0" max="10" class="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="Parking spaces" bind:value={brief.parking_spaces} oninput={changed} /></label>
          <label><span class="text-sm font-medium text-gray-700">Entrance faces</span>
            <select class="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="Entrance faces" bind:value={brief.entrance_facing} onchange={changed}>
              <option value={null}>Not specified</option>
              {#each FACINGS as f}<option value={f}>{f.replace('_', '-')}</option>{/each}
            </select></label>
          <label class="flex items-center gap-2 pt-6"><input type="checkbox" bind:checked={brief.open_kitchen} onchange={changed} /> <span class="text-sm text-gray-700">Open kitchen</span></label>
        </div>
      </section>

      <section class="bg-white rounded-xl border border-gray-200 p-5" aria-labelledby="rooms-heading">
        <div class="flex items-center justify-between gap-3 mb-3">
          <h2 id="rooms-heading" class="font-semibold text-gray-900">Rooms <span class="text-gray-400 font-normal">({brief.rooms.length})</span></h2>
          <div class="flex gap-2">
            <select class="rounded-lg border border-gray-300 px-2 py-1.5 text-sm" aria-label="New room type" bind:value={newRoomType}>
              {#each ROOM_TYPES as t}<option value={t}>{roomTypeLabel(t)}</option>{/each}
            </select>
            <button type="button" class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:bg-gray-50" onclick={addRoom}>Add room</button>
          </div>
        </div>
        {#each errors.filter(e => e.field === 'rooms') as e}<p class="text-xs text-red-700 mb-2">{e.message}</p>{/each}
        <p class="text-xs text-gray-500 mb-2">Leave minimum areas empty to use typical sizes. Areas are in {areaUnit}{imperial ? ' because the plot is in ' + brief.plot.unit : ''}.</p>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-left text-xs text-gray-500 border-b">
              <th class="py-2 pr-2">Name</th><th class="pr-2">Type</th><th class="pr-2">Floor</th><th class="pr-2">Requirement</th><th class="pr-2">Min. area ({areaUnit})</th><th class="pr-2">En-suite</th><th></th>
            </tr></thead>
            <tbody>
              {#each brief.rooms as room (room.id)}
                <tr class="border-b border-gray-100" data-room-id={room.id}>
                  <td class="py-1.5 pr-2"><input class="w-full rounded border border-gray-200 px-2 py-1" aria-label="Room name {room.id}" bind:value={room.name} oninput={changed} title={room.source ?? ''} /></td>
                  <td class="pr-2"><select class="rounded border border-gray-200 px-1 py-1" aria-label="Room type {room.id}" bind:value={room.type} onchange={changed}>
                    {#each ROOM_TYPES as t}<option value={t}>{roomTypeLabel(t)}</option>{/each}</select></td>
                  <td class="pr-2"><select class="rounded border border-gray-200 px-1 py-1" aria-label="Floor {room.id}" bind:value={room.floor_index} onchange={changed}>
                    <option value={null}>Any floor</option>
                    {#each Array.from({ length: floorCount }, (_, i) => i) as i}<option value={i}>{floorLabel(i)}</option>{/each}
                    {#if room.floor_index !== null && room.floor_index >= floorCount}<option value={room.floor_index}>{floorLabel(room.floor_index)} (missing)</option>{/if}
                  </select></td>
                  <td class="pr-2"><select class="rounded border border-gray-200 px-1 py-1" aria-label="Requirement {room.id}" bind:value={room.requirement} onchange={changed}>
                    <option value="hard">Required</option><option value="preferred">Preferred</option></select></td>
                  <td class="pr-2">
                    <input type="number" min="0" step="any" class="w-24 rounded border border-gray-200 px-2 py-1" aria-label="Minimum area {room.id}"
                      value={areaShown(room.min_area_m2)} onchange={(e) => setArea(room, e.currentTarget.value)} placeholder="auto" />
                    {#if imperial && room.min_area_m2}<span class="block text-[10px] text-gray-400" data-testid="area-m2-{room.id}">= {room.min_area_m2} m²</span>{/if}
                  </td>
                  <td class="pr-2 text-center">{#if ['bedroom', 'master_bedroom', 'guest_room'].includes(room.type)}<input type="checkbox" aria-label="En-suite {room.id}" bind:checked={room.attached_bathroom} onchange={changed} />{/if}</td>
                  <td class="text-right"><button type="button" class="text-xs text-red-600 hover:underline" aria-label="Remove {room.name}" onclick={() => removeRoom(room.id)}>Remove</button></td>
                </tr>
                {#each fieldErrors(`rooms.${room.id}`) as e}<tr><td colspan="7" class="text-xs text-red-700 pb-1">{e.message}</td></tr>{/each}
              {/each}
            </tbody>
          </table>
        </div>
      </section>

      <div class="grid gap-6 md:grid-cols-2">
        <section class="bg-white rounded-xl border border-gray-200 p-5" aria-labelledby="assumptions-heading">
          <h2 id="assumptions-heading" class="font-semibold text-gray-900 mb-2">Assumptions</h2>
          {#if brief.assumptions.length}
            <ul class="list-disc pl-5 text-sm text-gray-700 space-y-1">{#each brief.assumptions as a}<li>{a.statement}</li>{/each}</ul>
          {:else}<p class="text-sm text-gray-500">None.</p>{/if}
          {#if brief.entrance_status !== 'stated'}<p class="mt-2 text-xs text-gray-500">Orientation is not confirmed; daylight will only be estimated from exterior exposure.</p>{/if}
        </section>
        <section class="bg-white rounded-xl border border-gray-200 p-5" aria-labelledby="questions-heading">
          <h2 id="questions-heading" class="font-semibold text-gray-900 mb-2">Open questions</h2>
          {#if optional.length}
            <ul class="list-disc pl-5 text-sm text-gray-700 space-y-1">{#each optional as c}<li>{c.question}</li>{/each}</ul>
          {:else}<p class="text-sm text-gray-500">None.</p>{/if}
        </section>
      </div>

      {#if feasibility}<FeasibilityPanel report={feasibility} outdated={feasibilityOutdated} />{/if}

      {#if actionError}<p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{actionError}</p>{/if}

      <div class="flex justify-end gap-3">
        <button type="button" class="px-4 py-2.5 rounded-lg border border-gray-300 text-sm font-medium hover:bg-white disabled:opacity-50" disabled={busy !== null} onclick={check}>
          {busy === 'check' ? 'Checking…' : 'Check brief'}
        </button>
        <button type="button" class="px-5 py-2.5 bg-blue-600 text-white rounded-lg font-semibold text-sm hover:bg-blue-700 disabled:opacity-50" disabled={busy !== null || (!!confirmed && !dirty)} onclick={confirm}>
          {busy === 'confirm' ? 'Confirming…' : confirmed && !dirty ? 'Confirmed' : 'Confirm brief'}
        </button>
        <button type="button" class="px-5 py-2.5 bg-emerald-600 text-white rounded-lg font-semibold text-sm hover:bg-emerald-700 disabled:opacity-50"
          disabled={busy !== null || !confirmed || dirty} title={dirty ? 'Confirm your changes first' : ''} onclick={generate}>
          {busy === 'generate' ? 'Solving layouts…' : 'Generate layouts'}
        </button>
      </div>
      {#if busy === 'generate'}<p role="status" class="text-right text-xs text-gray-500">Starting the designs…</p>{/if}

      {#if project?.layout_runs.length}
        <section class="bg-white rounded-xl border border-gray-200 p-5" aria-labelledby="runs-heading">
          <h2 id="runs-heading" class="font-semibold text-gray-900 mb-2">Generated layouts</h2>
          <ul class="divide-y divide-gray-100 text-sm">
            {#each [...project.layout_runs].reverse() as r (r.id)}
              <li><a class="flex justify-between py-2 hover:underline" href="/build/{projectId}/layouts/{r.id}">
                <span>{new Date(r.created_at).toLocaleString()} · {r.kind === 'revision' ? `Revision: “${r.request ?? 'suggested change'}”` : 'New options'} · {r.status === 'complete' ? r.statuses.join(', ') : r.status}</span>
                <span class="text-gray-500">brief v{r.brief_version}{r.brief_version !== confirmed?.version ? ' (older brief)' : ''}</span>
              </a></li>
            {/each}
          </ul>
        </section>
      {/if}
    {/if}
  </main>
</div>
