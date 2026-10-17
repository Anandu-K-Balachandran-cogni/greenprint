"""Green advisor: service, API, tools and the climate cache (search stubbed). Live behaviour: evals/green_advisor_eval.py."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from constructos_engine import climate, ledger, services, store
from constructos_engine.agents import green_tools, runtime
from constructos_engine.main import app

FLOORS = json.loads((Path(__file__).parent / "fixtures/interior_two_storey.json").read_text())["floors"]
CHENNAI = {"city": "Chennai", "country": "India", "zone": "warm_humid", "rainfall_mm": 1400.0, "sun_hours": 5.3, "tariff": 6.0,
           "currency": "INR", "hemisphere": "north", "schemes": "PM Surya Ghar subsidy", "query": "Chennai",
           "sources": [{"title": "imd.gov.in", "url": "https://example.org/imd"}], "notes": "", "model": "stub", "looked_up_at": "2026-10-13T00:00:00+00:00"}


def run(tool, args, sly):
    return json.loads(asyncio.run(tool().async_invoke(args, sly)))


def test_green_check_combines_climate_and_the_model(monkeypatch):
    monkeypatch.setattr(climate, "lookup", lambda city: CHENNAI)
    pid = store.create("Green")["id"]
    g = services.green_check(pid, FLOORS, "Chennai", facing="south", editor_project_id="cos-g")
    r = g["report"]
    assert g["front"] == "south" and g["front_source"] == "given" and r["zone"] == "warm_humid"
    assert len(r["rooms"]) >= 5 and all(abs(x["required_wfr"] - 1 / 6) < 1e-3 for x in r["rooms"])
    assert r["water"]["harvest_litres_year"] > 0 and r["solar"]["kwp"] >= 1 and r["carbon"]["total_t"] > 10
    assert 0 < r["score"]["total"] <= r["score"]["after_fixes"] <= 100
    assert store.get_green(pid, g["id"])["climate"]["city"] == "Chennai"
    assert [s["action"] for s in ledger.steps(pid) if s["stage"] == "green"] == ["checked"]
    unknown = services.green_check(pid, FLOORS, "Chennai")   # no stated entrance in this project's brief
    assert unknown["front"] is None and all(not x["window_facings"] for x in unknown["report"]["rooms"])


def test_green_check_refuses_missing_inputs(monkeypatch):
    monkeypatch.setattr(climate, "lookup", lambda city: (_ for _ in ()).throw(RuntimeError("search down")))
    pid = store.create("Green")["id"]
    for floors, city, code in (([], "Chennai", "GREEN_NO_PLAN"), (FLOORS, " ", "GREEN_NO_CITY"), (FLOORS, "Chennai", "CLIMATE_FAILED")):
        try:
            services.green_check(pid, floors, city)
            raise AssertionError("expected an error")
        except services.ServiceError as e:
            assert e.code == code


def test_climate_lookup_is_cached_per_city(monkeypatch):
    calls = []
    real = climate.lookup.__wrapped__ if hasattr(climate.lookup, "__wrapped__") else None
    assert real is None  # plain function
    store.backend().put("climate", climate._key("  Pune "), {**CHENNAI, "city": "Pune", "looked_up_at": "2099-01-01T00:00:00+00:00"})
    monkeypatch.setattr(climate.gemini, "generate_content", lambda **k: calls.append(k))
    assert climate.lookup("pune")["city"] == "Pune" and calls == []


def test_tools_and_api(monkeypatch):
    monkeypatch.setattr(climate, "lookup", lambda city: CHENNAI)
    pid = store.create("Green tools")["id"]
    sly = {"project_id": pid, "editor_floors": FLOORS, "editor_project_id": "cos-t"}
    out = run(green_tools.GreenCheck, {"city": "Chennai", "entrance_facing": "South-West"}, sly)
    assert out["green_id"] == sly["green_id"] and out["orientation"].startswith("entrance faces south-west")
    assert out["sources"] == ["imd.gov.in"] and out["rooms"] and "material_swaps" in out
    again = run(green_tools.GreenFacts, {"topic": "solar"}, {"project_id": pid, "editor_project_id": "cos-t"})
    assert again["green_id"] == out["green_id"]
    assert "error" in run(green_tools.GreenCheck, {"city": "Chennai"}, {"project_id": pid})
    client = TestClient(app)
    r = client.post(f"/projects/{pid}/green", json={"floors": FLOORS, "city": "Chennai", "facing": "east", "editor_project_id": "cos-t"})
    assert r.status_code == 201, r.text
    assert client.get(f"/projects/{pid}/green?editor=cos-t").json()[-1]["id"] == r.json()["id"]
    assert client.post(f"/projects/{pid}/green", json={"floors": FLOORS, "city": "Chennai", "facing": "up"}).status_code == 422
    assert client.get(f"/projects/{pid}/green/{r.json()['id']}").json()["front"] == "east"


def test_chat_passes_editor_floors_to_the_agents(monkeypatch):
    seen = {}
    monkeypatch.setattr(runtime, "chat", lambda network, message, sly, ctx: seen.update(sly=sly) or
                        {"answer": "Score 61.", "chat_context": None, "sly_data": {"green_id": "g-1"}, "tools_called": [], "trace": [], "seconds": 1})
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Green chat"}).json()["id"]
    r = client.post(f"/projects/{pid}/agents/green/chat", json={"message": "How green is it in Chennai?", "floors": FLOORS})
    assert r.status_code == 200 and r.json()["green_id"] == "g-1" and seen["sly"]["editor_floors"] == FLOORS


def test_network_validates():
    hocon = Path(runtime.REGISTRIES / "sustainability_advisor.hocon")
    r = subprocess.run([sys.executable, "-m", "neuro_san.client.hocon_validator_cli", str(hocon)], capture_output=True, text=True,
                       env={**os.environ, "GEMINI_API_KEY": "test"})
    assert "Validation passed" in r.stdout + r.stderr


def test_pinned_site_supplies_city_and_hemisphere(monkeypatch):
    seen = {}
    monkeypatch.setattr(climate, "lookup", lambda city: seen.setdefault("city", city) and {**CHENNAI, "hemisphere": "north"})
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Pinned"}).json()["id"]
    r = client.put(f"/projects/{pid}/site", json={"lat": -33.86, "lng": 151.21, "address": "Sydney NSW", "city": "Sydney", "country": "Australia"})
    assert r.status_code == 200 and r.json()["pinned_at"]
    assert client.get(f"/projects/{pid}").json()["site"]["city"] == "Sydney"
    assert client.put(f"/projects/{pid}/site", json={"lat": 120, "lng": 0}).status_code == 422
    g = services.green_check(pid, FLOORS, None)
    assert seen["city"] == "Sydney, Australia" and g["site"]["lat"] == -33.86
    assert any(s["action"] == "pinned" for s in ledger.steps(pid))
