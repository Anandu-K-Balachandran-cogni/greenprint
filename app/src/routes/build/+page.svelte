<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { engine, EngineError, type SketchReading } from '$lib/constructos/engine';

  let name = $state('');
  let text = $state('');
  let busy = $state(false);
  let errorMessage = $state<string | null>(null);
  let projects = $state<{ id: string; name: string; created_at: string; brief_version: number }[]>([]);
  let model = $state<string | null>(null);
  // Sketches or plot drawings, resized in the browser (long side 1600 px, JPEG) before Gemini reads them.
  let sketches = $state<{ name: string; url: string; data: string }[]>([]);
  let reading = $state<SketchReading | null>(null);
  let readingBusy = $state(false);
  let projectId: string | null = null;

  async function shrink(file: File): Promise<{ url: string; data: string }> {
    const bitmap = await createImageBitmap(file);
    const k = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * k); canvas.height = Math.round(bitmap.height * k);
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const url = canvas.toDataURL('image/jpeg', 0.85);
    return { url, data: url.slice(url.indexOf(',') + 1) };
  }

  async function addSketches(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    errorMessage = null;
    for (const file of Array.from(input.files ?? []).slice(0, 3 - sketches.length)) {
      try { sketches = [...sketches, { name: file.name, ...(await shrink(file)) }]; }
      catch { errorMessage = `Could not open ${file.name}. Please use a JPEG, PNG or WebP photo.`; }
    }
    input.value = '';
  }

  async function ensureProject(): Promise<string> {
    projectId ??= (await engine.createProject(name.trim() || 'Untitled building')).id;
    return projectId;
  }

  async function readSketches() {
    if (!sketches.length || readingBusy) return;
    readingBusy = true; errorMessage = null;
    try {
      const id = await ensureProject();
      reading = await engine.readSketch(id, { text: text.trim() || undefined, images: sketches.map(s => ({ data: s.data, mime: 'image/jpeg' as const })) });
      text = reading.english_brief;
    } catch (e) {
      errorMessage = e instanceof EngineError ? e.message : 'Could not read the sketch. Please try again.';
    } finally { readingBusy = false; }
  }

  onMount(async () => {
    try { projects = await engine.listProjects(); } catch { /* engine offline is reported on submit */ }
    try { model = (await engine.models()).reasoning; } catch { model = null; }
  });

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!text.trim() || busy) return;
    busy = true;
    errorMessage = null;
    try {
      const id = await ensureProject();
      await engine.extractBrief(id, text.trim());
      await goto(`/build/${id}`);
    } catch (e) {
      errorMessage = e instanceof EngineError ? e.message : 'Could not reach the Greenprint engine.';
      busy = false;
    }
  }
</script>

<svelte:head><title>Greenprint — New building</title></svelte:head>

