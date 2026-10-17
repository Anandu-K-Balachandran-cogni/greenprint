"""Allowed-path routing graph and optimisation (shared by electrical and plumbing).

The graph is derived from the saved architecture, not drawn freehand:
- each room gets a service track: its outline offset inwards by a fixed margin,
  so runs stay inside the building and along walls;
- tracks of neighbouring rooms are joined by short wall penetrations at sampled
  points along their shared wall;
- ceiling items drop to the nearest point of their room's track;
- floors are joined only through named risers (explicit transitions).

Objective (plan section 8): J = L_mm + bend_penalty_mm × B + transition_penalty_mm × T.
The baseline is the length-only shortest path from the source; the candidate is
the J-optimal path from a Dijkstra over (node, incoming heading) states. Both
are measured on the same graph and endpoints, and the better by (J, L, B, T,
serialisation) is selected, so the selection can never be worse than baseline.
"""

import heapq
import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

TRACK_OFFSET_MM = 150
SAMPLE_MM = 300
PENETRATION_SPACING_MM = 600
BEND_TOLERANCE_DEG = 5

Node = tuple[int, int, int]  # (floor, x_mm, y_mm)
EdgeKind = Literal["track", "penetration", "ceiling", "riser"]


class Policy(BaseModel):
    version: str = "routing.policy.v1"
    bend_penalty_mm: int = 500
    transition_penalty_mm: int = 2000
    track_offset_mm: int = TRACK_OFFSET_MM
    algorithm: str = "dijkstra over (node, incoming heading); baseline = length-only dijkstra"


class RoomShape(BaseModel):
    id: str
    name: str
    type: str
    floor: int
    polygon: list[tuple[float, float]]  # mm, plan coordinates
    outdoor: bool = False


@dataclass
class Graph:
    adj: dict[Node, list[tuple[Node, float, EdgeKind]]] = field(default_factory=dict)
    room_track: dict[tuple[int, str], LineString] = field(default_factory=dict)
    room_poly: dict[tuple[int, str], Polygon] = field(default_factory=dict)
    penetrations: int = 0

    def add_edge(self, a: Node, b: Node, kind: EdgeKind, length: float | None = None) -> None:
        if a == b:
            return
        d = length if length is not None else math.dist(a[1:], b[1:])
        for u, v in ((a, b), (b, a)):
            lst = self.adj.setdefault(u, [])
            if not any(n == v for n, _, _ in lst):
                lst.append((v, d, kind))

    def has_edge(self, a: Node, b: Node) -> bool:
        return any(n == b for n, _, _ in self.adj.get(a, []))


def _key(floor: int, p) -> Node:
    return (floor, int(round(p[0])), int(round(p[1])))


def _track(poly: Polygon) -> LineString | None:
    for offset in (TRACK_OFFSET_MM, TRACK_OFFSET_MM / 3):
        inner = poly.buffer(-offset, join_style="mitre")
        if not inner.is_empty and inner.geom_type == "Polygon" and inner.area > 0:
            return LineString(inner.exterior.coords)
    return None


@dataclass
class Anchor:
    """A point that must become a node on a room's track (endpoint, penetration, riser...)."""
    floor: int
    room: str
    xy: tuple[float, float]


