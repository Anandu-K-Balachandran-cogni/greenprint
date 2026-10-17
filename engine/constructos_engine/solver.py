"""CP-SAT layout solver.

Rectilinear formulation on an integer grid:
- each space is a rectangle inside a shared building footprint W x H;
- spaces on a floor never overlap and exactly tile the footprint (optional
  terrace fillers absorb leftover area on upper floors);
- every passage in the programme becomes a shared wall at least as long as
  the door or opening it needs;
- habitable rooms touch an exterior edge; entrance and parking touch the front;
- vertical cores (stairs) occupy the same rectangle on every floor.

Preferences (target areas, front/rear zoning) are a weighted objective. The
solver status is reported as-is: a timeout without a solution is UNKNOWN, not
proof that the brief is impossible.
"""

import time
from typing import Literal

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from .feasibility import assess_program
from .grid import GRID_MM, area_to_grid as _g_area, to_grid as _g
from .program import Program, Setbacks, Space

TERRACE_HOSTS = {"hall", "family", "master_bedroom", "bedroom", "guest_room", "study"}
Status = Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
Side = Literal["left", "right", "front", "rear"]


class PlacedSpace(BaseModel):
    id: str
    type: str
    name: str
    floor: int
    x_mm: int
    y_mm: int
    w_mm: int
    h_mm: int
    area_m2: float
    exterior_sides: list[Side]
    filler: bool = False


class PlacedPassage(BaseModel):
    a: str
    b: str
    kind: str
    floor: int
    side: Side | None = None  # side of `a` on which `b` lies; None for exterior entrances
    overlap_mm: int
    extra: bool = False


class Allocation(BaseModel):
    schema_version: str = "constructos.allocation.v1"
    status: Status
    alternative: int
    seed: int
    solve_seconds: float
    objective: float | None = None
    grid_mm: int = GRID_MM
    plot: dict
    setbacks: Setbacks
    footprint: dict | None = None  # x_mm, y_mm (offset in plot), width_mm, depth_mm
    spaces: list[PlacedSpace] = Field(default_factory=list)
    passages: list[PlacedPassage] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    message: str | None = None


class _Rect:
    def __init__(self, m: cp_model.CpModel, s: Space, W: cp_model.IntVar, H: cp_model.IntVar,
                 max_w: int, max_h: int, present: cp_model.IntVar | None = None):
        self.space = s
        self.present = present
        optional = present is not None
        lo_w, lo_h = _g(s.min_w_mm), _g(s.min_h_mm)
        self.x = m.NewIntVar(0, max_w, f"x_{s.id}")
        self.y = m.NewIntVar(0, max_h, f"y_{s.id}")
        self.w = m.NewIntVar(0 if optional else lo_w, max_w, f"w_{s.id}")
        self.h = m.NewIntVar(0 if optional else lo_h, max_h, f"h_{s.id}")
        self.xe = m.NewIntVar(0, max_w, f"xe_{s.id}")
        self.ye = m.NewIntVar(0, max_h, f"ye_{s.id}")
        if not optional:
            self.ix = m.NewIntervalVar(self.x, self.w, self.xe, f"ix_{s.id}")
            self.iy = m.NewIntervalVar(self.y, self.h, self.ye, f"iy_{s.id}")
        else:
            self.ix = m.NewOptionalIntervalVar(self.x, self.w, self.xe, present, f"ix_{s.id}")
            self.iy = m.NewOptionalIntervalVar(self.y, self.h, self.ye, present, f"iy_{s.id}")
            m.Add(self.w >= lo_w).OnlyEnforceIf(present)
            m.Add(self.h >= lo_h).OnlyEnforceIf(present)
            m.Add(self.w == 0).OnlyEnforceIf(present.Not())
            m.Add(self.h == 0).OnlyEnforceIf(present.Not())
        m.Add(self.xe <= W)
        m.Add(self.ye <= H)
        max_area = max_w * max_h
        self.area = m.NewIntVar(0, max_area, f"a_{s.id}")
        m.AddMultiplicationEquality(self.area, [self.w, self.h])
        if not optional:
            m.Add(self.area >= _g_area(s.min_area_m2))
            m.Add(self.area <= _g_area(s.max_area_m2))
        if s.max_aspect:
            r = int(s.max_aspect * 10)
            m.Add(10 * self.w <= r * self.h)
            m.Add(10 * self.h <= r * self.w)
        # exterior contact literals
        self.touch: dict[Side, cp_model.IntVar] = {}
        for side, expr in (("left", self.x == 0), ("right", self.xe == W),
                           ("front", self.y == 0), ("rear", self.ye == H)):
            t = m.NewBoolVar(f"t_{side}_{s.id}")
            m.Add(expr).OnlyEnforceIf(t)
            if optional:
                m.AddImplication(t, present)
            self.touch[side] = t


