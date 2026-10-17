import { env } from '$env/dynamic/private';
import { json, redirect, type Handle } from '@sveltejs/kit';
import { authState, SESSION_COOKIE, verifySession } from '$lib/server/auth';

// Every dynamic route (pages and APIs) requires the shared login when it is configured.
// Static files are served before this hook and contain no project data.
const PUBLIC = new Set(['/login']);

export const handle: Handle = async ({ event, resolve }) => {
  const auth = authState(env);
  if (auth === 'disabled') return resolve(event);
  if (auth === 'misconfigured') return new Response('Login is not configured on this server.', { status: 503 });
  const path = event.url.pathname;
  if (PUBLIC.has(path) || verifySession(auth, event.cookies.get(SESSION_COOKIE))) return resolve(event);
  if (path.startsWith('/api/') || path === '/mcp') return json({ message: 'Sign in required' }, { status: 401 });
  redirect(303, `/login?next=${encodeURIComponent(path + event.url.search)}`);
};
