<script lang="ts">
  import { enhance } from '$app/forms';
  let { data, form } = $props();
  let busy = $state(false);
</script>

<svelte:head><title>Greenprint — Sign in</title></svelte:head>

<div class="min-h-screen bg-gradient-to-br from-slate-800 to-slate-700 flex items-center justify-center px-4">
  <form method="POST" class="w-full max-w-sm bg-white rounded-xl shadow-lg p-6 space-y-4" aria-labelledby="login-heading"
    use:enhance={() => { busy = true; return async ({ update }) => { await update({ reset: false }); busy = false; }; }}>
    <div>
      <h1 id="login-heading" class="text-xl font-bold text-gray-900">Greenprint</h1>
      <p class="text-sm text-emerald-700">AI-designed green homes, planned for your plot and your climate.</p>
      <p class="text-sm text-gray-500">Sign in to continue.</p>
    </div>
    <input type="hidden" name="next" value={data.next} />
    <label class="block text-sm font-medium text-gray-700">ID
      <input name="id" autocomplete="username" required defaultValue={form?.id ?? ''}
        class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
    </label>
    <label class="block text-sm font-medium text-gray-700">Password
      <input name="password" type="password" autocomplete="current-password" required
        class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
    </label>
    {#if form?.error}<p role="alert" class="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{form.error}</p>{/if}
    <button type="submit" disabled={busy} class="w-full py-2.5 bg-blue-600 text-white rounded-lg font-semibold text-sm hover:bg-blue-700 disabled:opacity-50">
      {busy ? 'Signing in…' : 'Sign in'}
    </button>
  </form>
</div>
