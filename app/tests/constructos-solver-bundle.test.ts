import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';

// The engine runs a bundled copy of the interior solver; it must match the editor's source.
it('the engine solver bundle is up to date with app/src/lib/constructos', () => {
  const script = fileURLToPath(new URL('../tooling/build-solver.mjs', import.meta.url));
  expect(execFileSync(process.execPath, [script, '--check'], { encoding: 'utf8' })).toContain('up to date');
}, 60_000);
