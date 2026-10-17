<script lang="ts" module>
  // One Maps JavaScript API load per page.
  let mapsLoad: Promise<typeof google.maps> | null = null;
  function loadMaps(key: string): Promise<typeof google.maps> {
    mapsLoad ??= new Promise((resolve, reject) => {
      const w = window as unknown as Record<string, unknown>;
      w.__greenprintMapsReady = () => resolve((window as unknown as { google: typeof google }).google.maps);
      const s = document.createElement('script');
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=__greenprintMapsReady`;
      s.async = true;
      s.onerror = () => { mapsLoad = null; reject(new Error('The map could not load.')); };
      document.head.appendChild(s);
    });
    return mapsLoad;
  }
</script>

<script lang="ts">
  // Pin the plot on Google Maps: find the address (Geocoding), then click or drag the pin onto the plot.
  // The pin gives the green check its exact location (city, hemisphere) and, later, the site's sun and shade.
  import { onMount } from 'svelte';
  import { engine, EngineError, type Site } from './engine';

  let { projectId, site = null, onPinned = () => {} }: { projectId: string; site?: Site | null; onPinned?: (s: Site) => void } = $props();

  let el: HTMLDivElement;
  let query = $state('');
  let error = $state<string | null>(null);
  let saving = $state(false);
  let ready = $state(false);
  let picked = $state<{ lat: number; lng: number } | null>(null);
  let current = $state<Site | null>(null);
  let maps: typeof google.maps;
  let map: google.maps.Map;
  let marker: google.maps.marker.AdvancedMarkerElement;
  let geocoder: google.maps.Geocoder;

  $effect(() => { current = site; });

  onMount(async () => {
    try {
      const { key } = await (await fetch('/api/maps/config')).json();
      if (!key) { error = 'The map is not available right now.'; return; }
      maps = await loadMaps(key);
      const [{ Map }, { AdvancedMarkerElement }, { Geocoder }] = await Promise.all([
        maps.importLibrary('maps') as Promise<google.maps.MapsLibrary>, maps.importLibrary('marker') as Promise<google.maps.MarkerLibrary>,
        maps.importLibrary('geocoding') as Promise<google.maps.GeocodingLibrary>]);
      const start = site ? { lat: site.lat, lng: site.lng } : { lat: 20.59, lng: 78.96 };
      map = new Map(el, { center: start, zoom: site ? 19 : 5, mapTypeId: 'hybrid', mapId: 'DEMO_MAP_ID', tilt: 0,
                          streetViewControl: false, fullscreenControl: false, mapTypeControl: false });
      marker = new AdvancedMarkerElement({ map, position: site ? start : null, gmpDraggable: true, title: 'Your plot' });
      geocoder = new Geocoder();
      map.addListener('click', (e: google.maps.MapMouseEvent) => { if (e.latLng) place(e.latLng.toJSON()); });
      marker.addListener('dragend', () => { const p = marker.position as google.maps.LatLngLiteral | null; if (p) picked = { lat: p.lat, lng: p.lng }; });
      ready = true;
    } catch (e) {
      error = e instanceof Error ? e.message : 'The map could not load.';
    }
  });

  function place(p: { lat: number; lng: number }) {
    marker.position = p;
    picked = p;
  }

  async function find() {
    if (!query.trim() || !ready) return;
    error = null;
    try {
      const { results } = await geocoder.geocode({ address: query.trim() });
      if (!results.length) { error = 'No place found. Try a fuller address.'; return; }
      const loc = results[0].geometry.location.toJSON();
      map.setCenter(loc); map.setZoom(19);
      place(loc);
    } catch { error = 'No place found. Try a fuller address.'; }
  }

  async function save() {
    if (!picked || saving) return;
    saving = true; error = null;
    try {
      const { results } = await geocoder.geocode({ location: picked });
      const comp = (type: string) => results.flatMap(r => r.address_components).find(c => c.types.includes(type))?.long_name ?? '';
      const s: Site = { lat: +picked.lat.toFixed(6), lng: +picked.lng.toFixed(6), address: (results[0]?.formatted_address ?? '').replace(/^[2-9CFGHJMPQRVWX]{4,8}\+[2-9CFGHJMPQRVWX]{2,3},?\s*/, ''),   // no plus code
        city: comp('locality') || comp('administrative_area_level_2') || comp('administrative_area_level_1'), country: comp('country') };
      current = await engine.setSite(projectId, s);
      picked = null;
      onPinned(current);
    } catch (e) {
      error = e instanceof EngineError ? e.message : 'Could not save the pin.';
    } finally { saving = false; }
  }
</script>

<div class="space-y-2" data-testid="plot-pin">
  <div class="flex gap-2">
    <input class="flex-1 rounded-lg border border-gray-300 px-2 py-1.5 text-sm" bind:value={query} aria-label="Plot address"
      placeholder="Search the plot's address or area" onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); find(); } }} />
    <button type="button" class="px-3 py-1.5 rounded-lg border border-gray-300 text-sm disabled:opacity-50" disabled={!ready || !query.trim()} onclick={find}>Find</button>
  </div>
  <div bind:this={el} class="w-full h-64 rounded-lg border border-gray-200 bg-gray-100" role="application" aria-label="Map: click to place your plot"></div>
  <div class="flex flex-wrap items-center gap-2 text-xs">
    {#if current}<span class="text-emerald-800" data-testid="pinned-site">📍 {current.address || `${current.lat}, ${current.lng}`}</span>{/if}
    {#if picked}
      <button type="button" class="ml-auto px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50" disabled={saving} onclick={save}>
        {saving ? 'Saving…' : 'Use this location'}</button>
    {:else if ready && !current}
      <span class="text-gray-500">Search, then click the map on your plot (or drag the pin).</span>
    {/if}
  </div>
  {#if error}<p role="alert" class="text-xs text-red-700">{error}</p>{/if}
</div>
