// Command-line entry for the engine (bundled to engine/constructos_engine/interior/solver.mjs).
// Reads one JSON request on stdin, writes one JSON reply on stdout:
//   { cmd: 'facts', floor, room_ids? }                        → room facts
//   { cmd: 'catalog' }                                         → indoor pieces, themes, floor materials
//   { cmd: 'plan', floor, brief, room_ids?, furnish?, home_has_pooja? } → problems or a checked plan
//   { cmd: 'green', floors, climate, front }                  → green performance report
import { floorMaterials } from '$lib/utils/materials';
import { THEMES } from './themes';
import { briefProblems, designCatalog, planDesign, roomFacts } from './design';
import { analyseGreen } from './green';

async function main() {
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;
  const req = JSON.parse(raw);
  let out: unknown;
  if (req.cmd === 'facts') out = { rooms: roomFacts(req.floor, req.room_ids) };
  else if (req.cmd === 'catalog') out = {
    pieces: designCatalog().map(f => ({ id: f.id, name: f.name, category: f.category, size_cm: [f.width, f.depth, f.height], ...(f.mount ? { mount_cm: f.mount } : {}) })),
    themes: THEMES.map(t => ({ id: t.id, name: t.name, blurb: t.blurb, walls: t.walls, accent: t.accent, fabric: t.palette.fabric, wood: t.palette.wood, metal: t.palette.metal })),
    floor_materials: floorMaterials.filter(m => m.id !== 'none').map(m => ({ id: m.id, name: m.name })),
  };
  else if (req.cmd === 'plan') {
    const problems = briefProblems(req.brief, req.floor);
    if (problems.length) out = { ok: false, problems };
    else {
      const r = planDesign(req.floor, req.brief, { roomIds: req.room_ids, furnish: req.furnish, homeHasPooja: req.home_has_pooja });
      out = { ok: true, rooms: r.rooms, skipped: r.skipped, issues: r.issues, added: r.added,
        restyled: r.changes.restyle.length, summary: r.changes.summary };
    }
  } else if (req.cmd === 'green') out = analyseGreen(req.floors, req.climate, req.front ?? null);
  else out = { error: `unknown cmd ${req.cmd}` };
  process.stdout.write(JSON.stringify(out));
}

main().catch(e => { process.stdout.write(JSON.stringify({ error: String(e?.message ?? e) })); process.exitCode = 1; });
