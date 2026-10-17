"""Evaluate the sustainability advisor (Neuro-SAN + Gemini with Google Search grounding) on a compiled floor plan.

Each check compares the reply with the green report stored for the project (the tool's ground truth) and the
grounded climate values with plausible ranges for the city.

    uv run python evals/green_advisor_eval.py [repeats]
"""

import json
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CONSTRUCTOS_DATA_DIR", tempfile.mkdtemp(prefix="green-eval-"))
os.environ["CONSTRUCTOS_AGENT_WARMUP"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from constructos_engine import store  # noqa: E402
from constructos_engine.agents import runtime  # noqa: E402

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
FLOORS = json.loads((ROOT / "tests/fixtures/interior_two_storey.json").read_text())["floors"]
RAIN = {"Chennai": (1000, 1800), "Singapore": (1900, 2900), "Pune": (500, 1000), "Bengaluru": (750, 1100)}


def fresh() -> dict:
    pid = store.create("green eval")["id"]
    return {"project_id": pid, "editor_floors": FLOORS, "editor_project_id": "cos-eval"}


def latest(sly):
    listed = store.list_green(sly["project_id"])
    return store.get_green(sly["project_id"], listed[-1]["id"]) if listed else None


digits = lambda s: re.sub(r"[,\s]", "", s)  # noqa: E731


def has_number(answer: str, value) -> bool:
    a = digits(answer)
    return any(v in a for v in {str(value), f"{value:g}" if isinstance(value, float) else str(value), str(round(value)) if isinstance(value, float) else str(value)})


def common(r, g, city) -> dict:
    lo, hi = RAIN[city]
    return {"checked the plan": bool(g), "for the right city": bool(g) and g["climate"]["query"].lower().startswith(city.lower()),
            "grounded rainfall is plausible": bool(g) and lo <= g["climate"]["rainfall_mm"] <= hi,
            "has sources": bool(g) and len(g["climate"]["sources"]) > 0}


def sc_how_green(_):
    sly = fresh()
    r = runtime.chat("sustainability_advisor", "How green is my home? We're building in Chennai.", sly)
    g = latest(sly)
    return r, {**common(r, g, "Chennai"), "quotes the stored score": bool(g) and has_number(r["answer"] or "", g["report"]["score"]["total"])}


def sc_rainwater(_):
    sly = fresh()
    r = runtime.chat("sustainability_advisor", "We are building in Singapore. How much rainwater can we collect in a year?", sly)
    g = latest(sly)
    return r, {**common(r, g, "Singapore"),
               "quotes the stored harvest": bool(g) and has_number(r["answer"] or "", g["report"]["water"]["harvest_litres_year"])}


def sc_no_city(_):
    sly = fresh()
    r = runtime.chat("sustainability_advisor", "Is my house green enough?", sly)
    a = (r["answer"] or "").lower()
    return r, {"did not run a check": latest(sly) is None, "asks for the city": "city" in a or "where" in a}


def sc_pinned_plot(_):
    sly = fresh()
    store.set_site(sly["project_id"], {"lat": 13.0857, "lng": 80.2101, "address": "Anna Nagar, Chennai", "city": "Chennai", "country": "India"})
    r = runtime.chat("constructos", "What should we do first to make our home cooler and greener?", sly)
    g = latest(sly)
    a = (r["answer"] or "").lower()
    return r, {**common(r, g, "Chennai"), "did not ask for the city": "which city" not in a and "what city" not in a,
               "routed to the advisor": "/sustainability_advisor" in r["tools_called"]}


def sc_dark_rooms(_):
    sly = fresh()
    r = runtime.chat("sustainability_advisor", "Which rooms will be too dark in Pune, and how do we fix them?", sly)
    g = latest(sly)
    dark = [x["name"] for x in g["report"]["rooms"] if not x["daylight_ok"]] if g else []
    a = (r["answer"] or "").lower()
    return r, {**common(r, g, "Pune"),
               "names a dark room (or says none)": (not dark and ("all" in a or "none" in a or "every" in a)) or any(n.lower() in a for n in dark)}


def sc_materials_follow_up(_):
    sly = fresh()
    first = runtime.chat("sustainability_advisor", "Check how green the house is for Chennai.", sly)
    r = runtime.chat("sustainability_advisor", "Which material change saves the most carbon?", sly, first["chat_context"])
    r["seconds"] += first["seconds"]
    g = latest(sly)
    best = max(g["report"]["carbon"]["swaps"], key=lambda s: s["saving_t"]) if g else None
    return r, {**common(r, g, "Chennai"), "one check reused": len(store.list_green(sly["project_id"])) == 1,
               "names the biggest saving": bool(best) and ("aac" in (r["answer"] or "").lower())}


def sc_front_agent_solar(_):
    sly = fresh()
    r = runtime.chat("constructos", "We are in Bengaluru. How big a rooftop solar system should we install?", sly)
    g = latest(sly)
    return r, {**common(r, g, "Bengaluru"), "routed to the advisor": "/sustainability_advisor" in r["tools_called"],
               "quotes the stored kWp": bool(g) and has_number(r["answer"] or "", g["report"]["solar"]["kwp"])}


SCENARIOS = [sc_how_green, sc_rainwater, sc_no_city, sc_pinned_plot, sc_dark_rooms, sc_materials_follow_up, sc_front_agent_solar]


def main():
    rows, all_s = [], []
    for sc in SCENARIOS:
        passed = total = 0
        secs = []
        for _ in range(REPEATS):
            r, checks = sc(None)
            secs.append(r["seconds"]); total += len(checks); passed += sum(map(bool, checks.values()))
            if not all(checks.values()):
                print(f"  ✗ {sc.__name__}: {[k for k, v in checks.items() if not v]} · tools {r['tools_called']} · {(r['answer'] or '')[:240]!r}")
        all_s += secs
        rows.append((sc.__name__[3:], passed, total, statistics.median(secs), max(secs)))
    print(f"\nSustainability advisor on gemini-3.8-flash · {REPEATS} runs per scenario\n")
    print(f"{'scenario':22} {'checks':>9} {'median s':>9} {'max s':>7}")
    for n, p, t, m, x in rows:
        print(f"{n:22} {p:>4}/{t:<4} {m:9.1f} {x:7.1f}")
    p, t = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print(f"{'TOTAL':22} {p:>4}/{t:<4} {statistics.median(all_s):9.1f} {max(all_s):7.1f}   ({100 * p / t:.0f}% of checks)")
    Path(__file__).with_suffix(".results.json").write_text(json.dumps(
        {"repeats": REPEATS, "ai_usage": __import__("constructos_engine.usage", fromlist=["summary"]).summary(), "scenarios": [dict(zip(("name", "passed", "total", "median_s", "max_s"), r)) for r in rows]}, indent=1))


if __name__ == "__main__":
    main()
