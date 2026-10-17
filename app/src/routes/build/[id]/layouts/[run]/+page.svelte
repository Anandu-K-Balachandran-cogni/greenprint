<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import LayoutPreview from '$lib/constructos/LayoutPreview.svelte';
  import LayoutEvidence from '$lib/constructos/LayoutEvidence.svelte';
  import ReviewPanel from '$lib/constructos/ReviewPanel.svelte';
  import { compileAllocation, CompileError } from '$lib/constructos/compiler';
  import { engine, layouts, EngineError, ACTIVE_RUN, type Change, type EngineProject, type LayoutRun } from '$lib/constructos/engine';
  import { localStore } from '$lib/services/datastore';

  const projectId = $derived(page.params.id ?? '');
  const runId = $derived(page.params.run ?? '');
  let project = $state<EngineProject | null>(null);
  let run = $state<LayoutRun | null>(null);
  let loadError = $state<string | null>(null);
  let opening = $state<number | null>(null);
  let openError = $state<string | null>(null);
  let floorShown = $state<Record<number, number>>({});

  let parent = $state<LayoutRun | null>(null);
  let reviewing = $state<number | null>(null);
  let revising = $state<number | null>(null);
  let requests = $state<Record<number, string>>({});
  let questions = $state<Record<number, string | null>>({});
  let rejectedReasons = $state<Record<number, string[]>>({});
  let aiError = $state<Record<number, string | null>>({});

  const stale = $derived(!!project && !!run && project.brief_version !== run.brief_version);
  const parentCandidate = $derived(run?.parent && parent ? parent.candidates[run.parent.candidate] : null);

  // Reload whenever the route changes (a revision opens in this same page). Runs solve in the
  // background: poll while queued/running, showing options as each one is published.
  $effect(() => {
    const [pid, rid] = [projectId, runId];
    run = null; parent = null; loadError = null; opening = null; questions = {}; aiError = {}; requests = {}; reconnecting = false;
    let cancelled = false;
    (async () => {
      try {
        const [p, r] = await Promise.all([engine.getProject(pid), layouts.get(pid, rid)]);
        if (cancelled) return;
        project = p; run = r;
        if (r.parent) parent = await layouts.get(pid, r.parent.run_id);
        // A dropped connection or a 5xx is retried with backoff (about 1.5 minutes in all);
        // anything else (e.g. 404) stops polling at once.
        let failures = 0;
        while (!cancelled && run && ACTIVE_RUN.includes(run.status)) {
          await new Promise(res => setTimeout(res, failures ? Math.min(10_000, 1500 * 2 ** failures) : 1500));
          if (cancelled) return;
          try {
            const next = await layouts.get(pid, rid);
            failures = 0; reconnecting = false;
            if (!cancelled) run = { ...next, candidates: next.candidates.map((c, k) => ({ ...c, review: run?.candidates[k]?.review ?? c.review })) };
          } catch (e) {
            if ((e instanceof EngineError && e.status < 500) || ++failures > 12) throw e;
            reconnecting = true;
          }
        }
      } catch (e) {
        if (!cancelled) loadError = e instanceof EngineError ? e.message : 'Could not reach the Greenprint engine.';
      }
    })();
    return () => { cancelled = true; };
  });
  const solving = $derived(!!run && ACTIVE_RUN.includes(run.status));
  let reconnecting = $state(false);

  async function runReview(k: number) {
    if (!run) return;
    reviewing = k; aiError[k] = null;
    try { run.candidates[k].review = await layouts.review(projectId, runId, k); }
    catch (e) { aiError[k] = e instanceof EngineError ? e.message : 'The review failed.'; }
    finally { reviewing = null; }
  }

  async function revise(k: number, body: { request?: string; changes?: Change[] }) {
    revising = k; aiError[k] = null; questions[k] = null; rejectedReasons[k] = [];
    try {
      const result = await layouts.revise(projectId, runId, k, body);
      if (result.status === 'clarify') { questions[k] = result.question; rejectedReasons[k] = result.rejected; return; }
      await goto(`/build/${projectId}/layouts/${result.run.id}`);
    } catch (e) {
      aiError[k] = e instanceof EngineError ? e.message : 'The change could not be planned.';
    } finally { revising = null; }
  }

  const editorProjectId = (k: number) => `cos-${runId}-${k}`;

  async function openInEditor(k: number) {
    if (!run || !project) return;
    opening = k; openError = null;
    try {
      const id = editorProjectId(k);
      // Reopening keeps any manual edits made since the first open.
      if (!(await localStore.has(id))) {
        const { project: compiled, problems } = compileAllocation(run.candidates[k].allocation, {
          projectId: id, name: `${project.name} — option ${k + 1}`,
          link: { engineProjectId: projectId, layoutRunId: runId, candidate: k, briefVersion: run.brief_version, briefHash: run.brief_hash },
        });
        if (problems.length) throw new CompileError(problems.join(' '));
        await localStore.save(compiled);
      }
      await goto(`/editor?id=${encodeURIComponent(id)}`);
    } catch (e) {
      openError = e instanceof Error ? e.message : 'The layout could not be compiled.';
      opening = null;
    }
  }

  function statusText(status: string) {
    return {
      OPTIMAL: 'The best layout for your brief', FEASIBLE: 'A good layout that meets every requirement',
      INFEASIBLE: 'No layout can meet every requirement; try relaxing some', UNKNOWN: 'No layout found in time; try again or relax some requirements',
      MODEL_INVALID: 'This brief could not be laid out',
    }[status] ?? status;
  }