def build_graph(rooms: list[RoomShape], anchors: list[Anchor]) -> tuple[Graph, dict[int, Node]]:
    """Build the allowed graph. Returns the graph and, per anchor index, the track node it maps to."""
    g = Graph()
    insert: dict[tuple[int, str], list[float]] = {}
    for r in rooms:
        poly = Polygon(r.polygon)
        if not poly.is_valid or poly.area <= 0:
            continue
        track = _track(poly)
        if track is None:
            continue
        g.room_poly[(r.floor, r.id)] = poly
        g.room_track[(r.floor, r.id)] = track
        insert[(r.floor, r.id)] = []

    # Wall penetrations along shared walls of neighbouring rooms.
    pen_pairs: list[tuple[tuple[int, str], float, tuple[int, str], float]] = []
    keys = list(g.room_poly)
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if ka[0] != kb[0]:
                continue
            shared = g.room_poly[ka].boundary.intersection(g.room_poly[kb].boundary)
            if shared.is_empty or shared.length < SAMPLE_MM:
                continue
            for line in getattr(shared, "geoms", [shared]):
                if line.geom_type != "LineString" or line.length < SAMPLE_MM:
                    continue
                n = max(1, int(line.length // PENETRATION_SPACING_MM))
                for j in range(n):
                    p = line.interpolate((j + 0.5) * line.length / n)
                    ta, tb = g.room_track[ka], g.room_track[kb]
                    da, db = ta.project(p), tb.project(p)
                    insert[ka].append(da)
                    insert[kb].append(db)
                    pen_pairs.append((ka, da, kb, db))

    anchor_pos: dict[int, tuple[tuple[int, str], float]] = {}
    for idx, a in enumerate(anchors):
        k = (a.floor, a.room)
        if k not in g.room_track:
            continue
        d = g.room_track[k].project(Point(a.xy))
        insert[k].append(d)
        anchor_pos[idx] = (k, d)

    # Track rings: vertices, regular samples and every inserted position.
    for k, track in g.room_track.items():
        total = track.length
        cuts = {0.0}
        acc = 0.0
        coords = list(track.coords)
        for p, q in zip(coords, coords[1:]):
            acc += math.dist(p, q)
            cuts.add(acc)
        cuts |= {i * SAMPLE_MM for i in range(int(total // SAMPLE_MM) + 1)}
        cuts |= set(insert[k])
        ordered = sorted(c % total for c in cuts)
        pts = [_key(k[0], track.interpolate(c).coords[0]) for c in ordered]
        ring = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        for p, q in zip(ring, ring[1:] + ring[:1]):
            g.add_edge(p, q, "track")

    for ka, da, kb, db in pen_pairs:
        a = _key(ka[0], g.room_track[ka].interpolate(da).coords[0])
        b = _key(kb[0], g.room_track[kb].interpolate(db).coords[0])
        g.add_edge(a, b, "penetration")
        g.penetrations += 1

    nodes = {idx: _key(k[0], g.room_track[k].interpolate(d).coords[0]) for idx, (k, d) in anchor_pos.items()}
    return g, nodes


def add_ceiling_drop(g: Graph, floor: int, room: str, xy: tuple[float, float]) -> Node | None:
    """Join a ceiling item to the nearest existing node on its room's track."""
    k = (floor, room)
    if k not in g.room_track:
        return None
    target = _key(floor, xy)
    candidates = [n for n in g.adj if n[0] == floor and g.room_poly[k].buffer(1).contains(Point(n[1:]))]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda n: (math.dist(n[1:], target[1:]), n))
    g.add_edge(target, nearest, "ceiling")
    return target


def add_riser(g: Graph, lower: Node, upper: Node) -> None:
    g.add_edge(lower, upper, "riser", length=0.0)


# --- Measurement --------------------------------------------------------------------

class PathMetrics(BaseModel):
    length_mm: float
    bends: int
    transitions: int
    j: float


def _heading(a: Node, b: Node) -> float | None:
    if a[0] != b[0]:
        return None
    return math.degrees(math.atan2(b[2] - a[2], b[1] - a[1]))


def _turned(h1: float | None, h2: float | None) -> bool:
    if h1 is None or h2 is None:
        return False
    d = abs((h2 - h1 + 180) % 360 - 180)
    return d > BEND_TOLERANCE_DEG


def measure(path: list[Node], policy: Policy) -> PathMetrics:
    """Recompute L, B, T and J from coordinates alone (independent of the search)."""
    length, bends, transitions, heading = 0.0, 0, 0, None
    for a, b in zip(path, path[1:]):
        if a[0] != b[0]:
            transitions += 1
            heading = None
            continue
        length += math.dist(a[1:], b[1:])
        h = _heading(a, b)
        if _turned(heading, h):
            bends += 1
        heading = h
    j = length + policy.bend_penalty_mm * bends + policy.transition_penalty_mm * transitions
    return PathMetrics(length_mm=round(length), bends=bends, transitions=transitions, j=round(j))


# --- Search -----------------------------------------------------------------------------

Adjacency = dict[Node, list[tuple[Node, float, EdgeKind]]]


def shortest_length_tree(g: Graph, source: Node, adj: Adjacency | None = None) -> dict[Node, Node | None]:
    """Length-only Dijkstra with a stable tie-break on node keys (the baseline).

    `adj` overrides the graph's undirected adjacency, e.g. with a directed drainage graph.
    """
    adj = adj if adj is not None else g.adj
    dist: dict[Node, float] = {source: 0.0}
    prev: dict[Node, Node | None] = {source: None}
    heap = [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        for v, w, _ in sorted(adj.get(u, []), key=lambda e: e[0]):
            nd = d + w
            if nd < dist.get(v, math.inf) - 1e-9 or (abs(nd - dist.get(v, math.inf)) <= 1e-9 and u < (prev.get(v) or u)):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return prev


def distances_to(adj: Adjacency, target: Node) -> dict[Node, float]:
    """Shortest distance from every node to `target` over an undirected adjacency."""
    dist = {target: 0.0}
    heap = [(0.0, target)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        for v, w, _ in adj.get(u, []):
            if d + w < dist.get(v, math.inf):
                dist[v] = d + w
                heapq.heappush(heap, (d + w, v))
    return dist


def bend_aware_tree(g: Graph, source: Node, policy: Policy, adj: Adjacency | None = None) -> tuple[dict, dict]:
    """Dijkstra over (node, incoming heading) minimising J; non-negative costs only.

    Returns (predecessor, distance) maps keyed by (node, heading) states.
    """
    adj = adj if adj is not None else g.adj
    start = (source, None)
    dist = {start: 0.0}
    prev: dict = {start: None}
    heap = [(0.0, (source, -999.0), start)]
    while heap:
        d, _, state = heapq.heappop(heap)
        if d > dist.get(state, math.inf):
            continue
        u, h = state
        for v, w, kind in sorted(adj.get(u, []), key=lambda e: e[0]):
            if kind == "riser":
                nh, cost = None, policy.transition_penalty_mm
            else:
                nh = _heading(u, v)
                cost = w + (policy.bend_penalty_mm if _turned(h, nh) else 0)
            ns = (v, None if nh is None else round(nh, 1))
            nd = d + cost
            if nd < dist.get(ns, math.inf) - 1e-9:
                dist[ns] = nd
                prev[ns] = state
                heapq.heappush(heap, (nd, (v, -999.0 if ns[1] is None else ns[1]), ns))
    return prev, dist


def path_from_tree(prev: dict, target: Node) -> list[Node] | None:
    if target not in prev:
        return None
    out = [target]
    while prev[out[-1]] is not None:
        out.append(prev[out[-1]])
    return out[::-1]


def best_state_path(prev: dict, dist: dict, target: Node) -> list[Node] | None:
    states = [s for s in dist if s[0] == target]
    if not states:
        return None
    best = min(states, key=lambda s: (dist[s], -999 if s[1] is None else s[1]))
    out = [best]
    while prev[out[-1]] is not None:
        out.append(prev[out[-1]])
    return [s[0] for s in out[::-1]]


def validate_path(g: Graph, path: list[Node], inside: dict[int, Polygon]) -> list[str]:
    """Independent check: every step is an allowed edge and stays inside the building outline."""
    problems = []
    for a, b in zip(path, path[1:]):
        if not g.has_edge(a, b):
            problems.append(f"step {a}->{b} is not an allowed edge")
            continue
        if a[0] == b[0] and not inside[a[0]].contains(LineString([a[1:], b[1:]])):
            problems.append(f"step {a}->{b} leaves the building")
    return problems


def building_outline(g: Graph) -> dict[int, Polygon]:
    floors: dict[int, list[Polygon]] = {}
    for (f, _), poly in g.room_poly.items():
        floors.setdefault(f, []).append(poly)
    return {f: unary_union(polys).buffer(5) for f, polys in floors.items()}


def union_length_mm(paths: list[list[Node]]) -> float:
    seen = set()
    total = 0.0
    for p in paths:
        for a, b in zip(p, p[1:]):
            e = (a, b) if a <= b else (b, a)
            if e in seen or a[0] != b[0]:
                continue
            seen.add(e)
            total += math.dist(a[1:], b[1:])
    return round(total)
