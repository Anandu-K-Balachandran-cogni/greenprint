"""Interior designer: the bundled editor solver, the budget, the coded tools and the API (agent stubbed).

The live agent's behaviour is measured by evals/interior_designer_eval.py.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from constructos_engine import ledger, services, store
from constructos_engine.agents import interior_tools, runtime
from constructos_engine.interior import costing, solver
from constructos_engine.main import app

FLOOR = json.loads((Path(__file__).parent / "fixtures/interior_single_floor.json").read_text())["floors"][0]


def run(tool, args, sly):
    return json.loads(asyncio.run(tool().async_invoke(args, sly)))


def living_id() -> str:
    return next(r["room_id"] for r in solver.facts(FLOOR) if r["kind"] == "living")


def test_facts_describe_each_room_with_its_own_openings():
    rooms = {r["name"]: r for r in solver.facts(FLOOR)}
    assert rooms["Living room"]["kind"] == "living" and rooms["Living room"]["windows"] == [{"width_cm": 180, "sill_cm": 75}]
    assert rooms["Parking (1 car)"]["kind"].startswith("not styled")
    assert all(r["free_wall_cm"] > 0 for r in rooms.values())


def test_plan_places_requests_and_reports_what_did_not_fit():
    lid = living_id()
    out = solver.plan(FLOOR, {"base_theme": "japandi", "colors": {"accent": "#4a6b8a"},
                              "rooms": [{"room_id": lid, "add": ["bookshelf", "ottoman"], "omit": ["tv_unit", "television"]}]})
    assert out["ok"] and out["issues"] == []
    living = next(r for r in out["rooms"] if r["room_id"] == lid)
    assert "Bookshelf" in living["pieces"] and "Ottoman" in living["pieces"]
    assert not any("TV" in p or p == "Television" for p in living["pieces"])
    assert living["accent"] == "#4a6b8a" and living["paint_m2"] > 20
    crowded = solver.plan(FLOOR, {"base_theme": "boho", "rooms": [{"room_id": lid, "add": ["wardrobe_wide"] * 6}]})
    assert crowded["ok"] and any(s["item"] == "wardrobe_wide" for s in crowded["skipped"])


def test_plan_rejects_unknown_ids_and_bad_colours():
    out = solver.plan(FLOOR, {"base_theme": "baroque", "colors": {"walls": "beige"}, "floors": {"living": "lava"}, "curtain_style": "velvet",
                              "rooms": [{"room_id": "nowhere", "add": ["spaceship"]}]})
    assert not out["ok"]
    text = " ".join(out["problems"])
    for word in ("baroque", "walls", "lava", "nowhere", "spaceship", "velvet"):
        assert word in text


def test_budget_counts_added_pieces_by_tier_and_leaves_out_construction_items():
    added = [{"id": "sofa", "name": "Sofa", "count": 1}, {"id": "toilet", "name": "Toilet", "count": 2}, {"id": "curtain", "name": "Curtain", "count": 3}]
    std, prem = costing.budget(added, [{"paint_m2": 40}]), costing.budget(added, [{"paint_m2": 40}], "premium")
    assert std["subtotal"] == 38000 + 3 * 6500 and std["total"] == std["subtotal"] + std["contingency"]
    assert prem["total"] > 1.8 * std["total"] and not any(line["item"] == "Toilet" for line in std["lines"])
    assert any("construction estimate" in n for n in std["notes"])


def test_propose_design_tool_stores_the_design_and_ledger_step():
    pid = store.create("Interior tools")["id"]
    sly = {"project_id": pid, "interior": {"floor": FLOOR, "room_ids": None, "furnish": True, "editor_project_id": "cos-1"}}
    facts = run(interior_tools.RoomFacts, {"focus": "all"}, sly)
    assert facts["scope"] == "the whole floor" and len(facts["rooms_in_scope"]) == 10
    cat = run(interior_tools.CatalogueSearch, {"query": "sofa"}, sly)
    assert {p["id"] for p in cat["pieces"]} >= {"sofa", "sofa_long", "sofa_corner"} and len(cat["themes"]) == 8
    out = run(interior_tools.ProposeDesign, {"base_theme": "coastal", "title": "Sea breeze", "plants": True, "budget_tier": "basic",
                                             "accent_color": "#2A9D8F", "light_living": "chandelier", "sofa": "sofa_corner",
                                             "curtain_style": "Sheer", "rug_shape": "round"}, sly)
    assert out["design_id"] == sly["design_id"] and out["clearance_problems"] == [] and out["pieces_added"] > 30
    assert 0 < out["budget_lakh_inr"] < 20 and out["budget_tier"] == "basic"
    saved = store.get_design(pid, out["design_id"])
    assert saved["brief"]["title"] == "Sea breeze" and saved["editor_project_id"] == "cos-1"
    assert saved["brief"]["colors"] == {"accent": "#2a9d8f"} and saved["brief"]["lights"] == {"living": "chandelier"}
    assert saved["brief"]["pieces"] == {"sofa": "sofa_corner"}
    assert (saved["brief"]["curtain_style"], saved["brief"]["rug_shape"]) == ("sheer_curtain", "round_rug")
    assert [s["action"] for s in ledger.steps(pid) if s["stage"] == "interiors"] == ["proposed"]
    bad = run(interior_tools.ProposeDesign, {"base_theme": "coastal", "title": "x", "floor_living": "lava"}, sly)
    assert "lava" in bad["error"] and "call propose_design again" in bad["error"]
    assert "error" in run(interior_tools.RoomFacts, {"focus": "x"}, {"project_id": pid})


def test_chat_endpoint_sends_the_floor_and_returns_the_design(monkeypatch):
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Interior API"}).json()["id"]
    seen = {}

    def fake_chat(network, message, sly, ctx):
        seen.update(network=network, sly=sly)
        d = services.propose_design(pid, sly["interior"]["floor"], {"base_theme": "scandinavian", "title": "Nordic"},
                                    editor_project_id=sly["interior"]["editor_project_id"])
        return {"answer": "Here is a Nordic look.", "chat_context": None, "sly_data": {"design_id": d["id"]},
                "tools_called": ["propose_design"], "trace": [], "seconds": 1.0}
    monkeypatch.setattr(runtime, "chat", fake_chat)
    r = client.post(f"/projects/{pid}/agents/interior/chat",
                    json={"message": "Nordic please", "interior": {"floor": FLOOR, "room_ids": [living_id()], "editor_project_id": "cos-9"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen["network"] == "interior_designer" and seen["sly"]["interior"]["room_ids"] == [living_id()]
    assert body["design"]["brief"]["title"] == "Nordic" and body["design"]["budget"]["total"] > 0
    listed = client.get(f"/projects/{pid}/designs?editor=cos-9").json()
    assert [d["id"] for d in listed] == [body["design"]["id"]]
    assert client.get(f"/projects/{pid}/designs/{body['design']['id']}").json()["brief"]["base_theme"] == "scandinavian"
    assert client.get(f"/projects/{pid}/designs/missing").status_code == 404


def test_interior_network_validates():
    hocon = Path(runtime.REGISTRIES / "interior_designer.hocon")
    r = subprocess.run([sys.executable, "-m", "neuro_san.client.hocon_validator_cli", str(hocon)], capture_output=True, text=True,
                       env={**os.environ, "GEMINI_API_KEY": "test"})
    assert "Validation passed" in r.stdout + r.stderr, r.stdout + r.stderr
