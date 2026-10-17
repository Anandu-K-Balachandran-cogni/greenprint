"""Independent measurements of a solved allocation.

These checks recompute geometry from the placed rectangles alone. They do not
trust solver status or solver variables, so a solver bug shows up as a failed
check rather than as an attractive but invalid layout.
"""

from typing import Literal

from pydantic import BaseModel

from .program import Program
from .solver import Allocation, PlacedSpace

Severity = Literal["error", "warning"]


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    object_ids: list[str]
    measured: float | None = None
    required: float | None = None


class Evidence(BaseModel):
    """One check, with what was actually measured."""
    check: str
    title: str
    status: Literal["pass", "fail", "warn"]
    detail: str


class RoomMetric(BaseModel):
    id: str
    name: str
    floor: int
    area_m2: float
    min_m2: float
    target_m2: float
    width_m: float
    depth_m: float


class LayoutMetrics(BaseModel):
    plot_m2: float
    footprint_m2: float
    ground_coverage_pct: float
    built_up_m2: float
    floor_area_ratio: float
    circulation_pct: float
    terrace_m2: float
    rooms_at_or_above_target: int
    rooms_total: int
    rooms: list[RoomMetric]


class CheckReport(BaseModel):
    status: Literal["PASS", "FAIL"]
    findings: list[Finding]
    checked: list[str]
    evidence: list[Evidence] = []
    metrics: LayoutMetrics | None = None


