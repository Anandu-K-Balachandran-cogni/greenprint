import json
from pathlib import Path

import networkx as nx
import pytest

from constructos_engine import plumbing
from constructos_engine.solver import Allocation
from routing_fixtures import source_from_allocation

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "tests" / "fixtures" / "constructos"


def load(name: str):
    return source_from_allocation(Allocation.model_validate(json.loads((FIXTURES / f"{name}-allocation.json").read_text())))


@pytest.fixture(scope="module")
def house():
    src = load("two-storey")
    return src, plumbing.generate(src)


def test_two_storey_supply_and_drainage_are_complete_and_validated(house):
    _, d = house
    assert d.kind == "plumbing" and d.status == "complete" and d.validation == [] and d.unresolved == []
    sup, dr = d.network_metrics["supply"], d.network_metrics["drainage"]
    assert sup.endpoints_connected == sup.endpoints_total > 0
    assert dr.endpoints_connected == dr.endpoints_total > 0
    assert sup.selected.j <= sup.baseline.j and dr.selected.j <= dr.baseline.j
    assert d.gravity == "unverified"
    assert any("Gravity drainage is not verified" in a for a in d.assumptions)


def test_drainage_only_flows_to_the_exit_without_cycles(house):
    _, d = house
    exit_ = next(e for e in d.endpoints if e.kind == "drain_exit")
    drains = [r for r in d.routes if r.circuit.startswith("DR")]
    net = nx.DiGraph()
    assert len({tuple(r.points[-1]) for r in drains}) == 1  # every drain ends at the same exit node
    assert drains[0].points[-1][0] == exit_.floor
    for r in drains:
        pts = [tuple(p) for p in r.points]
        assert len(set(pts)) == len(pts)  # no node revisited
        net.add_edges_from(zip(pts, pts[1:]))
    assert nx.is_directed_acyclic_graph(net)
    sinks = [n for n in net.nodes if net.out_degree(n) == 0]
    assert len(sinks) == 1  # everything drains to one exit
    # Drains never go up a floor.
    for r in drains:
        floors = [p[0] for p in r.points]
        assert floors == sorted(floors, reverse=True)


def test_supply_starts_at_the_entry_and_upper_floors_use_the_named_stack(house):
    _, d = house
    entry = next(e for e in d.endpoints if e.kind == "water_entry")
    supply = [r for r in d.routes if r.circuit.startswith("CW")]
    assert {tuple(r.points[0]) for r in supply} == {tuple(supply[0].points[0])}
    assert supply[0].points[0][0] == entry.floor
    stacks = [e for e in d.endpoints if e.kind == "stack"]
    assert {e.label for e in stacks} == {"Stack S1"} and {e.floor for e in stacks} == {0, 1}
    assert len({(e.x_mm, e.y_mm) for e in stacks}) == 1  # same position on every floor
    for r in d.routes:
        if r.points[-1][0] == 1 or r.points[0][0] == 1:
            assert r.transitions == 1
    below = next(e for e in stacks if e.floor == 0)
    room = next(r for r in d.rooms if r.floor == 0 and r.id == below.room_id)
    assert room.type not in ("bedroom", "master_bedroom", "guest_room")


def test_fixtures_are_where_the_wet_rooms_are(house):
    src, d = house
    types = {(r.floor, r.id): r.type for r in src.rooms}
    for e in d.endpoints:
        if e.kind in ("wc", "shower"):
            assert types[(e.floor, e.room_id)] in ("bathroom", "toilet")
        if e.kind == "sink":
            assert types[(e.floor, e.room_id)] == "kitchen"


def test_chosen_entry_and_exit_rooms_are_respected():
    src = load("single-floor")
    front = [r.id for r in src.rooms if r.type in ("foyer", "garage")]
    d = plumbing.generate(src, entry_room="foyer-1", exit_room="parking-1")
    assert next(e for e in d.endpoints if e.kind == "water_entry").room_id == "foyer-1"
    assert next(e for e in d.endpoints if e.kind == "drain_exit").room_id == "parking-1"
    assert "foyer-1" in front


def test_isolated_wet_room_and_no_wet_rooms_are_reported():
    src = load("single-floor")
    broken = src.model_copy(deep=True)
    bath = next(r for r in broken.rooms if r.type == "bathroom")
    bath.polygon = [(x + 50_000, y) for x, y in bath.polygon]
    d = plumbing.generate(broken)
    assert d.status == "partial" and any("no supply route" in u["reason"] or "no drainage route" in u["reason"] for u in d.unresolved)

    dry = src.model_copy(deep=True)
    dry.rooms = [r for r in dry.rooms if r.type not in plumbing.FIXTURES]
    d = plumbing.generate(dry)
    assert d.status == "partial" and any("no wet rooms" in u["reason"] for u in d.unresolved)


def test_water_entry_is_kept_clear_of_the_manhole(house):
    import math
    _, d = house
    entry = next(e for e in d.endpoints if e.kind == "water_entry")
    exit_ = next(e for e in d.endpoints if e.kind == "drain_exit")
    assert math.dist((entry.x_mm, entry.y_mm), (exit_.x_mm, exit_.y_mm)) >= 1500
