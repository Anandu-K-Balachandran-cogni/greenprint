"""Evaluate the ConstructOS agent team (front agent + architect + electrical + plumbing engineers).

Every check compares what the agents did and said with ground truth in the project store.
One layout is solved once; each run copies it into a fresh project so runs are independent.

    uv run python evals/agent_team_eval.py [repeats]
"""

import copy
import json
import os
import re
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("CONSTRUCTOS_DATA_DIR", tempfile.mkdtemp(prefix="team-eval-"))
os.environ["CONSTRUCTOS_AGENT_WARMUP"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from conftest import wait_run  # noqa: E402
from constructos_engine import ledger, store  # noqa: E402
from constructos_engine.agents import runtime  # noqa: E402
from constructos_engine.main import app  # noqa: E402
from constructos_engine.review import build_facts  # noqa: E402
from constructos_engine import services  # noqa: E402

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
SOURCE = json.loads((ROOT / "tests/fixtures/sharma_routing_source.json").read_text())
BRIEF = json.loads((ROOT / "tests/fixtures/sharma_brief.json").read_text())
client = TestClient(app)


def solved_template() -> tuple[str, dict]:
    pid = client.post("/projects", json={"name": "template"}).json()["id"]
    client.post(f"/projects/{pid}/brief/confirm", json={"brief": BRIEF, "expected_version": 0})
    run = client.post(f"/projects/{pid}/layouts", json={"expected_brief_version": 1, "alternatives": 1, "time_limit_s": 20}).json()
    return pid, wait_run(client, pid, run["id"])


TEMPLATE_PID, TEMPLATE_RUN = solved_template()


def fresh() -> dict:
    pid = client.post("/projects", json={"name": "eval"}).json()["id"]
    client.post(f"/projects/{pid}/brief/confirm", json={"brief": BRIEF, "expected_version": 0})
    run = copy.deepcopy(TEMPLATE_RUN); run["id"] = str(uuid.uuid4()); run["job"] = None
    store.save_layout_run(pid, run)
    return {"project_id": pid, "routing_source": SOURCE, "layout": {"run_id": run["id"], "candidate": 0}}


def latest(pid, kind):
    listed = [d for d in store.list_diagrams(pid) if d["kind"] == kind]
    return store.get_diagram(pid, listed[-1]["id"]) if listed else None


def sc_status(sly):
    r = runtime.chat("constructos", "Where are we with my house, and what should I do next?", sly)
    a = (r["answer"] or "").lower()
    return r, {"used project_status": "project_status" in r["tools_called"],
               "states the real next step (open in editor / services)": bool(re.search(r"editor|electrical|plumbing", a))}


def sc_two_experts(sly):
    r = runtime.chat("constructos", "Please add an AC point in both kids bedrooms, and plumb the house.", sly)
    pid = sly["project_id"]
    e, p = latest(pid, "electrical"), latest(pid, "plumbing")
    names = {(x["floor"], x["id"]): x["name"] for x in (e or {}).get("rooms", [])}
    ac_rooms = sorted({names[(x["floor"], x["room_id"])] for x in (e or {}).get("endpoints", []) if x["kind"] == "ac_point"})
    wires = [s for s in ledger.steps(pid) if s["action"] == "electrical"]
    return r, {"asked both engineers": {"/electrical_engineer", "/plumbing_engineer"} <= set(r["tools_called"]),
               "AC points really in both kids bedrooms": ac_rooms == ["Kids bedroom 1", "Kids bedroom 2"],
               "plumbing complete": bool(p) and p["status"] == "complete",
               "wired once (no wasted regeneration)": len(wires) == 1,
               "both diagram ids returned": {"plumbing_diagram_id", "electrical_diagram_id"} <= set(r["sly_data"])}


def sc_change(sly):
    r = runtime.chat("constructos", "Make Kids bedroom 1 at least 14 square metres.", sly)
    rid = (r["sly_data"] or {}).get("revision_run_id")
    ok_run, area = False, None
    if rid:
        run = wait_run(client, sly["project_id"], rid, timeout_s=180)
        ok_run = run["status"] == "complete" and run["candidates"][0]["report"]["status"] == "PASS"
        area = next((s["area_m2"] for s in run["candidates"][0]["allocation"]["spaces"] if s["name"] == "Kids bedroom 1"), None)
        descr = " ".join(run.get("change_descriptions") or [])
    return r, {"asked the architect": "/architect" in r["tools_called"],
               "revision queued": bool(rid),
               "change is exactly the request": bool(rid) and "Kids bedroom 1: at least 14" in descr,
               "revised option solved and checked": ok_run,
               "Kids bedroom 1 really >= 14 m²": area is not None and area >= 14 - 0.05}


def sc_architect_facts(sly):
    c = services.option_context(sly["project_id"], sly["layout"]["run_id"], 0)
    facts = {f.id: f.text for f in build_facts(c.brief, c.program, c.allocation, c.report)}
    kitchen = next(s for s in c.allocation.spaces if s.type == "kitchen")
    dining = next(s for s in c.allocation.spaces if s.type == "dining")
    adjacent = any(k for k in facts if k.startswith("A:") and kitchen.id in k and dining.id in k)
    r = runtime.chat("architect", "Is the kitchen next to the dining area? How big is the kitchen?", sly)
    a = (r["answer"] or "").lower()
    yes = bool(re.search(r"\byes\b|right next|adjacent|share", a)) and not re.search(r"\bnot (next|adjacent)\b|\bno,", a)
    return r, {"used layout_facts": "layout_facts" in r["tools_called"],
               "adjacency answer matches the facts": yes == adjacent,
               "states the true kitchen area": f"{kitchen.area_m2:g}" in a or f"{kitchen.area_m2:.1f}" in a}


def sc_resume(sly):
    runtime.chat("constructos", "Please plumb the house.", sly)
    fresh_sly = {k: v for k, v in sly.items()}  # new conversation: no chat_context, no ids from earlier turns
    r = runtime.chat("constructos", "I'm back. What has been done so far on my house?", fresh_sly)
    a = (r["answer"] or "").lower()
    return r, {"used project_status": "project_status" in r["tools_called"],
               "remembers the plumbing from the store": "plumb" in a,
               "did not redo the work": not {"/plumbing_engineer"} & set(r["tools_called"])}


def sc_cost(sly):
    r = runtime.chat("constructos", "How much would it cost to build with premium finishes in a metro city?", sly)
    est = store.list_estimates(sly["project_id"])
    e = store.get_estimate(sly["project_id"], est[-1]["id"]) if est else None
    a = r["answer"] or ""
    total_lakh = f"{e['total'] / 1e5:.2f}" if e else None
    return r, {"asked the cost estimator": "/cost_estimator" in r["tools_called"],
               "estimate is premium + metro": bool(e) and (e["tier"], e["location"]) == ("premium", "metro"),
               "quotes the stored total": bool(e) and (total_lakh in a or total_lakh.rstrip("0").rstrip(".") in a),
               "quotes the stored per sq ft": bool(e) and (f"{e['per_sqft']:,}" in a or str(e["per_sqft"]) in a),
               "within the published band": bool(e) and e["benchmark"]["verdict"] == "within"}


SCENARIOS = [sc_status, sc_two_experts, sc_change, sc_architect_facts, sc_resume, sc_cost]


def main():
    rows, all_s = [], []
    for sc in SCENARIOS:
        passed = total = 0
        secs = []
        for _ in range(REPEATS):
            r, checks = sc(fresh())
            secs.append(r["seconds"]); total += len(checks); passed += sum(map(bool, checks.values()))
            if not all(checks.values()):
                print(f"  ✗ {sc.__name__}: {[k for k, v in checks.items() if not v]} · tools {r['tools_called']} · {(r['answer'] or '')[:200]!r}")
        all_s += secs
        rows.append((sc.__name__[3:], passed, total, statistics.median(secs), max(secs)))
    print(f"\nConstructOS agent team on gemini-3.8-flash · {REPEATS} runs per scenario\n")
    print(f"{'scenario':18} {'checks':>9} {'median s':>9} {'max s':>7}")
    for n, p, t, m, x in rows:
        print(f"{n:18} {p:>4}/{t:<4} {m:9.1f} {x:7.1f}")
    p, t = sum(r[1] for r in rows), sum(r[2] for r in rows)
    print(f"{'TOTAL':18} {p:>4}/{t:<4} {statistics.median(all_s):9.1f} {max(all_s):7.1f}   ({100 * p / t:.0f}% of checks)")
    Path(__file__).with_suffix(".results.json").write_text(json.dumps(
        {"repeats": REPEATS, "ai_usage": __import__("constructos_engine.usage", fromlist=["summary"]).summary(), "scenarios": [dict(zip(("name", "passed", "total", "median_s", "max_s"), r)) for r in rows]}, indent=1))


if __name__ == "__main__":
    main()