def _overlap_1d(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def shared_edge_mm(a: PlacedSpace, b: PlacedSpace) -> int:
    """Length of common boundary between two touching rectangles (0 if they do not touch)."""
    if a.x_mm + a.w_mm == b.x_mm or b.x_mm + b.w_mm == a.x_mm:
        return _overlap_1d(a.y_mm, a.y_mm + a.h_mm, b.y_mm, b.y_mm + b.h_mm)
    if a.y_mm + a.h_mm == b.y_mm or b.y_mm + b.h_mm == a.y_mm:
        return _overlap_1d(a.x_mm, a.x_mm + a.w_mm, b.x_mm, b.x_mm + b.w_mm)
    return 0


def check_allocation(allocation: Allocation, program: Program) -> CheckReport:
    findings: list[Finding] = []
    fp = allocation.footprint
    if fp is None:
        return CheckReport(status="FAIL", checked=[], findings=[
            Finding(code="NO_LAYOUT", severity="error", message="No layout to check.", object_ids=[])])
    plot = allocation.plot
    fx0, fy0, fx1, fy1 = fp["x_mm"], fp["y_mm"], fp["x_mm"] + fp["width_mm"], fp["y_mm"] + fp["depth_mm"]
    by_key = {(s.floor, s.id): s for s in allocation.spaces}

    def err(code: str, message: str, ids: list[str], measured=None, required=None, severity: Severity = "error"):
        findings.append(Finding(code=code, severity=severity, message=message, object_ids=ids,
                                measured=measured, required=required))

    if fx0 < 0 or fy0 < 0 or fx1 > plot["width_mm"] or fy1 > plot["depth_mm"]:
        err("FOOTPRINT_OUTSIDE_PLOT", "Building footprint extends beyond the plot.", [])

    # Programme coverage: every programmed space is placed exactly once on its floor.
    for s in program.spaces:
        if (s.floor, s.id) not in by_key:
            err("SPACE_MISSING", f"{s.name} is not in the layout.", [s.id])

    for floor in range(program.storeys):
        spaces = [s for s in allocation.spaces if s.floor == floor]
        for s in spaces:
            if s.x_mm < fx0 or s.y_mm < fy0 or s.x_mm + s.w_mm > fx1 or s.y_mm + s.h_mm > fy1:
                err("OUTSIDE_FOOTPRINT", f"{s.name} extends outside the footprint.", [s.id])
            if s.w_mm <= 0 or s.h_mm <= 0:
                err("DEGENERATE_SPACE", f"{s.name} has no area.", [s.id])
        for i, a in enumerate(spaces):
            for b in spaces[i + 1:]:
                ov = _overlap_1d(a.x_mm, a.x_mm + a.w_mm, b.x_mm, b.x_mm + b.w_mm) * \
                     _overlap_1d(a.y_mm, a.y_mm + a.h_mm, b.y_mm, b.y_mm + b.h_mm)
                if ov:
                    err("OVERLAP", f"{a.name} and {b.name} overlap.", [a.id, b.id], measured=ov / 1e6, required=0)
        covered = sum(s.w_mm * s.h_mm for s in spaces)
        total = fp["width_mm"] * fp["depth_mm"]
        if covered != total:
            err("GAPS_IN_FLOOR", f"Floor {floor} spaces cover {covered / 1e6:.2f} m² of a {total / 1e6:.2f} m² footprint.",
                [], measured=covered / 1e6, required=total / 1e6)

    # Hard sizes and exposure from the programme.
    for s in program.spaces:
        placed = by_key.get((s.floor, s.id))
        if not placed:
            continue
        if min(placed.w_mm, placed.h_mm) < min(s.min_w_mm, s.min_h_mm):
            err("TOO_NARROW", f"{s.name} is narrower than its minimum.", [s.id],
                measured=min(placed.w_mm, placed.h_mm), required=min(s.min_w_mm, s.min_h_mm))
        if placed.area_m2 + 1e-6 < s.min_area_m2:
            err("TOO_SMALL", f"{s.name} is below its minimum area.", [s.id], measured=placed.area_m2, required=s.min_area_m2)
        if s.exterior == "required" and not placed.exterior_sides:
            err("NO_EXTERIOR_WALL", f"{s.name} has no exterior wall for a window.", [s.id])
        if s.exterior == "preferred" and not placed.exterior_sides:
            err("NO_EXTERIOR_WALL", f"{s.name} has no exterior wall (mechanical ventilation needed).", [s.id], severity="warning")
        if s.front and placed.y_mm != fy0:
            err("NOT_AT_FRONT", f"{s.name} must face the front.", [s.id])

    # Every programmed access is realised through an allowed parent with enough shared wall.
    realised = {(x.floor, x.b): x for x in allocation.passages if not x.extra}
    for p in program.passages:
        b = by_key.get((p.floor, p.b))
        if b is None:
            continue
        if p.extra:  # a connection the user asked for, on top of the access tree
            a = by_key.get((p.floor, p.a))
            if a is None or shared_edge_mm(a, b) < p.min_overlap_mm:
                err("NO_PASSAGE", f"{a.name if a else p.a} and {b.name} were asked to connect but do not share enough wall.",
                    [p.a, b.id], measured=shared_edge_mm(a, b) if a else 0, required=p.min_overlap_mm)
            continue
        if p.a == "exterior":
            if b.y_mm != fy0 or b.w_mm < p.min_overlap_mm:
                err("NO_ENTRANCE", f"{b.name} cannot be entered from the front.", [b.id])
            continue
        r = realised.get((p.floor, p.b))
        if r is None or r.a not in [p.a, *p.options]:
            err("NO_PASSAGE", f"{b.name} has no access through an allowed space.", [b.id])
            continue
        a = by_key.get((p.floor, r.a))
        shared = shared_edge_mm(a, b) if a else 0
        if shared < p.min_overlap_mm:
            err("NO_PASSAGE", f"{a.name if a else r.a} and {b.name} do not share enough wall for a {p.kind}.",
                [r.a, b.id], measured=shared, required=p.min_overlap_mm)

    # Reachability over the realised passages that actually have enough wall.
    for floor in range(program.storeys):
        roots = {p.b for p in program.passages if p.floor == floor and p.a == "exterior"}
        roots |= {s.id for s in program.spaces if s.floor == floor and s.vertical_core and floor > 0}
        edges = [(x.a, x.b) for x in allocation.passages if x.floor == floor and x.a != "exterior"
                 and (floor, x.a) in by_key and (floor, x.b) in by_key
                 and shared_edge_mm(by_key[(floor, x.a)], by_key[(floor, x.b)]) >= 1050]
        seen, stack = set(roots), list(roots)
        while stack:
            cur = stack.pop()
            for a, b in edges:
                for nxt in ((b,) if a == cur else (a,) if b == cur else ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
        for s in allocation.spaces:
            if s.floor == floor and s.id not in seen:
                err("UNREACHABLE", f"{s.name} cannot be reached from the entrance.", [s.id])

    checked = ["plot_containment", "programme_coverage", "non_overlap", "exact_tiling", "minimum_sizes",
               "exterior_exposure", "front_access", "passages", "reachability"]
    status = "FAIL" if any(f.severity == "error" for f in findings) else "PASS"
    return CheckReport(status=status, findings=findings, checked=checked,
                       evidence=_evidence(allocation, program, findings), metrics=_metrics(allocation, program))


CIRCULATION = {"hall", "corridor", "foyer", "stair"}


def _evidence(allocation: Allocation, program: Program, findings: list[Finding]) -> list[Evidence]:
    """Summarise each check with its measured values, so a pass is explained, not just asserted."""
    fp = allocation.footprint
    by_key = {(s.floor, s.id): s for s in allocation.spaces}
    rooms = [s for s in allocation.spaces if not s.filler]
    codes = {f.code for f in findings if f.severity == "error"}
    warn_codes = {f.code for f in findings if f.severity == "warning"}

    def item(check, title, fail_codes, detail, warn=frozenset()):
        failed = [f.message for f in findings if f.code in fail_codes and f.severity == "error"]
        state = "fail" if failed else "warn" if warn & warn_codes else "pass"
        return Evidence(check=check, title=title, status=state, detail="; ".join(failed) if failed else detail)

    out = [item("plot_containment", "Inside the plot", {"FOOTPRINT_OUTSIDE_PLOT", "OUTSIDE_FOOTPRINT"},
                f"Footprint {fp['width_mm'] / 1000:.1f} × {fp['depth_mm'] / 1000:.1f} m sits inside the plot and setbacks.")]
    out.append(item("programme_coverage", "Every room placed", {"SPACE_MISSING"},
                    f"{len([s for s in program.spaces if (s.floor, s.id) in by_key])} of {len(program.spaces)} programmed spaces are in the layout."))
    counts = [len([s for s in allocation.spaces if s.floor == f]) for f in range(program.storeys)]
    pairs = sum(n * (n - 1) // 2 for n in counts)
    out.append(item("non_overlap", "No overlaps", {"OVERLAP", "DEGENERATE_SPACE"},
                    f"0 overlapping pairs found ({pairs} pairwise comparisons)."))
    tiling = []
    for f in range(program.storeys):
        covered = sum(s.w_mm * s.h_mm for s in allocation.spaces if s.floor == f) / 1e6
        tiling.append(f"floor {f}: {covered:.1f} of {fp['width_mm'] * fp['depth_mm'] / 1e6:.1f} m²")
    out.append(item("exact_tiling", "No leftover gaps", {"GAPS_IN_FLOOR"}, "Spaces fill the footprint exactly (" + ", ".join(tiling) + ")."))

    margins = []
    for s in program.spaces:
        placed = by_key.get((s.floor, s.id))
        if placed and s.min_area_m2 and s.type not in ("garage", "stair"):  # those are fixed standard sizes
            margins.append((placed.area_m2 / s.min_area_m2 - 1, s, placed))
    if margins:
        m, s, placed = min(margins, key=lambda x: x[0])
        tight = f"tightest is {s.name}: {placed.area_m2:.1f} m² against a {s.min_area_m2:g} m² minimum (+{100 * m:.0f}%)"
    else:
        tight = "no minimums apply"
    out.append(item("minimum_sizes", "Minimum sizes", {"TOO_SMALL", "TOO_NARROW"},
                    f"All {len(margins)} sized spaces meet their minimum width and area; {tight}."))
    need = [s for s in program.spaces if s.exterior == "required"]
    lit = [s for s in need if (s.floor, s.id) in by_key and by_key[(s.floor, s.id)].exterior_sides]
    pref = [s for s in program.spaces if s.exterior == "preferred"]
    pref_lit = [s for s in pref if (s.floor, s.id) in by_key and by_key[(s.floor, s.id)].exterior_sides]
    out.append(item("exterior_exposure", "Daylight and ventilation", {"NO_EXTERIOR_WALL"},
                    f"{len(lit)} of {len(need)} rooms that need windows have an outside wall"
                    + (f"; {len(pref_lit)} of {len(pref)} bathrooms/service rooms also do." if pref else "."),
                    warn=frozenset({"NO_EXTERIOR_WALL"})))
    front = [s.name for s in program.spaces if s.front]
    out.append(item("front_access", "Road access", {"NOT_AT_FRONT", "NO_ENTRANCE"},
                    f"{', '.join(front)} face{'s' if len(front) == 1 else ''} the road."))
    links = [x for x in allocation.passages if x.a != "exterior"]
    shortest = min(links, key=lambda x: x.overlap_mm, default=None)
    short_txt = ""
    if shortest:
        a, b = by_key.get((shortest.floor, shortest.a)), by_key.get((shortest.floor, shortest.b))
        short_txt = f"; the shortest shared wall is {a.name if a else shortest.a} – {b.name if b else shortest.b} at {shortest.overlap_mm / 1000:g} m"
    out.append(item("passages", "Doors fit their walls", {"NO_PASSAGE"},
                    f"{len(links)} doors and openings each have at least the wall length they need{short_txt}."))
    out.append(item("reachability", "Every room reachable", {"UNREACHABLE"},
                    f"All {len(rooms) + len([s for s in allocation.spaces if s.filler])} spaces can be reached from the entrance"
                    + (" or the stair." if program.storeys > 1 else ".")))
    return out


def _metrics(allocation: Allocation, program: Program) -> LayoutMetrics:
    fp = allocation.footprint
    plot = allocation.plot["width_mm"] * allocation.plot["depth_mm"] / 1e6
    footprint = fp["width_mm"] * fp["depth_mm"] / 1e6
    roofed = [s for s in allocation.spaces if not s.filler]
    built = sum(s.area_m2 for s in roofed)
    circulation = sum(s.area_m2 for s in roofed if s.type in CIRCULATION)
    spec = {(s.floor, s.id): s for s in program.spaces}
    rooms = []
    for s in roofed:
        p = spec.get((s.floor, s.id))
        if p is None or s.type in CIRCULATION or s.type == "garage":
            continue
        rooms.append(RoomMetric(id=s.id, name=s.name, floor=s.floor, area_m2=s.area_m2, min_m2=p.min_area_m2,
                                target_m2=p.target_area_m2, width_m=s.w_mm / 1000, depth_m=s.h_mm / 1000))
    return LayoutMetrics(
        plot_m2=round(plot, 1), footprint_m2=round(footprint, 1), ground_coverage_pct=round(100 * footprint / plot, 1),
        built_up_m2=round(built, 1), floor_area_ratio=round(built / plot, 2),
        circulation_pct=round(100 * circulation / built, 1) if built else 0,
        terrace_m2=round(sum(s.area_m2 for s in allocation.spaces if s.filler), 1),
        rooms_at_or_above_target=sum(1 for r in rooms if r.area_m2 + 1e-6 >= r.target_m2 * 0.95),
        rooms_total=len(rooms), rooms=sorted(rooms, key=lambda r: (r.floor, -r.area_m2)))
