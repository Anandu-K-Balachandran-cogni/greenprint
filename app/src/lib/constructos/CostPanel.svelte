<script lang="ts">
  // Construction cost estimate for the saved plan: quantities × rates, grouped by trade,
  // with the per-sq-ft check against the published planning band for the chosen tier.
  import { designs, estimates, EngineError, type Estimate, type InteriorDesign } from './engine';
  import type { RoutingSource } from './routingSource';

  let { projectId, editorId, getSource, estimateId = null }: {
    projectId: string; editorId: string; getSource: () => Promise<RoutingSource>; estimateId?: string | null;
  } = $props();

  let estimate = $state<Estimate | null>(null);
  let tier = $state<'basic' | 'standard' | 'premium'>('standard');
  let location = $state<'non_metro' | 'metro'>('non_metro');
  let busy = $state(false);
  let error = $state<string | null>(null);
  let current = $state<boolean | null>(null);

  const inr = (v: number) => '₹' + Math.round(v).toLocaleString('en-IN');
  const lakh = (v: number) => `₹${(v / 1e5).toFixed(2)} lakh`;

  async function load(id?: string | null) {
    const list = await estimates.list(projectId, editorId);
    const pick = id ?? list.at(-1)?.id;
    estimate = pick ? await estimates.get(projectId, pick) : null;
    if (estimate) { tier = estimate.tier; location = estimate.location; current = estimate.source_hash === (await getSource()).source_hash; }
  }
  $effect(() => { void load(estimateId).catch(() => { error = 'Could not load the estimate.'; }); });

  // The latest interior design the designer proposed for this plan, priced separately from construction.
  let interior = $state<InteriorDesign | null>(null);
  $effect(() => {
    void designs.list(projectId, editorId).then(async list => {
      const last = list.at(-1);
      interior = last ? await designs.get(projectId, last.id) : null;
    }).catch(() => { interior = null; });
  });

  async function run() {
    busy = true; error = null;
    try {
      const source = await getSource();
      estimate = await estimates.create(projectId, { source, tier, location });
      current = true;
    } catch (e) {
      error = e instanceof EngineError ? e.message : 'The estimate failed.';
    } finally { busy = false; }
  }
  const groups = $derived(estimate ? Object.entries(estimate.groups) : []);
</script>

<section class="bg-white rounded-xl border border-gray-200 p-4 space-y-4 min-w-0" aria-label="Cost estimate">
  <div class="flex flex-wrap items-end gap-3">
    <label class="text-xs font-medium text-gray-700">Finish
      <select class="mt-1 block rounded-lg border border-gray-300 px-2 py-1.5 text-sm" bind:value={tier} aria-label="Finish level">
        <option value="basic">Basic</option><option value="standard">Standard</option><option value="premium">Premium</option>
      </select></label>
    <label class="text-xs font-medium text-gray-700">City
      <select class="mt-1 block rounded-lg border border-gray-300 px-2 py-1.5 text-sm" bind:value={location} aria-label="City type">
        <option value="non_metro">Non-metro city</option><option value="metro">Metro city</option>
      </select></label>
    <button type="button" class="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50" disabled={busy} onclick={run}>
      {busy ? 'Estimating…' : estimate ? 'Re-estimate' : 'Estimate cost'}</button>
    {#if estimate && current === false}<span class="text-xs font-semibold px-2 py-1 rounded bg-amber-100 text-amber-900" data-testid="estimate-currency">Out of date — the plan changed</span>{/if}
  </div>
  {#if error}<p role="alert" class="text-sm text-red-700">{error}</p>{/if}

  {#if estimate}
    <div class="grid gap-3 sm:grid-cols-3">
      <div class="rounded-lg bg-slate-800 text-white p-3"><p class="text-xs text-white/70">Estimated construction cost</p>
        <p class="text-2xl font-bold" data-testid="estimate-total">{lakh(estimate.total)}</p></div>
      <div class="rounded-lg border p-3"><p class="text-xs text-gray-500">Per sq ft built-up</p>
        <p class="text-xl font-semibold" data-testid="estimate-per-sqft">{inr(estimate.per_sqft)}</p>
        <p class="text-xs text-gray-500">{estimate.quantities.built_up_sqft.toLocaleString('en-IN')} sq ft</p></div>
      <div class="rounded-lg border p-3 {estimate.benchmark.verdict === 'within' ? 'border-emerald-300 bg-emerald-50' : 'border-amber-300 bg-amber-50'}">
        <p class="text-xs text-gray-500">Published {estimate.tier} band</p>
        <p class="text-sm font-semibold" data-testid="estimate-benchmark">{inr(estimate.benchmark.low)}–{inr(estimate.benchmark.high)} / sq ft · {estimate.benchmark.verdict}</p></div>
    </div>
    <table class="w-full text-sm" aria-label="Estimate lines">
      <thead><tr class="text-left text-xs text-gray-500"><th class="py-1">Item</th><th class="text-right">Quantity</th><th class="text-right">Rate</th><th class="text-right">Amount</th></tr></thead>
      <tbody>
        {#each groups as [group, amount]}
          <tr class="border-t-2 border-gray-200 font-semibold"><td class="py-1.5">{group}</td><td></td><td></td><td class="text-right">{inr(amount)}</td></tr>
          {#each estimate.lines.filter(l => l.group === group) as l}
            <tr class="border-t border-gray-100" title={l.basis}><td class="py-1 pl-3 text-gray-700">{l.item}</td>
              <td class="text-right text-gray-600">{l.quantity.toLocaleString('en-IN')} {l.unit}</td>
              <td class="text-right text-gray-600">{inr(l.rate)}</td><td class="text-right">{inr(l.amount)}</td></tr>
          {/each}
        {/each}
        <tr class="border-t-2 border-gray-200"><td class="py-1.5">Contingency (5%)</td><td></td><td></td><td class="text-right">{inr(estimate.contingency)}</td></tr>
        <tr class="border-t-2 border-slate-800 font-bold"><td class="py-1.5">Total</td><td></td><td></td><td class="text-right">{inr(estimate.total)}</td></tr>
      </tbody>
    </table>
    <details><summary class="text-xs cursor-pointer">Assumptions ({estimate.assumptions.length})</summary>
      <ul class="list-disc pl-4 text-xs text-gray-700 mt-1 space-y-0.5" data-testid="estimate-assumptions">{#each estimate.assumptions as a}<li>{a}</li>{/each}</ul></details>
  {:else}
    <p class="text-sm text-gray-600 p-6 text-center" data-testid="no-estimate">No cost estimate yet. Choose a finish and city, then estimate.</p>
  {/if}

  {#if interior}
    <div class="rounded-lg border border-indigo-200 bg-indigo-50/50 p-3 space-y-2" data-testid="interiors-budget">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <p class="text-sm font-semibold text-gray-800">Interiors — {interior.brief.title ?? interior.brief.base_theme}
          <span class="text-xs font-normal text-gray-500">(latest design proposal, {interior.budget.tier}; separate from construction)</span></p>
        <p class="text-lg font-bold" data-testid="interiors-total">{lakh(interior.budget.total)}</p>
      </div>
      <ul class="text-xs text-gray-700 grid sm:grid-cols-3 gap-x-4">
        {#each Object.entries(interior.budget.groups) as [g, v]}<li class="flex justify-between"><span>{g}</span><span>{inr(v)}</span></li>{/each}
      </ul>
      <ul class="list-disc pl-4 text-[11px] text-gray-600">{#each interior.budget.notes as n}<li>{n}</li>{/each}</ul>
    </div>
  {/if}
</section>
