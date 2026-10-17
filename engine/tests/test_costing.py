"""Cost estimate: quantities from the saved plan, auditable lines, tiers inside the published bands."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from constructos_engine import costing, ledger, plumbing
from constructos_engine.electrical import RoutingSource, SourceDoor, SourceWindow
from constructos_engine.main import app

SOURCE = RoutingSource.model_validate(json.loads((Path(__file__).parent / "fixtures/sharma_routing_source.json").read_text()))


def test_quantities_come_from_the_saved_plan():
    q, notes = costing.quantities(SOURCE, None, None)
    assert q.floors == 2 and q.built_up_sqft == round(q.built_up_m2 * costing.SQFT_PER_M2)
    assert 160 < q.built_up_m2 < 170 and q.terrace_m2 > 0
    assert q.doors["main"] == 1 and q.doors.get("garage", 0) == 0  # one front door; terrace doors are "external"
    assert q.electrical_estimated and q.plumbing_estimated and q.windows_estimated
    assert any("no windows" in n for n in notes)


def test_lines_add_up_and_every_line_is_quantity_times_rate():
    e = costing.estimate(SOURCE)
    for line in e.lines:
        assert line.amount == round(line.quantity * line.rate)
    assert e.subtotal == sum(e.groups.values()) == sum(x.amount for x in e.lines)
    assert e.total == e.subtotal + e.contingency and e.contingency == round(e.subtotal * costing.CONTINGENCY)
    assert e.per_sqft == round(e.total / e.quantities.built_up_sqft)


@pytest.mark.parametrize("tier", ["basic", "standard", "premium"])
@pytest.mark.parametrize("location", ["non_metro", "metro"])
def test_each_tier_lands_in_its_published_band(tier, location):
    e = costing.estimate(SOURCE, plumbing=plumbing.generate(SOURCE).model_dump(mode="json"), tier=tier, location=location)
    assert e.benchmark["verdict"] == "within", (tier, location, e.per_sqft, e.benchmark)


def test_metro_costs_more_and_premium_costs_more():
    base = costing.estimate(SOURCE).total
    assert costing.estimate(SOURCE, location="metro").total > base > costing.estimate(SOURCE, tier="basic").total
    assert costing.estimate(SOURCE, tier="premium").total > base


def test_plan_windows_and_services_replace_allowances():
    src = SOURCE.model_copy(update={"windows": [SourceWindow(floor=0, width_mm=1200, height_mm=1200)] * 5})
    pl = plumbing.generate(SOURCE).model_dump(mode="json")
    q, _ = costing.quantities(src, None, pl)
    assert (q.windows, q.windows_m2, q.windows_estimated) == (5, 7.2, False)
    assert not q.plumbing_estimated and q.drainage_points == 12


def test_estimate_api_stores_records_and_feeds_status():
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Cost"}).json()["id"]
    body = {"source": SOURCE.model_dump(mode="json"), "tier": "premium", "location": "metro"}
    r = client.post(f"/projects/{pid}/estimates", json=body)
    assert r.status_code == 201, r.text
    est = r.json()
    assert est["tier"] == "premium" and est["services_current"] == {"electrical": None, "plumbing": None}
    listed = client.get(f"/projects/{pid}/estimates").json()
    assert [x["id"] for x in listed] == [est["id"]] and "blob" not in listed[0]
    assert client.get(f"/projects/{pid}/estimates/{est['id']}").json()["total"] == est["total"]
    step = ledger.steps(pid)[-1]
    assert step["stage"] == "cost" and "premium" in step["summary"] and step["refs"]["estimate_id"] == est["id"]
    st = client.get(f"/projects/{pid}/status", params={"source_hash": SOURCE.source_hash}).json()
    assert st["cost_estimate"]["estimate_id"] == est["id"] and st["cost_estimate"]["matches_saved_plan"] is True
    assert client.post(f"/projects/{pid}/estimates", json={**body, "tier": "luxury"}).status_code == 422


def test_cost_tools_return_grounded_summaries():
    import asyncio
    from constructos_engine import store
    from constructos_engine.agents.cost_tools import CostFacts, EstimateCost
    pid = store.create("Cost tools")["id"]
    sly = {"project_id": pid, "routing_source": SOURCE.model_dump(mode="json")}
    assert "No cost estimate" in json.loads(asyncio.run(CostFacts().async_invoke({}, dict(sly))))["error"]
    out = json.loads(asyncio.run(EstimateCost().async_invoke({"tier": "basic"}, sly)))
    stored = store.get_estimate(pid, sly["estimate_id"])
    assert out["total_lakh_inr"] == round(stored["total"] / 1e5, 2) and out["tier"] == "basic"
    facts = json.loads(asyncio.run(CostFacts().async_invoke({}, {"project_id": pid, "routing_source": sly["routing_source"]})))
    assert facts["estimate_id"] == stored["id"] and len(facts["all_lines"]) == len(stored["lines"])
    assert "Tier must be" in json.loads(asyncio.run(EstimateCost().async_invoke({"tier": "luxury"}, dict(sly))))["error"]
