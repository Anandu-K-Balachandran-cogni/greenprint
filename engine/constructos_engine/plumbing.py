"""Plumbing diagram: cold-water supply and directed drainage from the saved plan.

Supply is routed from an assumed water entry to every fixture on the shared
allowed-path graph (as for electrical). Drainage is routed from every fixture
to the drain exit on a *directed acyclic* graph: each undirected edge is kept
only in the direction that decreases its rank (shortest distance to the exit,
ties broken by node key), and stacks only run downwards. Every drainage step
therefore moves strictly towards the exit, so the combined network cannot
contain a cycle. This restriction can exclude otherwise possible layouts.

Without floor levels and pipe slopes, gravity drainage is not verified; a
directed route is a planning topology, not an engineered drain.
"""

import math
import uuid
from datetime import datetime, timezone

import networkx as nx
from shapely.geometry import Point, Polygon

from . import routing
from .electrical import (Circuit, Diagram, DiagramMetrics, Endpoint, Route, RoutingSource, Totals, _room_doors,
                         _wall_positions)
from .routing import Anchor, Node, Policy

# fixture kinds per wet room type
FIXTURES: dict[str, list[str]] = {
    "bathroom": ["wc", "basin", "shower"], "toilet": ["wc", "basin"], "kitchen": ["sink"],
    "utility": ["washing_machine"], "laundry": ["washing_machine"], "garage": ["tap"],
    "terrace": ["floor_drain"], "balcony": ["floor_drain"],
}
SUPPLY = {"wc", "basin", "shower", "sink", "washing_machine", "tap"}
DRAIN = {"wc", "basin", "shower", "sink", "washing_machine", "floor_drain"}
LABEL = {"wc": "WC", "basin": "Wash basin", "shower": "Shower", "sink": "Kitchen sink", "washing_machine": "Washing machine",
         "tap": "Wash tap", "floor_drain": "Floor drain", "water_entry": "Water supply entry", "drain_exit": "Drain exit (manhole)",
         "stack": "Stack"}
WET = {"bathroom", "toilet", "kitchen", "utility", "laundry"}


def _stack_preference(room_type: str) -> int:
    """Where a stack may pass on the floor below: wet rooms first, bedrooms last."""
    if room_type in WET:
        return 0
    if room_type in ("hall", "corridor", "foyer", "garage", "store", "stair"):
        return 1
    if room_type in ("living", "dining", "family", "study", "home_office", "prayer"):
        return 2
    return 3


def _front_side(source: RoutingSource, ground: int) -> tuple[str, float]:
    """The building edge facing the road: the bounding-box side nearest the entrance door."""
    pts = [p for r in source.rooms if r.floor == ground for p in r.polygon]
    minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
    miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)
    door = next((d for d in source.doors if d.floor == ground and d.entrance), None)
    if door is None:
        return "maxy", maxy  # editor convention: the road is drawn at the bottom
    sides = {"minx": abs(door.x_mm - minx), "maxx": abs(door.x_mm - maxx), "miny": abs(door.y_mm - miny), "maxy": abs(door.y_mm - maxy)}
    side = min(sides, key=sides.get)
    return side, {"minx": minx, "maxx": maxx, "miny": miny, "maxy": maxy}[side]


def _on_front(poly: Polygon, side: str, value: float) -> bool:
    coords = poly.exterior.coords
    axis = 0 if side.endswith("x") else 1
    return any(abs(c[axis] - value) < 5 for c in coords)


def _front_point(poly: Polygon, side: str, value: float, near: tuple[float, float]) -> tuple[float, float]:
    """A point just inside the road-side wall of `poly`, as close as possible to `near`."""
    inset = routing.TRACK_OFFSET_MM
    minx, miny, maxx, maxy = poly.bounds
    if side in ("miny", "maxy"):
        x = min(max(near[0], minx + 600), maxx - 600)
        return (x, value + inset if side == "miny" else value - inset)
    y = min(max(near[1], miny + 600), maxy - 600)
    return (value + inset if side == "minx" else value - inset, y)


