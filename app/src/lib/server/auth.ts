// Single shared login for a deployed Greenprint: one ID and one password.
//
// The server holds only a scrypt hash of the password (APP_LOGIN_PASSWORD_HASH) and a
// random signing secret (APP_SESSION_SECRET). A session is an HttpOnly cookie
// "<expiry>.<hmac>" whose HMAC also covers the login ID and the password hash, so
// changing the password signs everyone out. Without APP_LOGIN_ID the login is off
// (local development); on Cloud Run a missing configuration fails closed.
import { createHmac, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

export const SESSION_COOKIE = 'cos_session';
export const SESSION_DAYS = 7;
const SCRYPT = { N: 16384, r: 8, p: 1 } as const;

export interface AuthConfig { id: string; passwordHash: string; secret: string }
export type AuthState = AuthConfig | 'disabled' | 'misconfigured';

export function authState(env: Record<string, string | undefined>): AuthState {
  const id = env.APP_LOGIN_ID?.trim(), passwordHash = env.APP_LOGIN_PASSWORD_HASH?.trim(), secret = env.APP_SESSION_SECRET?.trim();
  if (id && passwordHash?.startsWith('scrypt$') && secret && secret.length >= 32) return { id, passwordHash, secret };
  if (!id && !passwordHash && !env.K_SERVICE) return 'disabled';
  return 'misconfigured';
}

/** "scrypt$<N>$<r>$<p>$<salt b64>$<hash b64>" */
export function hashPassword(password: string, salt = randomBytes(16)): string {
  const hash = scryptSync(password, salt, 64, SCRYPT);
  return ['scrypt', SCRYPT.N, SCRYPT.r, SCRYPT.p, salt.toString('base64'), hash.toString('base64')].join('$');
}

export function verifyPassword(password: string, stored: string): boolean {
  const [scheme, n, r, p, salt, hash] = stored.split('$');
  if (scheme !== 'scrypt' || !salt || !hash) return false;
  const expected = Buffer.from(hash, 'base64');
  const actual = scryptSync(password, Buffer.from(salt, 'base64'), expected.length, { N: Number(n), r: Number(r), p: Number(p) });
  return timingSafeEqual(actual, expected);
}

function sameText(a: string, b: string): boolean {
  const [x, y] = [Buffer.from(a), Buffer.from(b)];
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Both checks always run, so a wrong ID and a wrong password take the same time. */
export function checkCredentials(cfg: AuthConfig, id: string, password: string): boolean {
  const idOk = sameText(id.trim(), cfg.id);
  const passwordOk = verifyPassword(password, cfg.passwordHash);
  return idOk && passwordOk;
}

function sign(cfg: AuthConfig, expires: number): string {
  return createHmac('sha256', cfg.secret).update(`${expires}|${cfg.id}|${cfg.passwordHash}`).digest('base64url');
}

export function createSession(cfg: AuthConfig, now = Date.now()): { value: string; maxAge: number } {
  const maxAge = SESSION_DAYS * 24 * 3600;
  const expires = Math.floor(now / 1000) + maxAge;
  return { value: `${expires}.${sign(cfg, expires)}`, maxAge };
}

export function verifySession(cfg: AuthConfig, value: string | undefined, now = Date.now()): boolean {
  const [expires, mac] = (value ?? '').split('.');
  if (!expires || !mac || !/^\d+$/.test(expires) || Number(expires) * 1000 < now) return false;
  return sameText(mac, sign(cfg, Number(expires)));
}

/** Only same-site paths may be a post-login destination (no "//host" or "/\\host"). */
export function safeNext(next: string | null): string {
  return next && /^\/(?![/\\])/.test(next) ? next : '/build';
}

// Login attempts per client: 10 per 15 minutes, per instance.
const WINDOW_MS = 15 * 60_000, MAX_ATTEMPTS = 10;
const attempts = new Map<string, { count: number; reset: number }>();

export function allowAttempt(client: string, now = Date.now()): boolean {
  const entry = attempts.get(client);
  if (!entry || entry.reset < now) {
    if (attempts.size > 10_000) attempts.clear();
    attempts.set(client, { count: 1, reset: now + WINDOW_MS });
    return true;
  }
  entry.count += 1;
  return entry.count <= MAX_ATTEMPTS;
}

export function clearAttempts(client: string): void {
  attempts.delete(client);
}
