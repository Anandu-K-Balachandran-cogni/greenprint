<script lang="ts">
  // Ask the interior designer agent for a room or the whole floor. Its design is re-planned here with
  // the same solver it used (deterministic), applied as one undo step to preview in 3D, then kept or
  // discarded. Follow-ups ("lighter please") first discard an open preview, then ask again.
  import type { Room } from '$lib/models/types';
  import type { CompiledProject } from './compiler';
  import { activeFloor, applyDesignChanges, currentProject, undo, undoHistoryStore } from '$lib/stores/project';
  import { get } from 'svelte/store';
  import { agents, EngineError, type InteriorDesign } from './engine';
  import { planDesign } from './design';
  import { designStyle } from './renderBus';

  let { room }: { room: Room } = $props();

  let message = $state('');
  let scope = $state<'room' | 'floor'>('room');
  let busy = $state(false);
  let error = $state('');
  let answer = $state('');
  let design = $state<InteriorDesign | null>(null);
  let preview = $state<{ steps: number; stamp: number | undefined; pieces: number } | null>(null);
  let kept = $state(false);
  let chatContext: Record<string, unknown> | null = null;

  const engineProjectId = $derived(($currentProject as CompiledProject | null)?.constructos?.engineProjectId ?? '');
  const lakh = (v: number) => `₹${(v / 1e5).toFixed(v < 1e6 ? 2 : 1)} lakh`;
  // Discard is safe only while the preview is still the latest undo step (nothing edited since).
  const canDiscard = $derived(!!preview && $undoHistoryStore.entries.length === preview.steps
    && $undoHistoryStore.entries.at(-1)?.timestamp === preview.stamp);

  function discard() {
    if (canDiscard) undo();
    preview = null;
  }

  async function ask() {
    const text = message.trim();
    if (!text || busy) return;
    // An open preview is replaced by the new proposal; a kept one stays as the starting point.
    if (preview && !kept) discard();
    preview = null; design = null; answer = '';
    const floor = get(activeFloor), project = get(currentProject);
    if (!floor || !project) return;
    busy = true; error = ''; kept = false;
    const roomIds = scope === 'room' ? [room.id] : null;
    const homeHasPooja = project.floors.some(f => f.id !== floor.id && f.furniture.some(i => i.catalogId === 'pooja_unit'));
    try {
      const reply = await agents.chat(engineProjectId, 'interior', {
        message: text, chat_context: chatContext,
        interior: { floor, room_ids: roomIds, furnish: true, home_has_pooja: homeHasPooja, editor_project_id: project.id },
      });
      chatContext = reply.chat_context;
      answer = reply.answer;
      design = reply.design ?? null;
      if (design) {
        const plan = planDesign(floor, design.brief, { roomIds: design.scope.room_ids ?? undefined, furnish: design.scope.furnish, homeHasPooja });
        applyDesignChanges(plan.changes, `Interior design: ${design.brief.title ?? design.brief.base_theme}`);
        const h = get(undoHistoryStore);
        preview = { steps: h.entries.length, stamp: h.entries.at(-1)?.timestamp, pieces: plan.changes.add.length };
        designStyle.set([design.brief.title, design.brief.rationale].filter(Boolean).join(': ') || null);
      }
      message = '';
    } catch (e) {
      error = e instanceof EngineError ? e.message : 'The interior designer could not answer. Please try again.';
    } finally {
      busy = false;
    }
  }
</script>

{#if engineProjectId}
  <div class="pt-2 border-t border-gray-100" data-testid="interior-designer">
    <span class="text-xs text-gray-500 block mb-1">Ask the interior designer</span>
    <textarea bind:value={message} rows="2" maxlength="600" aria-label="Describe the look you want"
      placeholder="e.g. warm Indian, lots of wood and plants, no TV"
      class="w-full px-2 py-1 border border-gray-200 rounded text-xs" onkeydown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask(); }}></textarea>
    <div class="flex items-center gap-2 mt-1 text-[11px] text-gray-600">
      <label class="flex items-center gap-1"><input type="radio" bind:group={scope} value="room" /> {room.name}</label>
      <label class="flex items-center gap-1"><input type="radio" bind:group={scope} value="floor" /> Whole floor</label>
      <button type="button" class="ml-auto px-2 py-1 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
        disabled={busy || !message.trim()} onclick={ask}>{busy ? 'Designing…' : 'Design it'}</button>
    </div>
    {#if busy}<p class="text-[11px] text-gray-500 mt-1" role="status">The designer is choosing pieces and checking that everything fits…</p>{/if}
    {#if error}<p class="text-[11px] text-red-700 mt-1" role="alert">{error}</p>{/if}
    {#if answer}
      <div class="mt-2 p-2 rounded bg-indigo-50 text-[11px] text-gray-800 space-y-1" data-testid="designer-reply">
        <p class="whitespace-pre-line">{answer}</p>
        {#if design}
          <p class="font-medium" data-testid="design-budget">Interiors budget: {lakh(design.budget.total)} ({design.budget.tier}) · {preview?.pieces ?? 0} pieces</p>
          {#if design.plan.skipped.length}
            <p class="text-amber-700">Could not fit: {design.plan.skipped.map(s => `${s.item.replace(/_/g, ' ')} in ${s.room}`).join(', ')}</p>
          {/if}
          {#if preview && !kept && !busy}
            <div class="flex gap-1 pt-1">
              <button type="button" class="flex-1 px-2 py-1 rounded bg-green-600 text-white hover:bg-green-700" onclick={() => { kept = true; }}>Keep design</button>
              <button type="button" class="flex-1 px-2 py-1 rounded border border-gray-300 hover:bg-white disabled:opacity-50" disabled={!canDiscard}
                title={canDiscard ? '' : 'Other changes were made since; use Undo instead'} onclick={discard}>Discard</button>
            </div>
          {:else if kept}
            <p class="text-green-700" data-testid="design-kept">Kept. Ask again to refine it, or use Undo to go back.</p>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
{/if}
