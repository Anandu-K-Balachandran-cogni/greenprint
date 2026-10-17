import { env } from '$env/dynamic/private';
import { fail, redirect } from '@sveltejs/kit';
import { allowAttempt, authState, checkCredentials, clearAttempts, createSession, safeNext, SESSION_COOKIE, verifySession } from '$lib/server/auth';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = ({ cookies, url }) => {
  const auth = authState(env);
  if (auth === 'disabled' || (typeof auth === 'object' && verifySession(auth, cookies.get(SESSION_COOKIE)))) {
    redirect(303, safeNext(url.searchParams.get('next')));
  }
  return { next: safeNext(url.searchParams.get('next')) };
};

export const actions: Actions = {
  default: async ({ request, cookies, url, getClientAddress }) => {
    const auth = authState(env);
    if (typeof auth !== 'object') return fail(503, { error: 'Login is not configured on this server.', id: '' });
    const form = await request.formData();
    const id = String(form.get('id') ?? '').slice(0, 200);
    const password = String(form.get('password') ?? '').slice(0, 500);
    const client = getClientAddress();
    if (!allowAttempt(client)) return fail(429, { error: 'Too many attempts. Try again in 15 minutes.', id });
    if (!checkCredentials(auth, id, password)) return fail(401, { error: 'The ID or password is not correct.', id });
    clearAttempts(client);
    const session = createSession(auth);
    cookies.set(SESSION_COOKIE, session.value, {
      path: '/', httpOnly: true, sameSite: 'lax', secure: url.protocol === 'https:', maxAge: session.maxAge,
    });
    redirect(303, safeNext(String(form.get('next') ?? '')));
  },
};
