<script lang="ts">
  // Style a whole room: wall paint (with an accent wall), ceiling colour, curtains for its windows,
  // and clearance warnings. Every change is one undo step.
  import type { Room } from '$lib/models/types';
  import { activeFloor, addFurniture, applyDesignChanges, beginUndoGroup, currentProject, endUndoGroup, selectedElementId, selectedRoomId, updateFurniture, updateRoom, updateWall } from '$lib/stores/project';
  import { get } from 'svelte/store';
  import { THEMES, planTheme, roomKind } from './themes';
  import InteriorDesigner from './InteriorDesigner.svelte';
  import { designStyle, renderState, requestRender } from './renderBus';
  import { resolveRoomGeometry } from '$lib/utils/roomDetection';
  import { pointInPolygon } from '$lib/utils/hitTesting';
  import { getCatalogItem } from '$lib/utils/furnitureCatalog';
  import { wallColors } from '$lib/utils/materials';
  import { clearanceIssues, curtainPlans, hasCurtainNear, roomWalls, spanUpdates } from './roomDesign';

  let { room, is3D = false }: { room: Room; is3D?: boolean } = $props();

  const PAINTS = wallColors.filter(c => !c.texture);
  const CEILINGS = ['#ffffff', '#faf7f2', '#f5f0e6', '#eef2f3', '#e8e4dc'];
  // The store mutates the floor in place, so recompute everything whenever it emits (a plain
  // $derived of the floor would see the same object and keep stale lists).
  const view = $derived.by(() => {
    const floor = $activeFloor;
    const polygons = floor ? new Map(resolveRoomGeometry(floor, floor.rooms).map(x => [x.room.id, x.polygon])) : new Map();
    const polygon = polygons.get(room.id);
    const walls = floor ? roomWalls(floor, room, polygons) : [];
    const windowPlans = floor && polygon ? curtainPlans(floor, walls, polygon) : [];
    return {
      floor, polygon, walls, windowsInRoom: windowPlans.length,
      curtains: floor ? windowPlans.filter(p => !hasCurtainNear(floor, p.position)) : [],
      furniture: floor && polygon ? floor.furniture.filter(f => pointInPolygon(f.position, polygon)) : [],
      issues: floor && polygon ? clearanceIssues(floor, polygon) : [],
    };
  });
  const floor = $derived(view.floor);
  const polygon = $derived(view.polygon);
  const walls = $derived(view.walls);
  const curtains = $derived(view.curtains);
  const windowsInRoom = $derived(view.windowsInRoom);
  const furniture = $derived(view.furniture);
  const issues = $derived(view.issues);
  let accentWallId = $state('');
  let themeId = $state(THEMES[0].id);
  let furnish = $state(true);
  let themeStatus = $state('');
  const theme = $derived(THEMES.find(t => t.id === themeId)!);

  // Plan against the floor as it is now, then apply in one undo step.
  function applyTheme(scope: 'room' | 'floor') {
    const f = get(activeFloor);
    if (!f) return;
    const homeHasPooja = (get(currentProject)?.floors ?? []).some(x => x.id !== f.id && x.furniture.some(i => i.catalogId === 'pooja_unit'));
    const changes = planTheme(f, theme, { roomIds: scope === 'room' ? [room.id] : undefined, furnish, homeHasPooja });
    applyDesignChanges(changes, `${theme.name} theme on ${scope === 'room' ? room.name : 'this floor'}`);
    const { rooms, added, restyled, furnished } = changes.summary;
    themeStatus = `${theme.name} applied to ${scope === 'room' ? room.name : `${rooms.length} rooms`}`
      + ` · ${added} item${added === 1 ? '' : 's'} added${furnished.length ? ` (furnished ${furnished.length})` : ''} · ${restyled} restyled`;
  }

  // Paint only this room's stretch of each wall face (long walls often run through several rooms).
  function paintWalls(color: string) {
    if (!floor || !polygon) return;
    const targets = accentWallId ? walls.filter(w => w.wallId !== accentWallId) : walls;
    beginUndoGroup();
    for (const u of spanUpdates(floor, targets, polygon, room.id, color)) updateWall(u.wallId, u.patch);
    endUndoGroup(`Painted ${room.name}`);
  }
  function paintAccent(color: string) {
    const target = walls.find(w => w.wallId === accentWallId);
    if (!floor || !polygon || !target) return;
    for (const u of spanUpdates(floor, [target], polygon, room.id, color)) updateWall(u.wallId, u.patch);
  }
  function addCurtains(kind: 'curtain' | 'sheer_curtain') {
    beginUndoGroup();
    for (const plan of curtains) {
      const id = addFurniture(kind, plan.position);
      updateFurniture(id, { rotation: plan.rotation, width: plan.width, height: plan.height, depth: plan.depth });
    }
    endUndoGroup(`Added curtains to ${room.name}`);
  }
  function select(id: string) { selectedRoomId.set(null); selectedElementId.set(id); }
  const wallLabel = (i: number, length: number) => `Wall ${i + 1} · ${(length / 100).toFixed(1)} m`;