</script>

<svelte:head><title>Greenprint — Layout options</title></svelte:head>

<div class="min-h-screen bg-gray-50">
  <header class="bg-gradient-to-r from-slate-800 to-slate-700">
    <div class="max-w-6xl mx-auto px-6 py-5 flex items-center justify-between gap-4">
      <div>
        <a href="/build/{projectId}" class="text-xs text-white/60 hover:text-white">← Requirements</a>
        <h1 class="text-2xl font-bold text-white">{project?.name ?? 'Layout options'}</h1>
      </div>
      {#if run}<p class="text-sm text-white/70">From brief v{run.brief_version} · {run.candidates.length} option(s)</p>{/if}
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-6 py-8 space-y-6" data-run-status={run?.status ?? 'loading'}>
    {#if loadError}
      <p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{loadError}</p>
    {:else if !run}
      <p role="status" class="text-gray-500">Loading…</p>
    {:else}
      {#if stale}
        <p role="status" class="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          The brief has changed since these layouts were generated (now v{project?.brief_version}). They remain available for comparison but do not reflect the current brief.
        </p>
      {/if}
      {#if solving && run.job}
        <div role="status" class="flex items-center gap-3 text-sm text-indigo-900 bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-2" data-testid="run-progress">
          <span class="inline-block h-3 w-3 rounded-full border-2 border-indigo-600 border-t-transparent animate-spin" aria-hidden="true"></span>
          <span>{run.status === 'queued' ? 'Queued' : `Solving option ${Math.min(run.job.progress.done + 1, run.job.progress.total)} of ${run.job.progress.total}`} · this runs in the background, so you can leave and come back.{reconnecting ? ' Connection interrupted — reconnecting…' : ''}</span>
        </div>
      {:else if run.status === 'failed' || run.status === 'interrupted'}
        <p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2" data-testid="run-error">
          {run.status === 'interrupted' ? 'Generation was interrupted.' : 'Generation failed.'} {run.job?.error ?? ''}
          {#if run.candidates.length} The options below finished before it stopped.{/if}
        </p>
      {/if}
      {#if openError}<p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{openError}</p>{/if}

      {#if run.kind === 'revision' && run.parent}
        {@const after = run.candidates[0]}
        <section class="bg-white rounded-xl border border-indigo-200 p-5 space-y-3" aria-label="Revision" data-testid="revision">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h2 class="font-semibold text-gray-900">Revision of option {run.parent.candidate + 1}</h2>
              {#if run.request}<p class="text-sm text-gray-700 mt-1">“{run.request}”</p>{/if}
            </div>
            <a class="text-sm text-indigo-700 hover:underline" href="/build/{projectId}/layouts/{run.parent.run_id}">← Original options</a>
          </div>
          <ul class="text-sm list-disc pl-5" aria-label="Changes applied" data-testid="changes-applied">
            {#each run.change_descriptions ?? [] as d}<li>{d}</li>{/each}
          </ul>
          {#if run.explanation}<p class="text-xs text-gray-600">{run.explanation}{run.interpreted_by ? ` (${run.interpreted_by})` : ''}</p>{/if}
          {#if run.rejected?.length}<p class="text-xs text-amber-800">Not applied: {run.rejected.join(' ')}</p>{/if}
          {#if parentCandidate?.allocation.footprint && after?.allocation.footprint}
            {@const before = new Map(parentCandidate.allocation.spaces.map(s => [`${s.floor}:${s.id}`, s]))}
            {@const rows = after.allocation.spaces.filter(s => !s.filler).map(s => ({ s, b: before.get(`${s.floor}:${s.id}`) }))}
            {@const removed = parentCandidate.allocation.spaces.filter(s => !s.filler && !after.allocation.spaces.some(x => x.id === s.id && x.floor === s.floor))}
            <table class="w-full text-xs" aria-label="Before and after">
              <thead><tr class="text-left text-gray-500"><th class="py-1">Space</th><th>Before</th><th>After</th><th>Change</th><th>Moved</th></tr></thead>
              <tbody>
                {#each rows as { s, b } (`${s.floor}:${s.id}`)}
                  {@const delta = b ? s.area_m2 - b.area_m2 : null}
                  <tr class="border-t border-gray-100" data-row={s.id}>
                    <td class="py-1 text-gray-900">{s.name}</td><td>{b ? `${b.area_m2.toFixed(1)} m²` : 'new'}</td><td>{s.area_m2.toFixed(1)} m²</td>
                    <td class={delta == null ? '' : delta > 0.05 ? 'text-emerald-700 font-medium' : delta < -0.05 ? 'text-red-700' : 'text-gray-400'}>{delta == null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)} m²`}</td>
                    <td>{b && (b.x_mm !== s.x_mm || b.y_mm !== s.y_mm) ? 'yes' : b ? '—' : ''}</td>
                  </tr>
                {/each}
                {#each removed as s}<tr class="border-t border-gray-100 text-gray-500"><td class="py-1">{s.name}</td><td>{s.area_m2.toFixed(1)} m²</td><td>removed</td><td></td><td></td></tr>{/each}
              </tbody>
            </table>
          {/if}
        </section>
      {/if}

      <div class="grid gap-6 lg:grid-cols-2">
        {#each run.candidates as c, k}
          {@const a = c.allocation}
          {@const storeys = a.spaces.length ? Math.max(...a.spaces.map(s => s.floor)) + 1 : 0}
          {@const errors = c.report?.findings.filter(f => f.severity === 'error') ?? []}
          {@const warnings = c.report?.findings.filter(f => f.severity === 'warning') ?? []}
          <section class="bg-white rounded-xl border border-gray-200 p-5 space-y-4" aria-label="Option {k + 1}" data-testid="candidate-{k}">
            <div class="flex items-start justify-between gap-3">
              <div>
                <h2 class="font-semibold text-gray-900">Option {k + 1}</h2>
                <p class="text-xs text-gray-500" data-testid="solver-status" data-status={a.status}>{statusText(a.status)}</p>
              </div>
              {#if c.report}
                <span class="text-xs font-semibold px-2 py-1 rounded {c.report.status === 'PASS' ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'}" data-testid="check-status">
                  Checks {c.report.status === 'PASS' ? 'passed' : 'failed'}
                </span>
              {/if}
            </div>

            {#if a.footprint}
              {#if storeys > 1}
                <div class="flex gap-2" role="group" aria-label="Floor">
                  {#each Array.from({ length: storeys }, (_, i) => i) as f}
                    <button type="button" class="px-3 py-1 text-xs rounded-full border {(floorShown[k] ?? 0) === f ? 'bg-slate-800 text-white border-slate-800' : 'border-gray-300'}"
                      onclick={() => floorShown[k] = f}>{f === 0 ? 'Ground' : f === 1 ? 'First' : `Floor ${f}`}</button>
                  {/each}
                </div>
              {/if}
              <LayoutPreview allocation={a} floor={floorShown[k] ?? 0} label="Option {k + 1} floor plan" />
              <p class="text-sm text-gray-600">
                Footprint {(a.footprint.width_mm / 1000).toFixed(1)} × {(a.footprint.depth_mm / 1000).toFixed(1)} m ·
                {a.spaces.filter(s => !s.filler).length} spaces
              </p>
              {#if errors.length || warnings.length}
                <ul class="text-xs space-y-1" aria-label="Check findings">
                  {#each errors as f}<li class="text-red-700">✕ {f.message}</li>{/each}
                  {#each warnings as f}<li class="text-amber-700">! {f.message}</li>{/each}
                </ul>
              {/if}
              {#if c.report?.evidence?.length}<LayoutEvidence report={c.report} />{/if}

              {#if c.review}
                <ReviewPanel review={c.review} busy={revising !== null} onapply={(change) => revise(k, { changes: [change] })} />
              {:else}
                <button type="button" class="w-full px-4 py-2 rounded-lg border border-indigo-300 text-indigo-800 text-sm font-medium hover:bg-indigo-50 disabled:opacity-50"
                  disabled={reviewing !== null || c.report?.status !== 'PASS'} onclick={() => runReview(k)}>
                  {reviewing === k ? 'Reviewing…' : 'Architect’s review'}
                </button>
              {/if}
              <form class="space-y-2" onsubmit={(e) => { e.preventDefault(); if (requests[k]?.trim()) revise(k, { request: requests[k] }); }}>
                <label class="block"><span class="text-xs font-medium text-gray-700">Request changes</span>
                  <textarea class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm min-h-16" maxlength="1000" bind:value={requests[k]}
                    aria-label="Request changes to option {k + 1}" placeholder="e.g. Make the kids bedroom bigger and keep the kitchen at the back"></textarea></label>
                {#if questions[k]}<p class="text-sm text-indigo-900 bg-indigo-50 rounded-lg px-3 py-2" data-testid="clarification">{questions[k]}</p>{/if}
                {#if rejectedReasons[k]?.length}<ul class="text-xs text-amber-900 space-y-0.5" data-testid="rejected-changes">{#each rejectedReasons[k] as r}<li>Not applied: {r}</li>{/each}</ul>{/if}
                {#if aiError[k]}<p role="alert" class="text-sm text-red-700 bg-red-50 rounded-lg px-3 py-2">{aiError[k]}</p>{/if}
                <button type="submit" class="w-full px-4 py-2 rounded-lg bg-slate-800 text-white text-sm font-medium disabled:opacity-50"
                  disabled={revising !== null || !requests[k]?.trim()}>{revising === k ? 'Re-planning…' : 'Re-plan with these changes'}</button>
              </form>
              <button type="button" class="w-full px-4 py-2.5 bg-blue-600 text-white rounded-lg font-semibold text-sm hover:bg-blue-700 disabled:opacity-50"
                disabled={opening !== null || c.report?.status !== 'PASS'} onclick={() => openInEditor(k)}>
                {opening === k ? 'Opening…' : c.report?.status === 'PASS' ? 'Open in editor' : 'Fails checks — cannot open'}
              </button>
            {:else}
              <p class="text-sm text-gray-700 bg-gray-50 rounded-lg p-3" data-testid="solver-message">{a.message}</p>
            {/if}
          </section>
        {/each}
      </div>

      <section class="bg-white rounded-xl border border-gray-200 p-5">
        <h2 class="font-semibold text-gray-900 mb-2">Design assumptions</h2>
        <ul class="list-disc pl-5 text-sm text-gray-700 space-y-1">
          {#each run.candidates[0]?.allocation.assumptions ?? run.program.assumptions as a}<li>{a}</li>{/each}
        </ul>
      </section>
    {/if}
  </main>
</div>
