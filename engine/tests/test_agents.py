"""Neuro-SAN plumbing_engineer: coded tools (deterministic) and the chat endpoint (agent stubbed).

The live agent's behaviour is measured by evals/plumbing_engineer_eval.py.
"""

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from constructos_engine import store
from constructos_engine.agents import plumbing_tools, runtime
from constructos_engine.main import app

SOURCE = json.loads((Path(__file__).parent / "fixtures/sharma_routing_source.json").read_text())


def run(tool, args, sly):
    return json.loads(asyncio.run(tool().async_invoke(args, sly)))


def test_plumb_plan_stores_a_diagram_and_returns_measured_facts():
    pid = store.create("Agent tools")["id"]
    sly = {"project_id": pid, "routing_source": SOURCE}
    facts = run(plumbing_tools.PlumbPlan, {}, sly)
    assert facts["status"] == "complete" and facts["networks"]["drainage"]["connected"] == 12
    assert facts["manhole_room"] == ["Parking (2 cars)"] and facts["stacks"] == ["Stack S1"]
    assert sly["plumbing_diagram_id"] == facts["diagram_id"]
    assert store.get_diagram(pid, facts["diagram_id"])["status"] == "complete"
    assert "routing_source" not in json.dumps(facts) and "polygon" not in json.dumps(facts)  # no geometry for the LLM


def test_plumb_plan_honours_a_named_room_and_refuses_an_unknown_one():
    pid = store.create("Agent tools")["id"]
    sly = {"project_id": pid, "routing_source": SOURCE}
    assert run(plumbing_tools.PlumbPlan, {"manhole_room": "foyer"}, sly)["manhole_room"] == ["Foyer"]
    err = run(plumbing_tools.PlumbPlan, {"manhole_room": "Garden"}, {"project_id": pid, "routing_source": SOURCE})
    assert "no ground-floor room called 'Garden'" in err["error"] and "Foyer" in err["error"]


def test_plumbing_facts_reads_the_store_in_a_new_session():
    pid = store.create("Agent tools")["id"]
    assert "No plumbing has been generated" in run(plumbing_tools.PlumbingFacts, {}, {"project_id": pid, "routing_source": SOURCE})["error"]
    run(plumbing_tools.PlumbPlan, {}, {"project_id": pid, "routing_source": SOURCE})
    facts = run(plumbing_tools.PlumbingFacts, {"topic": "status"}, {"project_id": pid, "routing_source": SOURCE})  # no diagram_id
    assert facts["status"] == "complete" and facts["matches_saved_plan"] is True
    stale = {**SOURCE, "source_hash": "sha256-different"}
    assert run(plumbing_tools.PlumbingFacts, {}, {"project_id": pid, "routing_source": stale})["matches_saved_plan"] is False


def test_tools_refuse_without_a_project_or_plan():
    assert "No project" in run(plumbing_tools.PlumbPlan, {}, {})["error"]
    assert "saved plan was not provided" in run(plumbing_tools.PlumbPlan, {}, {"project_id": "x"})["error"]


def test_networks_validate_with_the_neuro_san_validator():
    import os
    import subprocess
    import sys
    hocon = Path(runtime.REGISTRIES / "plumbing_engineer.hocon")
    r = subprocess.run([sys.executable, "-m", "neuro_san.client.hocon_validator_cli", str(hocon)], capture_output=True, text=True,
                       env={**os.environ, "GEMINI_API_KEY": "test"})
    assert "Validation passed" in r.stdout + r.stderr


def test_chat_endpoint_passes_the_plan_as_sly_data_and_returns_the_diagram(monkeypatch):
    seen = {}

    def fake_chat(network, message, sly, ctx):
        seen.update(network=network, message=message, sly=sly, ctx=ctx)
        return {"answer": "Done.", "chat_context": {"chat_histories": []}, "sly_data": {"plumbing_diagram_id": "d-1"},
                "tools_called": ["plumb_plan"], "trace": [], "seconds": 1.0}
    monkeypatch.setattr(runtime, "chat", fake_chat)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Agent API"}).json()["id"]
    r = client.post(f"/projects/{pid}/agents/plumbing/chat", json={"message": " Plumb it ", "source": SOURCE, "chat_context": {"a": 1}})
    assert r.status_code == 200, r.text
    assert r.json() == {"answer": "Done.", "chat_context": {"chat_histories": []}, "diagrams": {"plumbing": "d-1"},
                        "estimate_id": None, "green_id": None, "design": None, "revision_run_id": None, "tools_called": ["plumb_plan"], "seconds": 1.0}
    assert seen["network"] == "plumbing_engineer" and seen["message"] == "Plumb it" and seen["ctx"] == {"a": 1}
    assert seen["sly"]["project_id"] == pid and seen["sly"]["routing_source"]["source_hash"] == SOURCE["source_hash"]
    assert client.post(f"/projects/{pid}/agents/accountant/chat", json={"message": "x", "source": SOURCE}).status_code == 404
    assert client.post(f"/projects/{pid}/agents/constructos/chat", json={"message": "x", "layout": {"run_id": "missing"}}).status_code == 404


def test_chat_endpoint_reports_agent_failures_cleanly(monkeypatch):
    monkeypatch.setattr(runtime, "chat", lambda *a: (_ for _ in ()).throw(TimeoutError("slow")))
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Agent API"}).json()["id"]
    r = client.post(f"/projects/{pid}/agents/plumbing/chat", json={"message": "x", "source": SOURCE})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "AGENT_FAILED"
