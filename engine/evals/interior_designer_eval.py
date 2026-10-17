"""Evaluate the interior designer agent (Neuro-SAN) on a compiled ConstructOS floor.

Every check compares what the agent said and proposed with the design stored for the project:
the brief it chose, the pieces the editor's solver placed (and could not), the clearance check
and the budget. Each run uses a fresh project.

    uv run python evals/interior_designer_eval.py [repeats]
"""

import colorsys
import json
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CONSTRUCTOS_DATA_DIR", tempfile.mkdtemp(prefix="interior-eval-"))
os.environ["CONSTRUCTOS_AGENT_WARMUP"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from constructos_engine import store  # noqa: E402
from constructos_engine.agents import runtime  # noqa: E402

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
FLOOR = json.loads((ROOT / "tests/fixtures/interior_single_floor.json").read_text())["floors"][0]
ROOM = {r["name"]: r["id"] for r in FLOOR["rooms"]}
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def fresh(room_ids=None) -> dict:
    pid = store.create("interior eval")["id"]
    return {"project_id": pid, "interior": {"floor": FLOOR, "room_ids": room_ids, "furnish": True, "home_has_pooja": False,
                                            "editor_project_id": "cos-eval"}}


def design_of(sly) -> dict | None:
    listed = store.list_designs(sly["project_id"])
    return store.get_design(sly["project_id"], listed[-1]["id"]) if listed else None


def room(d, name):
    return next((r for r in d["plan"]["rooms"] if r["name"] == name), None) if d else None


def saturation(hex_color: str | None) -> float:
    if not hex_color:
        return 0.0
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[2]


def hue(hex_color: str | None) -> float:
    if not hex_color:
        return -1
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[0] * 360


def common(r, d) -> dict:
    a = r["answer"] or ""
    total = d["budget"]["total"] / 1e5 if d else None
    quoted = bool(d) and any(f"{v:.{n}f}" in a for v in (total,) for n in (1, 2))
    return {"proposed a design": bool(d) and r["sly_data"].get("design_id") == d["id"],
            "everything placed fits (no clearance problems)": bool(d) and d["plan"]["issues"] == [],
            "quotes the stored budget": quoted,
            "no hex codes in the reply": not HEX.search(a)}


def sc_warm_indian_no_tv(_):
    sly = fresh()
    r = runtime.chat("interior_designer", "Warm Indian look for the whole home, lots of wood and plants. No TV in the living room please.", sly)
    d = design_of(sly)
    living = room(d, "Living room")
    pieces = [p for x in (d["plan"]["rooms"] if d else []) for p in x["pieces"]]
    return r, {**common(r, d),
               "Indian base theme": bool(d) and d["brief"]["base_theme"] in ("indian-contemporary", "warm-traditional"),
               "no TV in the living room": bool(living) and not any(p in ("TV Unit with Doors", "TV Stand", "Television") for p in living["pieces"]),
               "plants placed": any("Plant" in p for p in pieces)}


def sc_japandi_study(_):
    sly = fresh([ROOM["Main bedroom"]])
    r = runtime.chat("interior_designer", "A calm Japandi bedroom for us, with a small study corner where I can work.", sly)
    d = design_of(sly)
    master = room(d, "Main bedroom")
    return r, {**common(r, d),
               "Japandi base theme": bool(d) and d["brief"]["base_theme"] == "japandi",
               "only the selected room": bool(d) and [x["name"] for x in d["plan"]["rooms"]] == ["Main bedroom"],
               "desk and chair placed": bool(master) and {"Desk", "Office Chair"} <= set(master["pieces"])}


def sc_kids_bright(_):
    sly = fresh([ROOM["Bedroom"]])
    r = runtime.chat("interior_designer", "This bedroom is for our two kids. Make it bright and playful, with a desk for homework.", sly)
    d = design_of(sly)
    kid = room(d, "Bedroom")
    return r, {**common(r, d),
               "a bright colour on the walls": bool(kid) and max(saturation(kid["wall_color"]), saturation(kid.get("accent"))) >= 0.35,
               "desk placed": bool(kid) and "Desk" in kid["pieces"]}


def sc_budget(_):
    sly = fresh()
    r = runtime.chat("interior_designer", "Keep it budget friendly: a simple, clean modern look for the whole home.", sly)
    d = design_of(sly)
    return r, {**common(r, d),
               "basic budget tier": bool(d) and d["budget"]["tier"] == "basic",
               "modern or Scandinavian base": bool(d) and d["brief"]["base_theme"] in ("modern-minimal", "scandinavian", "japandi")}


def sc_grand_traditional(_):
    sly = fresh()
    r = runtime.chat("interior_designer", "Grand traditional style with a chandelier in the living room and a pooja unit. Premium finish.", sly)
    d = design_of(sly)
    pieces = [p for x in (d["plan"]["rooms"] if d else []) for p in x["pieces"]]
    living_light = (d["brief"].get("lights") or {}).get("living") if d else None
    return r, {**common(r, d),
               "traditional base theme": bool(d) and d["brief"]["base_theme"] == "warm-traditional",
               "chandelier in the living room": bool(d) and (living_light == "chandelier" or (living_light is None and d["brief"]["base_theme"] == "warm-traditional")),
               "pooja unit placed": "Pooja Unit" in pieces,
               "premium budget tier": bool(d) and d["budget"]["tier"] == "premium"}


def sc_follow_up(_):
    sly = fresh([ROOM["Living room"]])
    first = runtime.chat("interior_designer", "Scandinavian living room please.", sly)
    r = runtime.chat("interior_designer", "Lovely. Make the accent wall sage green instead.", sly, first["chat_context"])
    r["seconds"] += first["seconds"]
    d = design_of(sly)
    living = room(d, "Living room")
    return r, {**common(r, d),
               "two designs stored": len(store.list_designs(sly["project_id"])) == 2,
               "still Scandinavian": bool(d) and d["brief"]["base_theme"] == "scandinavian",
               "green accent": bool(living) and 60 <= hue(living.get("accent")) <= 170}


SCENARIOS = [sc_warm_indian_no_tv, sc_japandi_study, sc_kids_bright, sc_budget, sc_grand_traditional, sc_follow_up]


def main():
    rows, all_s, fit = [], [], []
    for sc in SCENARIOS:
        passed = total = 0
        secs = []
        for _ in range(REPEATS):
            r, checks = sc(None)
            secs.append(r["seconds"]); total += len(checks); passed += sum(map(bool, checks.values()))
            fit.append(checks["everything placed fits (no clearance problems)"])
            if not all(checks.values()):
                print(f"  ✗ {sc.__name__}: {[k for k, v in checks.items() if not v]} · tools {r['tools_called']} · {(r['answer'] or '')[:220]!r}")
        all_s += secs
        rows.append((sc.__name__[3:], passed, total, statistics.median(secs), max(secs)))
    print(f"\nInterior designer on gemini-3.8-flash · {REPEATS} runs per scenario\n")
    print(f"{'scenario':22} {'checks':>9} {'median s':>9} {'max s':>7}")
    for n, p, t, m, x in rows:
        print(f"{n:22} {p:>4}/{t:<4} {m:9.1f} {x:7.1f}")
    p, t = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print(f"{'TOTAL':22} {p:>4}/{t:<4} {statistics.median(all_s):9.1f} {max(all_s):7.1f}   ({100 * p / t:.0f}% of checks)")
    print(f"Proposals that fit (validator pass): {sum(fit)}/{len(fit)}")
    Path(__file__).with_suffix(".results.json").write_text(json.dumps(
        {"repeats": REPEATS, "ai_usage": __import__("constructos_engine.usage", fromlist=["summary"]).summary(), "fit": f"{sum(fit)}/{len(fit)}",
         "scenarios": [dict(zip(("name", "passed", "total", "median_s", "max_s"), r)) for r in rows]}, indent=1))


if __name__ == "__main__":
    main()
