import json
from pathlib import Path

import networkx as nx
import pytest

from constructos_engine import electrical, routing
from constructos_engine.routing import Graph, Policy
from constructos_engine.solver import Allocation
from routing_fixtures import source_from_allocation

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "tests" / "fixtures" / "constructos"
POLICY = Policy()


def grid_graph(edges) -> Graph:
    g = Graph()
    for a, b in edges:
        g.add_edge((0, *a), (0, *b), "track")
    return g


def exhaustive_best(g: Graph, s, t):
    nxg = nx.Graph()
    for u, vs in g.adj.items():
        for v, _, _ in vs:
            nxg.add_edge(u, v)
    return min(routing.measure(p, POLICY).j for p in nx.all_simple_paths(nxg, s, t))


# --- Search oracles on tiny graphs -------------------------------------------------

def test_bend_aware_search_matches_exhaustive_enumeration_where_shortest_is_not_best():
    # Staircase: 6 x 1000 mm, 5 bends -> J = 6000 + 2500 = 8500 (the shortest path).
    stairs = [((0, 0), (1000, 0)), ((1000, 0), (1000, 1000)), ((1000, 1000), (2000, 1000)), ((2000, 1000), (2000, 2000)),
              ((2000, 2000), (3000, 2000)), ((3000, 2000), (3000, 3000))]
    # Detour: 200 + 3200 + 3200 + 200 = 6800 mm, 3 bends -> J = 6800 + 1500 = 8300 (longer but better).
    detour = [((0, 0), (-200, 0)), ((-200, 0), (-200, 3200)), ((-200, 3200), (3000, 3200)), ((3000, 3200), (3000, 3000))]
    g = grid_graph(stairs + detour)
    s, t = (0, 0, 0), (0, 3000, 3000)
    base = routing.path_from_tree(routing.shortest_length_tree(g, s), t)
    prev, dist = routing.bend_aware_tree(g, s, POLICY)
    cand = routing.best_state_path(prev, dist, t)
    mb, mc = routing.measure(base, POLICY), routing.measure(cand, POLICY)
    assert (mb.length_mm, mb.bends, mb.j) == (6000, 5, 8500)
    assert (mc.length_mm, mc.bends, mc.j) == (6800, 3, 8300)
    assert mc.j == exhaustive_best(g, s, t)


def test_equal_routes_tie_break_is_stable_and_reports_no_improvement():
    square = [((0, 0), (1000, 0)), ((1000, 0), (1000, 1000)), ((0, 0), (0, 1000)), ((0, 1000), (1000, 1000))]
    g = grid_graph(square)
    s, t = (0, 0, 0), (0, 1000, 1000)
    runs = {tuple(routing.path_from_tree(routing.shortest_length_tree(g, s), t)) for _ in range(5)}
    assert len(runs) == 1
    prev, dist = routing.bend_aware_tree(g, s, POLICY)
    assert routing.measure(routing.best_state_path(prev, dist, t), POLICY).j == routing.measure(list(next(iter(runs))), POLICY).j


def test_measure_counts_bends_and_transitions_from_coordinates():
    path = [(0, 0, 0), (0, 1000, 0), (0, 1000, 1000), (1, 1000, 1000), (1, 2000, 1000)]
    m = routing.measure(path, POLICY)
    assert (m.length_mm, m.bends, m.transitions, m.j) == (3000, 1, 1, 3000 + 500 + 2000)


def test_disconnected_target_is_unreachable_not_invented():
    g = grid_graph([((0, 0), (1000, 0)), ((5000, 0), (6000, 0))])
    assert routing.path_from_tree(routing.shortest_length_tree(g, (0, 0, 0)), (0, 6000, 0)) is None


# --- Electrical on the solved two-storey house ------------------------------------

@pytest.fixture(scope="module")
def house():
    alloc = Allocation.model_validate(json.loads((FIXTURES / "two-storey-allocation.json").read_text()))
    src = source_from_allocation(alloc)
    return src, electrical.generate(src)


def test_every_supply_point_is_routed_validated_and_never_worse_than_baseline(house):
    _, d = house
    assert d.status == "complete" and d.validation == [] and d.unresolved == []
    routed = [e for e in d.endpoints if e.kind not in ("db", "riser", "light", "fan", "exhaust")]
    assert d.metrics.endpoints_connected == d.metrics.endpoints_total == len(routed) == len(d.routes)
    assert d.metrics.selected.j <= d.metrics.baseline.j
    for r in d.routes:
        m = routing.measure([tuple(p) for p in r.points], d.policy)
        assert (m.length_mm, m.bends, m.transitions, m.j) == (r.length_mm, r.bends, r.transitions, r.j)
    assert d.metrics.unique_corridor_mm < d.metrics.per_connection_mm


def test_db_riser_and_controls_follow_the_plan(house):
    src, d = house
    db = next(e for e in d.endpoints if e.kind == "db")
    assert db.floor == 0 and db.room_id == "foyer-1"
    upper = [r for r in d.routes if r.points[-1][0] == 1]
    assert upper and all(r.transitions == 1 for r in upper)
    by = {e.id: e for e in d.endpoints}
    for sb, target in d.controls:
        assert by[sb].kind == "switchboard" and by[target].kind in ("light", "fan", "exhaust")
        assert by[sb].room_id == by[target].room_id
    # Control associations are not conductor routes.
    assert not {r.endpoint_id for r in d.routes} & {t for _, t in d.controls}
    # Every room with a track gets a switchboard.
    assert {e.room_id for e in d.endpoints if e.kind == "switchboard"} >= {r.id for r in src.rooms if r.type != "terrace"}


def test_points_stay_inside_their_rooms(house):
    from shapely.geometry import Point, Polygon
    src, d = house
    polys = {(r.floor, r.id): Polygon(r.polygon) for r in src.rooms}
    for e in d.endpoints:
        assert polys[(e.floor, e.room_id)].buffer(1).contains(Point(e.x_mm, e.y_mm)), e


def test_preferences_add_typed_points(house):
    src, _ = house
    d = electrical.generate(src, extras=[electrical.ExtraEndpoint(room_id="bedroom-1", kind="ac_point", reason="AC requested")])
    ac = [e for e in d.endpoints if e.kind == "ac_point"]
    assert len(ac) == 1 and ac[0].room_id == "bedroom-1" and ac[0].circuit == "AC1"
    assert any("AC requested" in a for a in d.assumptions)


def test_an_isolated_room_makes_the_diagram_partial(house):
    src, _ = house
    broken = src.model_copy(deep=True)
    lonely = next(r for r in broken.rooms if r.id == "bedroom-2")
    lonely.polygon = [(x + 40_000, y) for x, y in lonely.polygon]  # detached from every other room
    d = electrical.generate(broken)
    assert d.status == "partial"
    assert any("bedroom" in u["reason"].lower() or "Bedroom" in u["reason"] for u in d.unresolved)
