"""Run every eval and the layout-solver timings, then write the benchmark report (docs/benchmark.md + .json).

    uv run python evals/benchmark.py [repeats]

Each eval runs in its own process and data directory; its results (checks, latency, Gemini usage) are
read back from evals/<name>.results.json.
"""

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPEATS = sys.argv[1] if len(sys.argv) > 1 else "3"
EVALS = [
    ("agent_team_eval", "Agent team: front agent + architect + engineers + cost"),
    ("plumbing_engineer_eval", "Plumbing engineer agent"),
    ("interior_designer_eval", "Interior designer agent"),
    ("green_advisor_eval", "Sustainability advisor agent (Search-grounded climate)"),
    ("sketch_intake_eval", "Sketch, plot-drawing and multilingual brief intake"),
]


def solver_timings() -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from constructos_engine import brief as b, program, solver
    from constructos_engine.layout_checks import check_allocation
    sharma = b.Brief.model_validate(json.loads((ROOT / "tests/fixtures/sharma_brief.json").read_text()))
    out = []
    for name, brief in (("Sharma family home (G+1, 40 x 60 ft)", sharma),):
        p = program.build_program(brief)
        secs, statuses = [], []
        for seed in range(3):
            t = time.monotonic()
            a = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=20, seed=seed)
            secs.append(time.monotonic() - t)
            statuses.append(check_allocation(a, p).status)
        out.append({"case": name, "spaces": len(p.spaces), "median_s": round(statistics.median(secs), 1), "max_s": round(max(secs), 1),
                    "checks_pass": f"{statuses.count('PASS')}/{len(statuses)}"})
    return out


def main():
    results = {}
    for name, title in EVALS:
        t = time.monotonic()
        print(f"▶ {name} …", flush=True)
        done = subprocess.run([sys.executable, str(ROOT / "evals" / f"{name}.py"), REPEATS], cwd=ROOT, capture_output=True, text=True)
        tail = "\n".join(l for l in done.stdout.splitlines() if l.startswith(("TOTAL", "  ✗")))
        print(tail, flush=True)
        path = ROOT / "evals" / f"{name}.results.json"
        results[name] = {"title": title, "wall_s": round(time.monotonic() - t), "exit": done.returncode,
                         **(json.loads(path.read_text()) if path.exists() else {})}
    results["solver"] = solver_timings()
    (ROOT.parent / "docs" / "benchmark.json").write_text(json.dumps(results, indent=1))
    lines = ["# Greenprint benchmark", "", f"Live runs on Gemini (text, vision, image, Search grounding) · {REPEATS} runs per scenario · "
             f"{time.strftime('%Y-%m-%d')}", "", "## Agent and AI evals", "",
             "| Eval | Checks passed | Median s per turn | Max s | Gemini calls | Tokens (in / out) | Est. cost USD |", "|---|---|---|---|---|---|---|"]
    for name, title in EVALS:
        r = results[name]
        sc = r.get("scenarios") or r.get("cases") or []
        p, t = sum(s["passed"] for s in sc), sum(s["total"] for s in sc)
        med = statistics.median([s["median_s"] for s in sc]) if sc else 0
        mx = max([s.get("max_s", s["median_s"]) for s in sc]) if sc else 0
        u = r.get("ai_usage") or {}
        calls = sum(x["model_requests"] for x in u.get("by_purpose", []))
        lines.append(f"| {title} | {p}/{t} ({100 * p / max(t, 1):.0f}%) | {med:.1f} | {mx:.1f} | {calls} | "
                     f"{u.get('prompt_tokens', 0):,} / {u.get('output_tokens', 0):,} | {u.get('est_usd', 0):.2f} |")
    lines += ["", "## Layout solver (OR-Tools CP-SAT, time-boxed at 20 s: it keeps improving the layout until the limit)", "", "| Case | Spaces | Median s | Max s | Layout checks |", "|---|---|---|---|---|"]
    for s in results["solver"]:
        lines.append(f"| {s['case']} | {s['spaces']} | {s['median_s']} | {s['max_s']} | {s['checks_pass']} |")
    lines += ["", "Costs are estimates at the list prices recorded in docs/benchmark.json (prices_usd)."]
    (ROOT.parent / "docs" / "benchmark.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
