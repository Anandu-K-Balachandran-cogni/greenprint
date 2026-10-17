"""Project ledger and status: every step is recorded with its actor, and status says what comes next."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import wait_run
from constructos_engine import ledger
from constructos_engine.main import app
from test_layout import single_floor_brief

SOURCE = json.loads((Path(__file__).parent / "fixtures/sharma_routing_source.json").read_text())


def test_status_walks_through_the_project_and_the_ledger_records_each_step():
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Ledger"}).json()["id"]
    assert client.get(f"/projects/{pid}/status").json()["next_step"].startswith("Describe the home")
    brief = single_floor_brief().model_dump(mode="json")
    assert client.post(f"/projects/{pid}/brief/confirm", json={"brief": brief, "expected_version": 0}).status_code == 200
    assert client.get(f"/projects/{pid}/status").json()["next_step"] == "Generate layout options."
    run = client.post(f"/projects/{pid}/layouts", json={"expected_brief_version": 1, "alternatives": 1, "time_limit_s": 10}).json()
    wait_run(client, pid, run["id"])
    st = client.get(f"/projects/{pid}/status").json()
    assert st["latest_generation"]["run_id"] == run["id"] and st["latest_generation"]["options"][0]["checks"] == "PASS"
    assert st["next_step"].startswith("Open an option in the editor")
    assert client.post(f"/projects/{pid}/diagrams/plumbing", json={"source": SOURCE}).status_code == 201
    st = client.get(f"/projects/{pid}/status", params={"source_hash": SOURCE["source_hash"]}).json()
    assert st["services"]["plumbing"]["matches_saved_plan"] is True and st["next_step"] == "Generate the electrical layout."
    stale = client.get(f"/projects/{pid}/status", params={"source_hash": "sha256-other"}).json()
    assert stale["next_step"].startswith("The plan changed")
    steps = client.get(f"/projects/{pid}/ledger").json()
    assert [(s["stage"], s["action"]) for s in steps] == [("brief", "confirmed"), ("layouts", "queued"), ("layouts", "ready"),
                                                         ("services", "plumbing")]
    assert {s["actor"] for s in steps} == {"user"}
    assert steps[-1]["refs"]["source_hash"] == SOURCE["source_hash"]


def test_agent_steps_are_attributed_to_the_agent():
    import asyncio
    from constructos_engine import store
    from constructos_engine.agents.plumbing_tools import PlumbPlan
    pid = store.create("Ledger agent")["id"]
    asyncio.run(PlumbPlan().async_invoke({}, {"project_id": pid, "routing_source": SOURCE}))
    steps = ledger.steps(pid)
    assert [(s["actor"], s["action"]) for s in steps] == [("agent:plumbing_engineer", "plumbing")]
    assert "drains 12/12" in steps[0]["summary"]
