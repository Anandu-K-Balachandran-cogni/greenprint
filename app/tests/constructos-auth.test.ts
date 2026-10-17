import { describe, expect, it } from 'vitest';
import { allowAttempt, authState, checkCredentials, createSession, hashPassword, safeNext, verifySession, type AuthConfig } from '../src/lib/server/auth';

const cfg: AuthConfig = { id: 'constructos', passwordHash: hashPassword('correct horse'), secret: 'x'.repeat(48) };

describe('shared login', () => {
  it('accepts only the configured ID and password', () => {
    expect(checkCredentials(cfg, 'constructos', 'correct horse')).toBe(true);
    expect(checkCredentials(cfg, ' constructos ', 'correct horse')).toBe(true);
    expect(checkCredentials(cfg, 'constructos', 'Correct horse')).toBe(false);
    expect(checkCredentials(cfg, 'admin', 'correct horse')).toBe(false);
    expect(checkCredentials(cfg, '', '')).toBe(false);
  });

  it('stores a salted scrypt hash, never the password', () => {
    const a = hashPassword('pw'), b = hashPassword('pw');
    expect(a).toMatch(/^scrypt\$16384\$8\$1\$/);
    expect(a).not.toContain('pw$');
    expect(a).not.toBe(b);
  });

  it('signs sessions that expire, resist tampering and end when the password changes', () => {
    const now = Date.UTC(2026, 9, 12);
    const { value, maxAge } = createSession(cfg, now);
    expect(maxAge).toBe(7 * 24 * 3600);
    expect(verifySession(cfg, value, now)).toBe(true);
    expect(verifySession(cfg, value, now + 8 * 24 * 3600 * 1000)).toBe(false);
    const [exp, mac] = value.split('.');
    expect(verifySession(cfg, `${Number(exp) + 3600}.${mac}`, now)).toBe(false);
    expect(verifySession(cfg, `${exp}.${mac.slice(0, -2)}AA`, now)).toBe(false);
    expect(verifySession({ ...cfg, passwordHash: hashPassword('new') }, value, now)).toBe(false);
    expect(verifySession({ ...cfg, secret: 'y'.repeat(48) }, value, now)).toBe(false);
    expect(verifySession(cfg, undefined, now)).toBe(false);
    expect(verifySession(cfg, 'garbage', now)).toBe(false);
  });

  it('is off locally, and fails closed on Cloud Run without configuration', () => {
    expect(authState({})).toBe('disabled');
    expect(authState({ K_SERVICE: 'constructos-app' })).toBe('misconfigured');
    expect(authState({ APP_LOGIN_ID: 'a' })).toBe('misconfigured');
    expect(authState({ APP_LOGIN_ID: 'a', APP_LOGIN_PASSWORD_HASH: cfg.passwordHash, APP_SESSION_SECRET: 'short' })).toBe('misconfigured');
    expect(authState({ APP_LOGIN_ID: 'a', APP_LOGIN_PASSWORD_HASH: cfg.passwordHash, APP_SESSION_SECRET: cfg.secret })).toMatchObject({ id: 'a' });
  });

  it('redirects only to same-site paths after login', () => {
    expect(safeNext('/services/cos-1?x=1')).toBe('/services/cos-1?x=1');
    for (const bad of ['//evil.example', '/\\evil.example', 'https://evil.example', 'javascript:alert(1)', '', null]) {
      expect(safeNext(bad)).toBe('/build');
    }
  });

  it('limits attempts per client', () => {
    const now = 1_000_000;
    const results = Array.from({ length: 12 }, () => allowAttempt('203.0.113.9', now));
    expect(results.filter(Boolean)).toHaveLength(10);
    expect(allowAttempt('203.0.113.9', now + 16 * 60_000)).toBe(true);
    expect(allowAttempt('198.51.100.1', now)).toBe(true);
  });
});