</script>

<div class="mt-4 pt-3 border-t border-gray-200 space-y-3" data-testid="room-design">
  <h4 class="text-sm font-semibold text-gray-700">Room design</h4>

  <div data-testid="themes">
    <span class="text-xs text-gray-500 block mb-1">Theme</span>
    <div class="grid grid-cols-2 gap-1" role="radiogroup" aria-label="Theme">
      {#each THEMES as t}
        <button type="button" role="radio" aria-checked={themeId === t.id} aria-label="Theme {t.name}" title={t.blurb}
          class="text-left px-1.5 py-1 rounded border text-[11px] leading-tight {themeId === t.id ? 'border-blue-500 ring-2 ring-blue-200 bg-blue-50' : 'border-gray-200 hover:bg-gray-50'}"
          onclick={() => { themeId = t.id; themeStatus = ''; }}>
          <span class="flex gap-0.5 mb-0.5">
            {#each [t.walls, t.accent, t.palette.fabric, t.palette.wood, t.rug] as c}<span class="w-3 h-3 rounded-sm border border-gray-300" style="background:{c}"></span>{/each}
          </span>
          {t.name}
        </button>
      {/each}
    </div>
    <p class="text-[11px] text-gray-500 mt-1">{theme.blurb}</p>
    <label class="flex items-center gap-1 text-[11px] text-gray-600 mt-1"><input type="checkbox" bind:checked={furnish} /> Furnish empty rooms</label>
    <div class="flex gap-1 mt-1">
      <button type="button" class="flex-1 px-2 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50" disabled={!roomKind(room)}
        onclick={() => applyTheme('room')}>Apply to {room.name}</button>
      <button type="button" class="flex-1 px-2 py-1 text-xs rounded border border-blue-600 text-blue-700 hover:bg-blue-50" onclick={() => applyTheme('floor')}>Apply to whole floor</button>
    </div>
    {#if !roomKind(room)}<p class="text-[11px] text-gray-500 mt-1">Themes style living spaces, bedrooms, kitchens, bathrooms and halls.</p>{/if}
    {#if themeStatus}<p class="text-[11px] text-green-700 mt-1" data-testid="theme-status">{themeStatus}</p>{/if}
  </div>

  <InteriorDesigner {room} />

  {#if is3D}
    <div class="pt-2 border-t border-gray-100" data-testid="photo-render">
      <span class="text-xs text-gray-500 block mb-1">Photo-real render</span>
      <div class="flex gap-1">
        <button type="button" class="flex-1 px-2 py-1 text-xs rounded bg-slate-800 text-white hover:bg-slate-900 disabled:opacity-50" disabled={$renderState.busy}
          onclick={() => requestRender({ kind: 'room', roomId: room.id, roomName: room.name, style: $designStyle ?? undefined })}>Render this room</button>
        <button type="button" class="flex-1 px-2 py-1 text-xs rounded border border-slate-800 text-slate-800 hover:bg-slate-50 disabled:opacity-50" disabled={$renderState.busy}
          onclick={() => requestRender({ kind: 'view', style: $designStyle ?? undefined })}>Render 3D view</button>
      </div>
      {#if $renderState.busy}<p class="text-[11px] text-gray-500 mt-1" role="status">Rendering {$renderState.label}…</p>{/if}
      {#if $renderState.error}<p class="text-[11px] text-red-700 mt-1" role="alert">{$renderState.error}</p>{/if}
      {#if $renderState.image && !$renderState.busy}
        <a href={$renderState.image} target="_blank" rel="noopener" class="block mt-1">
          <img src={$renderState.image} alt="Photo-real render of {$renderState.label}" class="w-full rounded border border-gray-200" data-testid="render-image" />
        </a>
        <p class="text-[10px] text-gray-400 mt-0.5">{$renderState.label} · a picture only; your design is unchanged</p>
      {/if}
    </div>
  {/if}

  <div>
    <span class="text-xs text-gray-500 block mb-1">Walls ({walls.length}){accentWallId ? ' except the accent wall' : ''}</span>
    <div class="flex flex-wrap gap-1" role="group" aria-label="Wall paint">
      {#each PAINTS as c}
        <button type="button" class="w-6 h-6 rounded border border-gray-300 hover:scale-110 transition-transform" style="background:{c.color}"
          title={c.name} aria-label="Paint walls {c.name}" onclick={() => paintWalls(c.color)}></button>
      {/each}
    </div>
  </div>

  <div>
    <label class="text-xs text-gray-500 block">Accent wall
      <select class="w-full mt-1 px-2 py-1 border border-gray-200 rounded text-sm" bind:value={accentWallId} aria-label="Accent wall">
        <option value="">None</option>
        {#each walls as w, i}<option value={w.wallId}>{wallLabel(i, w.length)}</option>{/each}
      </select></label>
    {#if accentWallId}
      <div class="flex flex-wrap gap-1 mt-1" role="group" aria-label="Accent paint">
        {#each PAINTS as c}
          <button type="button" class="w-6 h-6 rounded border border-gray-300 hover:scale-110 transition-transform" style="background:{c.color}"
            title={c.name} aria-label="Paint accent wall {c.name}" onclick={() => paintAccent(c.color)}></button>
        {/each}
      </div>
    {/if}
  </div>

  <div>
    <span class="text-xs text-gray-500 block mb-1">Ceiling</span>
    <div class="flex gap-1" role="group" aria-label="Ceiling colour">
      {#each CEILINGS as c}
        <button type="button" class="w-6 h-6 rounded border {room.ceilingColor === c ? 'border-blue-500 ring-2 ring-blue-200' : 'border-gray-300'}" style="background:{c}"
          aria-label="Ceiling {c}" onclick={() => updateRoom(room.id, { ceilingColor: c })}></button>
      {/each}
    </div>
  </div>

  <div>
    <span class="text-xs text-gray-500 block mb-1">Curtains</span>
    {#if curtains.length}
      <div class="flex gap-2">
        <button type="button" class="flex-1 px-2 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50" onclick={() => addCurtains('curtain')}>
          Add curtains ({curtains.length} window{curtains.length > 1 ? 's' : ''})</button>
        <button type="button" class="flex-1 px-2 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50" onclick={() => addCurtains('sheer_curtain')}>Sheer</button>
      </div>
    {:else}
      <p class="text-[11px] text-gray-500" data-testid="curtain-status">{windowsInRoom ? 'Every window in this room has curtains.' : 'This room has no windows.'}</p>
    {/if}
  </div>

  <div>
    <span class="text-xs text-gray-500 block mb-1">In this room ({furniture.length})</span>
    <ul class="text-xs space-y-0.5" data-testid="room-furniture">
      {#each furniture as f}<li><button type="button" class="hover:underline text-left" onclick={() => select(f.id)}>{getCatalogItem(f.catalogId)?.name ?? f.catalogId}</button></li>{/each}
    </ul>
    {#if issues.length}
      <ul class="mt-1 text-[11px] text-red-700 space-y-0.5" data-testid="clearance-issues">{#each issues as i}<li>⚠ {i.message}</li>{/each}</ul>
    {/if}
  </div>
</div>
