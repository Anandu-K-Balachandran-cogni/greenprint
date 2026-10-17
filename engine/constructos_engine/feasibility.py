"""Explain, before any layout is solved, whether a brief can fit its plot.

The assessment works on the same programme and grid the solver uses. Checks
marked "fail" are proofs (the solver will report INFEASIBLE for the same
reason); "warn" and "info" are reasoned estimates. Only the solver can prove
that a layout exists.
"""

from typing import Literal

from pydantic import BaseModel

from .brief import Brief
from .grid import GRID_MM, area_to_grid, buildable_grid, grid_area_m2, to_grid
from .program import Program, Setbacks, Space, build_program

Status = Literal["pass", "warn", "fail", "info"]
Verdict = Literal["fits", "tight", "does_not_fit", "incomplete"]

TIGHT_LOAD_PCT = 85


class FeasibilityCheck(BaseModel):
    code: str
    status: Status
    title: str
    message: str
    measured: float | None = None
    limit: float | None = None
    unit: str | None = None


class SpaceBudget(BaseModel):
    id: str
    name: str
    type: str
    floor: int
    min_m2: float
    target_m2: float
    min_dims_m: str
    source: Literal["your size", "standard", "compact minimum", "typical", "added"]


class FloorBudget(BaseModel):
    floor: int
    name: str
    min_m2: float
    target_m2: float
    buildable_m2: float
    min_load_pct: float
    target_load_pct: float
    spaces: list[SpaceBudget]


class Feasibility(BaseModel):
    verdict: Verdict
    headline: str
    plot: dict | None = None
    setbacks: Setbacks | None = None
    buildable: dict | None = None
    compact: bool = False
    floors: list[FloorBudget] = []
    checks: list[FeasibilityCheck] = []
    assumptions: list[str] = []
    note: str = ("Estimated from areas and dimensions on the solver's 300 mm grid. Failed checks are proofs; "
                 "passing them does not guarantee a layout — generating one does.")


def floor_name(f: int) -> str:
    return "Ground floor" if f == 0 else "First floor" if f == 1 else f"Floor {f}"


def _source(s: Space, compact: bool) -> str:
    if s.user_min:
        return "your size"
    if s.type in ("garage", "stair"):
        return "standard"
    if s.source != "brief":
        return "added"
    return "compact minimum" if compact else "typical"