def _share_edge(m: cp_model.CpModel, a: _Rect, b: _Rect, d: int, name: str,
                enforce: cp_model.IntVar | None = None) -> dict[Side, cp_model.IntVar]:
    """One of four contacts with at least `d` units of common wall (only when `enforce` holds, if given)."""
    sides: dict[Side, cp_model.IntVar] = {s: m.NewBoolVar(f"{name}_{s}") for s in ("right", "left", "rear", "front")}
    if enforce is None:
        m.AddExactlyOne(sides.values())
    else:
        m.Add(sum(sides.values()) == enforce)
    for side, lit in sides.items():
        if side == "right":
            m.Add(a.xe == b.x).OnlyEnforceIf(lit)
        elif side == "left":
            m.Add(b.xe == a.x).OnlyEnforceIf(lit)
        elif side == "rear":
            m.Add(a.ye == b.y).OnlyEnforceIf(lit)
        else:
            m.Add(b.ye == a.y).OnlyEnforceIf(lit)
        if side in ("right", "left"):
            m.Add(a.y + d <= b.ye).OnlyEnforceIf(lit)
            m.Add(b.y + d <= a.ye).OnlyEnforceIf(lit)
        else:
            m.Add(a.x + d <= b.xe).OnlyEnforceIf(lit)
            m.Add(b.x + d <= a.xe).OnlyEnforceIf(lit)
    return sides


def _shared_mm(a: PlacedSpace, b: PlacedSpace) -> int:
    if a.x_mm + a.w_mm == b.x_mm or b.x_mm + b.w_mm == a.x_mm:
        return min(a.y_mm + a.h_mm, b.y_mm + b.h_mm) - max(a.y_mm, b.y_mm)
    return min(a.x_mm + a.w_mm, b.x_mm + b.w_mm) - max(a.x_mm, b.x_mm)


def _proven_conflicts(program: Program, plot_w_mm: int, plot_d_mm: int) -> list[str]:
    """Conflicts provable before search: exactly the feasibility assessment's failed checks."""
    report = assess_program(program, plot_w_mm, plot_d_mm)
    out = [c.message for c in report.checks if c.status == "fail"]
    if out:
        out.append("Reduce the programme, enlarge the plot or relax the setbacks.")
    return out


