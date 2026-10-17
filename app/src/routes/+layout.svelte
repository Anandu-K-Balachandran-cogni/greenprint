<script>
  import '../app.css';
  import { initializeLocale } from '$lib/i18n';
  onMount(initializeLocale);
  import { browser } from '$app/environment';
  import { onMount } from 'svelte';
  import { themePreference } from '$lib/stores/theme';
  import DeploymentNotice from '$lib/components/DeploymentNotice.svelte';
  let { children } = $props();
</script>

{@render children()}
<DeploymentNotice />
