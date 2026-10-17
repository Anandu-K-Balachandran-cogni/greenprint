import { env } from '$env/dynamic/private';
import { error, type RequestHandler } from '@sveltejs/kit';

// Same-origin gateway to the Python engine. Only known routes are forwarded and
// the browser never sees engine credentials or its address.
const ALLOWED = [/^health$/, /^ai\/models$/, /^projects$/, /^projects\/[0-9a-f-]{36}(\/([a-z]+|[0-9a-f-]{36}|\d{1,2}))*$/];
const MAX_BODY = 256 * 1024;
// Sketch and plot photos (up to three, resized in the browser) need more room.
const MAX_BODY_FOR = [{ re: /\/brief\/read$/, max: 12 * 1024 * 1024 }, { re: /\/renders$/, max: 8 * 1024 * 1024 },
  { re: /\/green$/, max: 4 * 1024 * 1024 }, { re: /\/agents\/[a-z]+\/chat$/, max: 4 * 1024 * 1024 }];

// On Cloud Run the engine is private: calls carry a Google-signed ID token for the
// engine's URL, fetched from the metadata server for this service's identity.
let cachedToken: { value: string; expires: number } | null = null;
async function identityToken(audience: string): Promise<string | null> {
  if (env.ENGINE_AUTH !== 'google-id-token') return null;
  if (cachedToken && cachedToken.expires > Date.now()) return cachedToken.value;
  const res = await fetch(`http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${encodeURIComponent(audience)}`,
    { headers: { 'Metadata-Flavor': 'Google' }, signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`metadata server returned ${res.status}`);
  cachedToken = { value: await res.text(), expires: Date.now() + 45 * 60_000 }; // tokens last 60 minutes
  return cachedToken.value;
}

const forward: RequestHandler = async ({ params, request, url }) => {
  const path = params.path ?? '';
  if (!ALLOWED.some(re => re.test(path))) error(404, 'Not found');
  const base = (env.ENGINE_URL || 'http://127.0.0.1:8787').replace(/\/$/, '');
  const body = request.method === 'GET' ? undefined : await request.text();
  const limit = MAX_BODY_FOR.find(l => l.re.test(path))?.max ?? MAX_BODY;
  if (body && body.length > limit) error(413, 'Request too large');
  let upstream: Response;
  try {
    const headers: Record<string, string> = body ? { 'content-type': 'application/json' } : {};
    const token = await identityToken(base);
    if (token) headers.authorization = `Bearer ${token}`;
    upstream = await fetch(`${base}/${path}${url.search}`, {
      method: request.method,
      headers,
      body,
      signal: AbortSignal.timeout(180_000),
    });
  } catch {
    error(502, 'Greenprint engine is not reachable');
  }
  const headers: Record<string, string> = { 'content-type': upstream.headers.get('content-type') ?? 'application/json' };
  const disposition = upstream.headers.get('content-disposition');
  if (disposition && /^attachment; filename="[\w.-]+"$/.test(disposition)) headers['content-disposition'] = disposition;
  return new Response(upstream.body, { status: upstream.status, headers });
};

export const GET = forward;
export const POST = forward;
export const PUT = forward;