def solve(program: Program, plot_w_mm: int, plot_d_mm: int, *, setbacks: Setbacks | None = None,
          seed: int = 0, alternative: int = 0, avoid: list[Allocation] | None = None,
          near: Allocation | None = None, time_limit_s: float = 20.0, workers: int = 8) -> Allocation:
    """Solve a layout. `near` (a previous layout) starts the search there and penalises needless moves."""
    setbacks = setbacks or program.setbacks
    max_w = (plot_w_mm - 2 * setbacks.side_mm) // GRID_MM
    max_h = (plot_d_mm - setbacks.front_mm - setbacks.rear_mm) // GRID_MM
    base = dict(alternative=alternative, seed=seed, plot={"width_mm": plot_w_mm, "depth_mm": plot_d_mm},
                setbacks=setbacks, assumptions=list(program.assumptions) + [
                    f"Setbacks assumed: front {setbacks.front_mm / 1000:g} m, rear {setbacks.rear_mm / 1000:g} m, "
                    f"sides {setbacks.side_mm / 1000:g} m (replace with local bylaws).",
                    f"Layout grid {GRID_MM} mm; walls are centred on grid lines.",
                ])
    if max_w <= 0 or max_h <= 0:
        return Allocation(status="MODEL_INVALID", solve_seconds=0, message="Plot is smaller than its setbacks.", **base)
    conflicts = _proven_conflicts(program.model_copy(update={"setbacks": setbacks}), plot_w_mm, plot_d_mm)
    if conflicts:
        return Allocation(status="INFEASIBLE", solve_seconds=0, message=" ".join(conflicts), **base)

    m = cp_model.CpModel()
    W = m.NewIntVar(1, max_w, "W")
    H = m.NewIntVar(1, max_h, "H")
    WH = m.NewIntVar(1, max_w * max_h, "WH")
    m.AddMultiplicationEquality(WH, [W, H])

    rects: dict[tuple[int, str], _Rect] = {}
    fillers: list[_Rect] = []
    terrace_access: list[tuple[_Rect, list[tuple[_Rect, cp_model.IntVar]]]] = []
    for s in program.spaces:
        rects[(s.floor, s.id)] = _Rect(m, s, W, H, max_w, max_h)

    for f in range(program.storeys):
        floor_rects = [r for (fl, _), r in rects.items() if fl == f]
        if f > 0:  # open terraces take whatever the upper floor does not need; each needs a door
            hosts = [r for r in floor_rects if r.space.type in TERRACE_HOSTS]
            for k in range(2):
                t = Space(id=f"terrace-{f}-{k + 1}", type="terrace", name="Open terrace", floor=f,
                          min_w_mm=1500, min_h_mm=1500, min_area_m2=2, target_area_m2=0, max_area_m2=200,
                          zone="front", exterior="required", max_aspect=None, source="assumption")
                present = m.NewBoolVar(f"p_{t.id}")
                r = _Rect(m, t, W, H, max_w, max_h, present)
                m.AddBoolOr(list(r.touch.values())).OnlyEnforceIf(present)
                access = []
                for h in hosts:
                    lit = m.NewBoolVar(f"acc_{t.id}_{h.space.id}")
                    m.AddImplication(lit, present)
                    _share_edge(m, h, r, _g(1200), f"acc_{t.id}_{h.space.id}", enforce=lit)
                    access.append((h, lit))
                m.Add(sum(lit for _, lit in access) == present)
                terrace_access.append((r, access))
                fillers.append(r)
                floor_rects.append(r)
        m.AddNoOverlap2D([r.ix for r in floor_rects], [r.iy for r in floor_rects])
        m.Add(sum(r.area for r in floor_rects) == WH)

    # Vertical cores line up across floors.
    cores: dict[str, list[_Rect]] = {}
    for r in rects.values():
        if r.space.vertical_core:
            cores.setdefault(r.space.type, []).append(r)
    for group in cores.values():
        for other in group[1:]:
            for attr in ("x", "y", "w", "h"):
                m.Add(getattr(other, attr) == getattr(group[0], attr))

    objective_terms: list = []
    # Each passage: (programme passage, [(parent id, side literals, choice literal or None)]).
    passage_choices: list[tuple] = []
    for i, p in enumerate(program.passages):
        b = rects[(p.floor, p.b)]
        d = _g(p.min_overlap_mm)
        if p.a == "exterior":
            m.Add(b.y == 0)
            m.Add(b.w >= d)
            passage_choices.append((p, None))
            continue
        parents = [c for c in [p.a, *p.options] if (p.floor, c) in rects]
        if len(parents) == 1:
            passage_choices.append((p, [(parents[0], _share_edge(m, rects[(p.floor, parents[0])], b, d, f"p{i}"), None)]))
            continue
        options = []
        for j, parent in enumerate(parents):
            lit = m.NewBoolVar(f"via_{i}_{j}")
            options.append((parent, _share_edge(m, rects[(p.floor, parent)], b, d, f"p{i}_{j}", enforce=lit), lit))
            if j:
                objective_terms.append(6 * j * lit)  # prefer earlier parents
        m.AddExactlyOne(lit for _, _, lit in options)
        passage_choices.append((p, options))

    for (f, sid), r in rects.items():
        s = r.space
        if s.front:
            m.Add(r.y == 0)
        if s.exterior == "required":
            m.AddBoolOr(list(r.touch.values()))
        elif s.exterior == "preferred":
            none = m.NewBoolVar(f"noext_{sid}_{f}")
            m.AddBoolOr(list(r.touch.values()) + [none])
            objective_terms.append(20 * none)
        target = _g_area(s.target_area_m2)
        dev = m.NewIntVar(0, max_w * max_h, f"dev_{sid}_{f}")
        m.Add(dev >= r.area - target)
        m.Add(dev >= target - r.area)
        weight = 1 if s.type not in {"hall", "corridor", "stair"} else 3
        objective_terms.append(weight * dev)
        if s.zone == "front":
            objective_terms.append(r.y)
        elif s.zone == "rear":
            gap = m.NewIntVar(0, max_h, f"rear_{sid}_{f}")
            m.Add(gap == H - r.ye)
            objective_terms.append(gap)
    for t in fillers:  # prefer few terraces, towards the front (over the parking)
        objective_terms.append(4 * t.present)
        objective_terms.append(t.y)

    # Alternatives must differ: at least two spaces move by >= 3 grid units.
    for prev in avoid or []:
        moved = []
        for ps in prev.spaces:
            r = rects.get((ps.floor, ps.id))
            if r is None:
                continue
            px = (ps.x_mm - prev.footprint["x_mm"]) // GRID_MM
            py = (ps.y_mm - prev.footprint["y_mm"]) // GRID_MM
            far = m.NewBoolVar(f"far_{ps.id}_{len(moved)}")
            dx = m.NewIntVar(0, max_w, "")
            dy = m.NewIntVar(0, max_h, "")
            m.AddAbsEquality(dx, r.x - px)
            m.AddAbsEquality(dy, r.y - py)
            m.Add(dx + dy >= 3).OnlyEnforceIf(far)
            moved.append(far)
        m.Add(sum(moved) >= 2)

    if near is not None and near.footprint:
        ox, oy = setbacks.side_mm, setbacks.front_mm
        m.AddHint(W, near.footprint["width_mm"] // GRID_MM)
        m.AddHint(H, near.footprint["depth_mm"] // GRID_MM)
        for ps in near.spaces:
            r = rects.get((ps.floor, ps.id))
            if r is None:
                continue
            for var, val in ((r.x, (ps.x_mm - ox) // GRID_MM), (r.y, (ps.y_mm - oy) // GRID_MM),
                             (r.w, ps.w_mm // GRID_MM), (r.h, ps.h_mm // GRID_MM)):
                m.AddHint(var, val)
                moved = m.NewIntVar(0, max(max_w, max_h), "")
                m.AddAbsEquality(moved, var - val)
                objective_terms.append(2 * moved)

    m.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    started = time.perf_counter()
    code = solver.Solve(m)
    elapsed = round(time.perf_counter() - started, 2)
    status: Status = solver.StatusName(code)  # type: ignore[assignment]
    if code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        msg = {
            "INFEASIBLE": "No layout satisfies every hard requirement within this plot and formulation.",
            "UNKNOWN": "The solver found no layout within the time limit; this is not proof that none exists.",
        }.get(status, "The layout model was invalid.")
        return Allocation(status=status, solve_seconds=elapsed, message=msg, **base)

    ox, oy = setbacks.side_mm, setbacks.front_mm
    Wv, Hv = solver.Value(W), solver.Value(H)
    placed: list[PlacedSpace] = []
    for r in list(rects.values()) + fillers:
        if r.present is not None and not solver.Value(r.present):
            continue
        # touch literals only imply contact, so recompute exterior sides exactly
        x, y, w, h = (solver.Value(v) for v in (r.x, r.y, r.w, r.h))
        sides = [s for s, ok in (("left", x == 0), ("right", x + w == Wv), ("front", y == 0), ("rear", y + h == Hv)) if ok]
        placed.append(PlacedSpace(
            id=r.space.id, type=r.space.type, name=r.space.name, floor=r.space.floor,
            x_mm=ox + x * GRID_MM, y_mm=oy + y * GRID_MM, w_mm=w * GRID_MM, h_mm=h * GRID_MM,
            area_m2=round(w * h * GRID_MM * GRID_MM / 1e6, 2), exterior_sides=sides, filler=r.present is not None,
        ))
    by_key = {(s.floor, s.id): s for s in placed}
    passages = []
    for p, options in passage_choices:
        b = by_key[(p.floor, p.b)]
        if options is None:
            passages.append(PlacedPassage(a=p.a, b=p.b, kind=p.kind, floor=p.floor, side=None, overlap_mm=b.w_mm))
            continue
        parent, sides, _ = next(o for o in options if o[2] is None or solver.Value(o[2]))
        side = next(s for s, lit in sides.items() if solver.Value(lit))
        a = by_key[(p.floor, parent)]
        if side in ("left", "right"):
            overlap = min(a.y_mm + a.h_mm, b.y_mm + b.h_mm) - max(a.y_mm, b.y_mm)
        else:
            overlap = min(a.x_mm + a.w_mm, b.x_mm + b.w_mm) - max(a.x_mm, b.x_mm)
        passages.append(PlacedPassage(a=parent, b=p.b, kind=p.kind, floor=p.floor, side=side, overlap_mm=overlap, extra=p.extra))

    for r, access in terrace_access:
        if not solver.Value(r.present):
            continue
        host_rect = next(h for h, lit in access if solver.Value(lit))
        a, t = by_key[(host_rect.space.floor, host_rect.space.id)], by_key[(r.space.floor, r.space.id)]
        passages.append(PlacedPassage(a=a.id, b=t.id, kind="door", floor=t.floor, side=None,
                                      overlap_mm=_shared_mm(a, t)))

    return Allocation(
        status=status, solve_seconds=elapsed, objective=solver.ObjectiveValue(),
        footprint={"x_mm": ox, "y_mm": oy, "width_mm": Wv * GRID_MM, "depth_mm": Hv * GRID_MM},
        spaces=sorted(placed, key=lambda s: (s.floor, s.y_mm, s.x_mm)), passages=passages, **base,
    )