<div class="min-h-screen bg-gray-50">
  <header class="bg-gradient-to-r from-slate-800 to-slate-700">
    <div class="max-w-4xl mx-auto px-6 py-5 flex items-center justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-white">Greenprint</h1>
        <p class="text-sm text-emerald-300">AI-designed green homes, planned for your plot and your climate.</p>
        <p class="text-sm text-white/60">Describe your home. We generate an editable plan, 3D model, services, costs and interiors.</p>
      </div>
      <div class="flex items-center gap-4">
        <a href="/" class="text-sm text-white/70 hover:text-white underline">Project library</a>
        <form method="POST" action="/logout"><button class="text-sm text-white/70 hover:text-white underline">Sign out</button></form>
      </div>
    </div>
  </header>

  <main class="max-w-4xl mx-auto px-6 py-8 space-y-8">
    <form class="bg-white rounded-xl border border-gray-200 p-6 space-y-4 shadow-sm" onsubmit={submit}>
      <h2 class="text-lg font-semibold text-gray-900">New building from a brief</h2>
      <label class="block">
        <span class="text-sm font-medium text-gray-700">Project name</span>
        <input class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" bind:value={name} placeholder="Family house" maxlength="120" />
      </label>
      <label class="block">
        <span class="text-sm font-medium text-gray-700">Building brief</span>
        <textarea class="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm min-h-40" bind:value={text} maxlength="8000"
          placeholder="e.g. A house on a 40 x 60 ft site with three bedrooms across two floors, parking for two cars, an open kitchen and the parents' bedroom on the ground floor."></textarea>
      </label>
      <div class="rounded-lg border border-dashed border-emerald-300 bg-emerald-50/40 p-3 space-y-2" data-testid="sketch-intake">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p class="text-sm font-medium text-gray-800">Have a hand sketch or a plot drawing?</p>
            <p class="text-xs text-gray-500">Add up to 3 photos. We read the sizes, rooms, road side and north, along with any notes you add (Hindi, Tamil, Japanese, Bahasa and more).</p>
          </div>
          <label class="px-3 py-1.5 rounded-lg border border-emerald-600 text-emerald-700 text-sm font-medium cursor-pointer hover:bg-emerald-50 {sketches.length >= 3 ? 'opacity-50 pointer-events-none' : ''}">
            Add sketch or photo
            <input type="file" accept="image/*" multiple class="sr-only" aria-label="Add sketch or plot photo" onchange={addSketches} disabled={sketches.length >= 3} />
          </label>
        </div>
        {#if sketches.length}
          <div class="flex flex-wrap items-end gap-2">
            {#each sketches as s, i}
              <figure class="relative">
                <img src={s.url} alt="Sketch {i + 1}: {s.name}" class="h-20 w-28 object-cover rounded border border-gray-300" />
                <button type="button" class="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-gray-800 text-white text-xs leading-5"
                  aria-label="Remove {s.name}" onclick={() => { sketches = sketches.filter((_, j) => j !== i); }}>×</button>
              </figure>
            {/each}
            <button type="button" class="ml-auto px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50 hover:bg-emerald-700"
              disabled={readingBusy || busy} onclick={readSketches}>{readingBusy ? 'Reading your sketch…' : 'Read my sketch'}</button>
          </div>
        {/if}
        {#if reading}
          <div class="text-xs text-gray-700 bg-white rounded border border-emerald-200 p-2 space-y-1" data-testid="sketch-reading">
            <p class="font-medium text-emerald-800">We read your {reading.image_notes.length > 1 ? 'images' : 'image'}{reading.language !== 'none' && reading.language !== 'English' ? ` and your words in ${reading.language}` : ''}; the brief above now includes it. Please check it.</p>
            <ul class="list-disc pl-4">{#each reading.image_notes as n}<li>{n}</li>{/each}</ul>
            {#if reading.unclear.length}<p class="text-amber-700">Could not read clearly: {reading.unclear.join('; ')}</p>{/if}
          </div>
        {/if}
      </div>
      {#if errorMessage}<p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{errorMessage}</p>{/if}
      <div class="flex items-center justify-between gap-4">
        <p class="text-xs text-gray-500">{model ? 'You review everything before anything is designed.' : 'Greenprint is not reachable right now.'}</p>
        <button type="submit" disabled={busy || !text.trim()}
          class="px-5 py-2.5 bg-blue-600 text-white rounded-lg font-semibold text-sm disabled:opacity-50 hover:bg-blue-700">
          {busy ? 'Reading brief…' : 'Extract requirements'}
        </button>
      </div>
    </form>

    {#if projects.length}
      <section aria-labelledby="recent-heading">
        <h2 id="recent-heading" class="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Recent buildings</h2>
        <ul class="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
          {#each projects as p (p.id)}
            <li>
              <a href="/build/{p.id}" class="flex items-center justify-between px-4 py-3 hover:bg-gray-50">
                <span class="font-medium text-gray-900">{p.name}</span>
                <span class="text-xs text-gray-500">{p.brief_version ? `Brief v${p.brief_version} confirmed` : 'Brief not confirmed'}</span>
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  </main>
</div>