def generate(source: RoutingSource, *, entry_room: str | None = None, exit_room: str | None = None,
             policy: Policy | None = None) -> Diagram:
    policy = policy or Policy()
    floors = sorted(f.index for f in source.floors)
    ground = floors[0]
    rooms = {(r.floor, r.id): r for r in source.rooms}
    polys = {k: Polygon(r.polygon) for k, r in rooms.items()}
    assumptions: list[str] = []
    unresolved: list[dict] = []
    endpoints: list[Endpoint] = []
    count = 0

    def add(kind, room, floor, xy, label=None) -> Endpoint:
        nonlocal count
        count += 1
        ep = Endpoint(id=f"p{count}-{kind}", kind=kind, room_id=room, floor=floor, x_mm=round(xy[0]), y_mm=round(xy[1]),
                      mount="wall", label=label or LABEL[kind])
        endpoints.append(ep)
        return ep

    # Fixtures along each wet room's service track, clear of doors.
    for (f, rid), r in rooms.items():
        kinds = FIXTURES.get(r.type, [])
        track = routing._track(polys[(f, rid)])
        if not kinds or track is None:
            continue
        doors = _room_doors(polys[(f, rid)], source.doors, f)
        for kind, xy in zip(kinds, _wall_positions(track, polys[(f, rid)], doors, len(kinds), clear_mm=400)):
            add(kind, rid, f, xy)
    fixtures = list(endpoints)
    if not fixtures:
        unresolved.append({"endpoint_id": None, "reason": "The plan has no wet rooms (bathroom, toilet, kitchen or utility)."})

    # Water entry and drain exit just inside the road-side wall.
    side, value = _front_side(source, ground)
    front_rooms = [r for (f, _), r in rooms.items() if f == ground and not r.outdoor and _on_front(polys[(f, r.id)], side, value)]
    wet_ground = [e for e in fixtures if e.floor == ground] or fixtures
    centre = (sum(e.x_mm for e in wet_ground) / max(1, len(wet_ground)), sum(e.y_mm for e in wet_ground) / max(1, len(wet_ground)))

    def pick(room_id: str | None, prefer: tuple[str, ...]) -> tuple | None:
        chosen = next((r for r in front_rooms if r.id == room_id), None)
        if chosen is None:
            for t in prefer:
                chosen = min((r for r in front_rooms if r.type == t), default=None,
                             key=lambda r: polys[(ground, r.id)].distance(Point(centre)))
                if chosen:
                    break
        if chosen is None and front_rooms:
            chosen = min(front_rooms, key=lambda r: polys[(ground, r.id)].distance(Point(centre)))
        return chosen

    entry = pick(entry_room, ("garage", "foyer", "utility", "kitchen"))
    exit_ = pick(exit_room, ("garage", "utility", "kitchen", "foyer"))
    entry_ep = exit_ep = None
    exit_xy = None
    if exit_:
        exit_xy = _front_point(polys[(ground, exit_.id)], side, value, centre)
        exit_ep = add("drain_exit", exit_.id, ground, exit_xy)
        assumptions.append(f"Drainage leaves through a manhole at the road-side wall of {exit_.name}{'' if exit_room == exit_.id else ' (assumed)'}.")
    if entry:
        # Keep the supply entry clear of the manhole: the far end of the room's road-side wall if needed.
        epoly = polys[(ground, entry.id)]
        xy = _front_point(epoly, side, value, centre)
        if exit_xy and math.dist(xy, exit_xy) < 1500:
            minx, miny, maxx, maxy = epoly.bounds
            far = [(minx, miny), (maxx, maxy)]
            xy = max((_front_point(epoly, side, value, f) for f in far), key=lambda p: math.dist(p, exit_xy))
        entry_ep = add("water_entry", entry.id, ground, xy)
        assumptions.append(f"Water supply enters through the road-side wall of {entry.name}{'' if entry_room == entry.id else ' (assumed)'}.")
    if not (entry and exit_):
        unresolved.append({"endpoint_id": None, "reason": "No ground-floor room touches the road side for the water entry and drain exit."})

    # One stack per upper floor with wet fixtures: where an upper wet room sits over a ground room.
    stacks: list[tuple[str, int, int, str, str, tuple[float, float]]] = []  # (id, lower, upper, lower room, upper room, xy)
    for lower, upper in zip(floors, floors[1:]):
        upper_wet = [e for e in fixtures if e.floor >= upper]
        if not upper_wet:
            continue
        best = None
        # Wet rooms first; a floor whose only drains are on terraces gets a down-pipe from a terrace.
        drained = {e.room_id for e in upper_wet if e.floor == upper}
        for candidates in ([r for (f, _), r in rooms.items() if f == upper and r.type in WET],
                           [r for (f, _), r in rooms.items() if f == upper and r.id in drained]):
            for r in candidates:
                rid = r.id
                c = polys[(upper, rid)].centroid
                for corner in polys[(upper, rid)].exterior.coords[:-1]:
                    # 240 mm in from the corner, towards the room's centre
                    inside = (corner[0] + math.copysign(240, c.x - corner[0]), corner[1] + math.copysign(240, c.y - corner[1]))
                    below = next((lr for (lf, lid), lr in rooms.items() if lf == lower and not lr.outdoor and lr.type != "stair"
                                  and polys[(lf, lid)].contains(Point(inside))), None)
                    if below is None:
                        continue
                    score = (_stack_preference(below.type), math.dist(inside, centre))
                    if best is None or score < best[0]:
                        best = (score, rid, below.id, inside)
            if best is not None:
                break
        if best is None:
            unresolved.append({"endpoint_id": None, "reason": f"No room with drains on floor {upper} sits over a ground room for a stack."})
            continue
        if rooms[(upper, best[1])].outdoor:
            assumptions.append(f"Floor {upper} has no wet room; its terrace drains go down a rainwater pipe from "
                               f"{rooms[(upper, best[1])].name}.")
        sid = f"S{len(stacks) + 1}"
        stacks.append((sid, lower, upper, best[2], best[1], best[3]))
        if best[0][0] == 3:
            assumptions.append(f"Stack {sid} has to pass through a bedroom below; consider stacking wet rooms.")
        kind = "rainwater pipe" if rooms[(upper, best[1])].outdoor else "supply riser and soil/waste pipe"
        assumptions.append(f"Stack {sid} ({kind}) runs from {rooms[(upper, best[1])].name} down into "
                           f"{rooms[(lower, best[2])].name}; it carries the same ID on every floor.")

    # Graph with every point on a service track.
    routed = [e for e in endpoints]
    anchors = [Anchor(e.floor, e.room_id, (e.x_mm, e.y_mm)) for e in routed]
    for sid, lower, upper, lroom, uroom, xy in stacks:
        anchors += [Anchor(lower, lroom, xy), Anchor(upper, uroom, xy)]
    g, nodes = routing.build_graph(source.rooms, anchors)
    node_of = {e.id: nodes.get(i) for i, e in enumerate(routed)}
    stack_nodes = []
    for i, (sid, lower, upper, lroom, uroom, xy) in enumerate(stacks):
        lo, up = nodes.get(len(routed) + 2 * i), nodes.get(len(routed) + 2 * i + 1)
        if lo and up:
            routing.add_riser(g, lo, up)
            stack_nodes.append((sid, lo, up))
            for f in (lower, upper):
                endpoints.append(Endpoint(id=f"{sid}-f{f}", kind="stack", room_id=lroom if f == lower else uroom, floor=f,
                                          x_mm=round(xy[0]), y_mm=round(xy[1]), mount="wall", label=f"Stack {sid}"))

    inside = routing.building_outline(g)
    routes: list[Route] = []
    validation: list[str] = []
    net_metrics: dict[str, DiagramMetrics] = {}

    def totals(ms) -> Totals:
        return Totals(length_mm=round(sum(m.length_mm for m in ms)), bends=sum(m.bends for m in ms),
                      transitions=sum(m.transitions for m in ms), j=round(sum(m.j for m in ms)))

    def select(base, cand):
        mb, mc = routing.measure(base, policy), routing.measure(cand, policy)
        key = lambda m, p: (m.j, m.length_mm, m.bends, m.transitions, str(p))  # noqa: E731
        return (cand, mc, "optimised") if key(mc, cand) <= key(mb, base) else (base, mb, "baseline"), mb

    # --- Supply: entry -> each supplied fixture, undirected graph.
    sup_base, sup_sel = [], []
    if entry_ep and node_of.get(entry_ep.id):
        src = node_of[entry_ep.id]
        prev_b = routing.shortest_length_tree(g, src)
        prev_c, dist_c = routing.bend_aware_tree(g, src, policy)
        for e in [x for x in fixtures if x.kind in SUPPLY]:
            t = node_of.get(e.id)
            base = routing.path_from_tree(prev_b, t) if t else None
            cand = routing.best_state_path(prev_c, dist_c, t) if t else None
            if not base or not cand:
                unresolved.append({"endpoint_id": e.id, "reason": f"{e.label} in {rooms[(e.floor, e.room_id)].name} has no supply route."})
                continue
            (path, m, chosen), mb = select(base, cand)
            validation += [f"{e.id}: {p}" for p in routing.validate_path(g, path, inside)]
            sup_base.append(mb)
            sup_sel.append(m)
            routes.append(Route(endpoint_id=e.id, circuit=f"CW{e.floor}", points=path, length_mm=m.length_mm, bends=m.bends,
                                transitions=m.transitions, j=m.j, chosen=chosen))

    # --- Drainage: each drained fixture -> exit, on the directed acyclic graph.
    dr_base, dr_sel = [], []
    dag_edges: set[tuple[Node, Node]] = set()
    if exit_ep and node_of.get(exit_ep.id):
        sink = node_of[exit_ep.id]
        # Rank = shortest distance to the exit; stacks count as 1 mm so they always point downwards.
        undirected = {u: [(v, (1.0 if k == "riser" else w), k) for v, w, k in vs] for u, vs in g.adj.items()}
        dist = routing.distances_to(undirected, sink)
        rank = lambda n: (dist.get(n, math.inf), n)  # noqa: E731
        downhill: dict[Node, list] = {}  # u -> [(v, w, kind)] where flow u -> v moves towards the exit
        for u, vs in g.adj.items():
            for v, w, k in vs:
                if u in dist and v in dist and rank(v) < rank(u):
                    if k == "riser" and not u[0] > v[0]:
                        continue  # stacks only drain downwards
                    downhill.setdefault(u, []).append((v, w, k))
                    dag_edges.add((u, v))
        # Search from the exit over reversed downhill edges; reversing a path keeps its length and bends.
        uphill: dict[Node, list] = {}
        for u, vs in downhill.items():
            for v, w, k in vs:
                uphill.setdefault(v, []).append((u, w, k))
        prev_b = routing.shortest_length_tree(g, sink, uphill)
        prev_c, dist_c = routing.bend_aware_tree(g, sink, policy, uphill)
        for e in [x for x in fixtures if x.kind in DRAIN]:
            t = node_of.get(e.id)
            base = routing.path_from_tree(prev_b, t) if t else None
            cand = routing.best_state_path(prev_c, dist_c, t) if t else None
            if not base or not cand:
                unresolved.append({"endpoint_id": e.id, "reason": f"{e.label} in {rooms[(e.floor, e.room_id)].name} has no drainage route towards the exit."})
                continue
            base, cand = base[::-1], cand[::-1]  # fixture -> exit
            (path, m, chosen), mb = select(base, cand)
            for a, b in zip(path, path[1:]):
                if (a, b) not in dag_edges:
                    validation.append(f"{e.id}: drainage step {a}->{b} does not flow towards the exit")
            validation += [f"{e.id}: {p}" for p in routing.validate_path(g, path, inside)]
            dr_base.append(mb)
            dr_sel.append(m)
            routes.append(Route(endpoint_id=e.id, circuit=f"DR{e.floor}", points=path, length_mm=m.length_mm, bends=m.bends,
                                transitions=m.transitions, j=m.j, chosen=chosen))
        # The combined drainage network must be acyclic and end at the exit.
        net = nx.DiGraph()
        for r in routes:
            if r.circuit.startswith("DR"):
                net.add_edges_from(zip(map(tuple, r.points), map(tuple, r.points[1:])))
        if net.number_of_nodes() and not nx.is_directed_acyclic_graph(net):
            validation.append("The combined drainage network contains a directed cycle.")
        if any(r.circuit.startswith("DR") and tuple(r.points[-1]) != sink for r in routes):
            validation.append("A drainage route does not end at the exit.")

    def metrics(base, sel, kinds, circuit_prefix) -> DiagramMetrics:
        b, s = totals(base), totals(sel)
        need = [x for x in fixtures if x.kind in kinds]
        return DiagramMetrics(baseline=b, selected=s, improvement_pct=None if b.j == 0 else round(100 * (b.j - s.j) / b.j, 1),
                              unique_corridor_mm=routing.union_length_mm([r.points for r in routes if r.circuit.startswith(circuit_prefix)]),
                              per_connection_mm=s.length_mm, endpoints_total=len(need),
                              endpoints_connected=len([r for r in routes if r.circuit.startswith(circuit_prefix)]),
                              penetrations_available=g.penetrations)

    net_metrics["supply"] = metrics(sup_base, sup_sel, SUPPLY, "CW")
    net_metrics["drainage"] = metrics(dr_base, dr_sel, DRAIN, "DR")
    combined_b, combined_s = totals(sup_base + dr_base), totals(sup_sel + dr_sel)
    for e in fixtures:
        e.circuit = f"DR{e.floor}" if e.kind == "floor_drain" else f"CW{e.floor}"
    circuits = []
    for f in floors:
        sup = [r.endpoint_id for r in routes if r.circuit == f"CW{f}"]
        dr = [r.endpoint_id for r in routes if r.circuit == f"DR{f}"]
        if sup:
            circuits.append(Circuit(id=f"CW{f}", label="Cold water supply", floor=f, endpoint_ids=sup))
        if dr:
            circuits.append(Circuit(id=f"DR{f}", label="Drainage (soil and waste)", floor=f, endpoint_ids=dr))
    assumptions += [
        "Drainage is routed only towards the exit on a directed graph; this restriction can exclude otherwise possible layouts.",
        "Gravity drainage is not verified: there are no floor levels or pipe slopes. Directions show topology, not falls.",
        "Hot water comes from the point-of-use water heaters on the electrical diagram; vents, pipe sizes and fittings are not designed.",
    ]
    need_total = len([x for x in fixtures if x.kind in SUPPLY]) + len([x for x in fixtures if x.kind in DRAIN])
    complete = not unresolved and not validation and len(routes) == need_total
    return Diagram(
        id=str(uuid.uuid4()), kind="plumbing", editor_project_id=source.editor_project_id, source_hash=source.source_hash,
        created_at=datetime.now(timezone.utc).isoformat(), policy=policy, floors=source.floors, rooms=source.rooms,
        walls=source.walls, doors=source.doors, endpoints=endpoints, routes=routes, controls=[], circuits=circuits,
        metrics=DiagramMetrics(baseline=combined_b, selected=combined_s,
                               improvement_pct=None if combined_b.j == 0 else round(100 * (combined_b.j - combined_s.j) / combined_b.j, 1),
                               unique_corridor_mm=routing.union_length_mm([r.points for r in routes]),
                               per_connection_mm=combined_s.length_mm, endpoints_total=need_total,
                               endpoints_connected=len(routes), penetrations_available=g.penetrations),
        unresolved=unresolved, assumptions=assumptions, status="complete" if complete else "partial", validation=validation,
        network_metrics=net_metrics, gravity="unverified",
    )