def assess_program(program: Program, plot_w_mm: int, plot_d_mm: int) -> Feasibility:
    sb = program.setbacks
    max_w, max_h = buildable_grid(plot_w_mm, plot_d_mm, sb.front_mm, sb.rear_mm, sb.side_mm)
    plot = {"width_m": plot_w_mm / 1000, "depth_m": plot_d_mm / 1000, "area_m2": round(plot_w_mm * plot_d_mm / 1e6, 1)}
    if max_w <= 0 or max_h <= 0:
        return Feasibility(verdict="does_not_fit", headline="The plot is smaller than its setbacks.", plot=plot,
                           setbacks=sb, checks=[FeasibilityCheck(code="SETBACKS", status="fail", title="Buildable area",
                                                                 message="Nothing can be built inside the assumed setbacks.")])
    bw, bd = max_w * GRID_MM / 1000, max_h * GRID_MM / 1000
    b_m2 = grid_area_m2(max_w * max_h)
    buildable = {"width_m": bw, "depth_m": bd, "area_m2": round(b_m2, 1)}
    dims = f"{bw:.1f} × {bd:.1f} m"
    checks: list[FeasibilityCheck] = []

    checks.append(FeasibilityCheck(
        code="BUILDABLE", status="info", title="Buildable area",
        message=f"{plot['area_m2']:.0f} m² plot minus setbacks (front {sb.front_mm / 1000:g} m, rear {sb.rear_mm / 1000:g} m, "
                f"sides {sb.side_mm / 1000:g} m) leaves {dims} = {b_m2:.0f} m² per floor.",
        measured=round(b_m2, 1), unit="m²"))

    # Every space must fit the buildable rectangle in its fixed orientation (proof).
    too_big = [s for s in program.spaces if to_grid(s.min_w_mm) > max_w or to_grid(s.min_h_mm) > max_h]
    for s in too_big:
        checks.append(FeasibilityCheck(
            code="SPACE_DIMENSIONS", status="fail", title=f"{s.name} dimensions",
            message=f"{s.name} needs at least {s.min_w_mm / 1000:g} × {s.min_h_mm / 1000:g} m, but the buildable area is only {dims}."))
    if not too_big:
        widest = max(program.spaces, key=lambda s: s.min_w_mm)
        deepest = max(program.spaces, key=lambda s: s.min_h_mm)
        checks.append(FeasibilityCheck(
            code="SPACE_DIMENSIONS", status="pass", title="Room dimensions",
            message=f"Every space fits the buildable area; the widest is {widest.name} ({widest.min_w_mm / 1000:g} m of {bw:.1f} m) "
                    f"and the deepest {deepest.name} ({deepest.min_h_mm / 1000:g} m of {bd:.1f} m)."))

    floors: list[FloorBudget] = []
    for f in range(program.storeys):
        spaces = [s for s in program.spaces if s.floor == f]
        need = grid_area_m2(sum(area_to_grid(s.min_area_m2) for s in spaces))
        target = grid_area_m2(sum(area_to_grid(s.target_area_m2) for s in spaces))
        load, tload = 100 * need / b_m2, 100 * target / b_m2
        floors.append(FloorBudget(
            floor=f, name=floor_name(f), min_m2=round(need, 1), target_m2=round(target, 1), buildable_m2=round(b_m2, 1),
            min_load_pct=round(load, 1), target_load_pct=round(tload, 1),
            spaces=[SpaceBudget(id=s.id, name=s.name, type=s.type, floor=f, min_m2=s.min_area_m2, target_m2=s.target_area_m2,
                                min_dims_m=f"{s.min_w_mm / 1000:g} × {s.min_h_mm / 1000:g}", source=_source(s, program.compact))
                    for s in sorted(spaces, key=lambda s: -s.min_area_m2)]))
        title = f"{floor_name(f)} area"
        if need > b_m2:
            largest = ", ".join(f"{s.name} {s.min_area_m2:g} m²{' (your minimum)' if s.user_min else ''}"
                                for s in sorted(spaces, key=lambda s: -s.min_area_m2)[:4])
            checks.append(FeasibilityCheck(
                code="FLOOR_AREA", status="fail", title=title, measured=round(need, 1), limit=round(b_m2, 1), unit="m²",
                message=f"The {floor_name(f).lower()} needs at least {need:.0f} m² (largest: {largest}), "
                        f"but the buildable area is {dims} ({b_m2:.0f} m²)."))
        elif load > TIGHT_LOAD_PCT:
            checks.append(FeasibilityCheck(
                code="FLOOR_AREA", status="warn", title=title, measured=round(need, 1), limit=round(b_m2, 1), unit="m²",
                message=f"Minimum rooms take {load:.0f}% of the {b_m2:.0f} m² available; rooms will be at their minimum sizes "
                        f"with little slack for shape and circulation."))
        else:
            sizing = ("so rooms can reach their typical sizes" if tload <= 100 else
                      f"but typical sizes would need {target:.0f} m², so some rooms will be smaller than typical")
            checks.append(FeasibilityCheck(
                code="FLOOR_AREA", status="pass", title=title, measured=round(need, 1), limit=round(b_m2, 1), unit="m²",
                message=f"Minimum rooms need {need:.0f} m² of {b_m2:.0f} m² ({load:.0f}%), {sizing}."))
        if f > 0 and floors[0].min_m2 > need:
            checks.append(FeasibilityCheck(
                code="UPPER_FLOOR", status="info", title=f"{floor_name(f)} terraces",
                message=f"The {floor_name(f).lower()} needs less than the footprint below it; the remainder becomes open terrace "
                        f"with a door from the upper hall or a bedroom."))

        # Rooms that must face the road share one edge of the footprint (proof).
        front = [s for s in spaces if s.front]
        if front:
            entrance = {p.b: p.min_overlap_mm for p in program.passages if p.a == "exterior" and p.floor == f}
            width = sum(max(to_grid(s.min_w_mm), to_grid(entrance.get(s.id, 0))) for s in front)
            names = " + ".join(f"{s.name} {max(s.min_w_mm, entrance.get(s.id, 0)) / 1000:g} m" for s in front)
            ok = width <= max_w
            checks.append(FeasibilityCheck(
                code="FRONTAGE", status="pass" if ok else "fail", title="Road frontage",
                measured=width * GRID_MM / 1000, limit=bw, unit="m",
                message=(f"{names} = {width * GRID_MM / 1000:g} m must all face the road; "
                         f"{'the' if ok else 'but the'} buildable width is {bw:.1f} m.")))

        # Rooms needing windows compete for the footprint perimeter (estimate).
        needs_wall = [s for s in spaces if s.exterior == "required"]
        if needs_wall:
            wall = sum(to_grid(min(s.min_w_mm, s.min_h_mm)) for s in needs_wall) * GRID_MM / 1000
            perimeter = 2 * (bw + bd)
            pct = 100 * wall / perimeter
            checks.append(FeasibilityCheck(
                code="EXTERIOR_WALLS", status="warn" if pct > 90 else "pass", title=f"{floor_name(f)} outside walls",
                measured=round(wall, 1), limit=round(perimeter, 1), unit="m",
                message=f"{len(needs_wall)} rooms need an outside wall for windows, using at least {wall:.1f} m of the "
                        f"{perimeter:.1f} m perimeter ({pct:.0f}%)."))

    if program.storeys > 1:
        stair = next((s for s in program.spaces if s.type == "stair"), None)
        if stair:
            checks.append(FeasibilityCheck(
                code="STAIR", status="info", title="Stair core",
                message=f"A {stair.min_w_mm / 1000:g} × {stair.min_h_mm / 1000:g} m stair core sits in the same place on every floor "
                        f"and counts towards each floor's area."))

    if any(c.status == "fail" for c in checks):
        verdict: Verdict = "does_not_fit"
        headline = "This brief cannot fit the plot: " + next(c.message for c in checks if c.status == "fail")
    elif any(c.status == "warn" for c in checks):
        verdict = "tight"
        headline = "The brief fits, but tightly: " + next(c.message for c in checks if c.status == "warn")
    else:
        verdict = "fits"
        worst = max(floors, key=lambda x: x.min_load_pct)
        headline = (f"The brief fits: the fullest floor ({worst.name.lower()}) needs {worst.min_load_pct:.0f}% of the "
                    f"buildable area at minimum sizes.")
    return Feasibility(verdict=verdict, headline=headline, plot=plot, setbacks=sb, buildable=buildable,
                       compact=program.compact, floors=floors, checks=checks, assumptions=program.assumptions)


def assess(brief: Brief) -> Feasibility:
    if not (brief.plot.width_mm and brief.plot.depth_mm and brief.storeys and brief.rooms):
        missing = [n for n, ok in (("plot size", brief.plot.width_mm), ("number of floors", brief.storeys),
                                   ("rooms", brief.rooms)) if not ok]
        return Feasibility(verdict="incomplete", headline=f"Add the {', '.join(missing)} to assess feasibility.")
    try:
        program = build_program(brief)
    except ValueError as e:
        return Feasibility(verdict="incomplete", headline=f"The room programme is incomplete: {e}")
    return assess_program(program, brief.plot.width_mm, brief.plot.depth_mm)
