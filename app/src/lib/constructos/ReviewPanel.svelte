<script lang="ts">
  import type { Change, Review } from './engine';

  let { review, onapply, busy = false }: { review: Review; onapply: (change: Change) => void; busy?: boolean } = $props();
  const facts = $derived(new Map(review.facts.map(f => [f.id, f.text])));
</script>

{#snippet cites(ids: string[])}
  {#if ids.length}<span class="ml-1 align-middle text-[10px] px-1 rounded bg-slate-100 text-slate-600 cursor-help"
    title={ids.map(id => facts.get(id) ?? '').filter(Boolean).join('\n')} data-fact={ids.join(' ')}>measured</span>{/if}
{/snippet}

<section class="rounded-lg border border-indigo-200 bg-indigo-50/50 p-3 space-y-2 text-sm" aria-label="AI review" data-testid="ai-review">
  <p class="text-gray-900" data-testid="review-summary">{review.summary}</p>
  {#if review.strengths.length}
    <ul class="space-y-1" aria-label="Strengths">
      {#each review.strengths as c}<li class="text-gray-800"><span class="text-emerald-700 font-bold">+</span> {c.statement}{@render cites(c.evidence)}</li>{/each}
    </ul>
  {/if}
  {#if review.concerns.length}
    <ul class="space-y-1" aria-label="Concerns">
      {#each review.concerns as c}
        <li class="text-gray-800"><span class="text-xs font-semibold px-1.5 rounded {c.severity === 'major' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'}">{c.severity}</span> {c.statement}{@render cites(c.evidence)}</li>
      {/each}
    </ul>
  {/if}
  {#if review.suggestions.length}
    <ul class="space-y-1.5" aria-label="Suggestions">
      {#each review.suggestions as s}
        <li class="flex items-start justify-between gap-2 text-gray-800">
          <span>→ {s.statement}{@render cites(s.evidence)}</span>
          {#if s.change}<button type="button" class="shrink-0 text-xs px-2 py-1 rounded bg-indigo-600 text-white disabled:opacity-50" disabled={busy} onclick={() => onapply(s.change!)}>Apply</button>{/if}
        </li>
      {/each}
    </ul>
  {/if}
  <p class="text-[11px] text-gray-500">
    Every point is based on measurements of this plan; hover “measured” to see them.
  </p>
</section>
