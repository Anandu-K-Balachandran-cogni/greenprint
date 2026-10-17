"""Construction cost estimate from the saved model's quantities.

Quantities are measured from the saved editor plan (areas, wall faces, doors, windows,
stairs) and from the generated services (electrical points, plumbing points and
fixtures). Each line is quantity × rate, with its basis, so the total can be audited
and the rate table changed in one place.

Rates are indicative 2026 Indian residential rates (materials + labour, INR) for a
non-metro city, in three finish tiers; metros apply a factor. The result is checked
against the published per-sq-ft planning bands for the same tier. This is a planning
estimate, not a quotation: land, approvals, interiors and furniture are excluded.
"""

import math
from collections import Counter
from typing import Literal

from pydantic import BaseModel
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .electrical import RoutingSource

SQFT_PER_M2 = 10.7639
Tier = Literal["basic", "standard", "premium"]
Location = Literal["non_metro", "metro"]
LOCATION_FACTOR = {"non_metro": 1.0, "metro": 1.35}  # metros run ~30-50 % higher than smaller cities

# item: (unit, basic, standard, premium) — INR per unit, materials + labour, non-metro 2026.
RATES: dict[str, tuple[str, float, float, float]] = {
    "structure": ("sq ft built-up", 700, 900, 1100),         # excavation, foundation, RCC frame and slabs
    "masonry_230": ("sq ft wall face", 110, 140, 180),        # 230 mm external walls
    "masonry_115": ("sq ft wall face", 70, 90, 115),          # 115 mm internal walls
    "plaster_internal": ("sq ft", 40, 55, 75),
    "plaster_external": ("sq ft", 50, 65, 85),
    "paint_internal": ("sq ft", 18, 28, 45),
    "paint_external": ("sq ft", 22, 32, 50),
    "flooring": ("sq ft", 90, 140, 260),                      # vitrified tiles laid, with skirting
    "wall_tiles": ("sq ft", 80, 120, 220),                    # bathroom and kitchen dado
    "waterproofing_terrace": ("sq ft", 70, 110, 160),         # roof/terrace waterproofing and tiles
    "door_internal": ("each", 8000, 13000, 24000),
    "door_main": ("each", 20000, 35000, 70000),
    "door_external": ("each", 12000, 20000, 36000),            # terrace and side doors in external walls
    "door_garage": ("each", 35000, 50000, 80000),
    "windows": ("sq ft", 350, 550, 900),
    "stair_finish": ("each flight", 20000, 35000, 70000),
    "electrical_point": ("each", 700, 1100, 1800),
    "electrical_db": ("lump sum", 12000, 20000, 35000),        # distribution board, earthing, main cable
    "water_point": ("each", 1300, 2000, 3200),
    "drainage_point": ("each", 1500, 2400, 3800),
    "sanitaryware": ("per bathroom", 10000, 22000, 55000),
    "kitchen_sink": ("each", 5000, 9000, 18000),
    "tank_pump": ("lump sum", 20000, 30000, 50000),
}
CONTINGENCY = 0.05
# Published planning bands, INR per sq ft built-up, non-metro (2026 guides; they vary by source).
BENCHMARK = {"basic": (1400, 1800), "standard": (1800, 2800), "premium": (2800, 5000)}
DEFAULT_WALL_MM, DEFAULT_DOOR_MM = 3000, 2100
WET = {"bathroom", "toilet"}


class Line(BaseModel):
    group: str
    item: str
    quantity: float
    unit: str
    rate: int
    amount: int
    basis: str


class Quantities(BaseModel):
    floors: int
    built_up_m2: float
    built_up_sqft: float
    terrace_m2: float
    roof_m2: float
    external_wall_m: float
    internal_wall_m: float
    openings_m2: float
    doors: dict[str, int]
    windows: int
    windows_m2: float
    windows_estimated: bool
    bathrooms: int
    kitchens: int
    stair_flights: int
    electrical_points: int
    electrical_estimated: bool
    water_points: int
    drainage_points: int
    plumbing_estimated: bool


class Estimate(BaseModel):
    schema_version: str = "constructos.estimate.v1"
    tier: Tier
    location: Location
    currency: str = "INR"
    source_hash: str
    quantities_hash: str | None
    electrical_diagram_id: str | None
    plumbing_diagram_id: str | None
    quantities: Quantities
    lines: list[Line]
    groups: dict[str, int]
    subtotal: int
    contingency: int
    total: int
    per_sqft: int
    benchmark: dict
    assumptions: list[str]


def _length(w) -> float:
    return math.hypot(w.x2 - w.x1, w.y2 - w.y1) / 1000


def quantities(source: RoutingSource, electrical: dict | None, plumbing: dict | None) -> tuple[Quantities, list[str]]:
    notes: list[str] = []
    floors = sorted({f.index for f in source.floors})
    indoor, outdoor = {}, {}
    for f in floors:
        polys = [Polygon(r.polygon) for r in source.rooms if r.floor == f and not r.outdoor and len(r.polygon) >= 3]
        out = [Polygon(r.polygon) for r in source.rooms if r.floor == f and r.outdoor and len(r.polygon) >= 3]
        indoor[f] = unary_union([p.buffer(0) for p in polys]).area / 1e6 if polys else 0.0
        outdoor[f] = unary_union([p.buffer(0) for p in out]).area / 1e6 if out else 0.0
    built = sum(indoor.values())
    ext = sum(_length(w) for w in source.walls if w.thickness_mm >= 200)
    internal = sum(_length(w) for w in source.walls if w.thickness_mm < 200)
    heights = [w.height_mm for w in source.walls if w.height_mm]
    wall_h = (sum(heights) / len(heights) / 1000) if heights else DEFAULT_WALL_MM / 1000
    ground = min(floors) if floors else 0
    doors: Counter = Counter()
    for d in source.doors:
        if (d.kind or "") == "opening":
            continue
        if (d.kind or "") == "garage":
            doors["garage"] += 1
        elif d.entrance and d.floor == ground and not doors["main"]:
            doors["main"] += 1  # the front door
        elif d.entrance:
            doors["external"] += 1
        else:
            doors["internal"] += 1
    door_m2 = sum(d.width_mm / 1000 * (d.height_mm or DEFAULT_DOOR_MM) / 1000 for d in source.doors if (d.kind or "") != "opening")
    habitable = [r for r in source.rooms if not r.outdoor and r.type in
                 {"bedroom", "master_bedroom", "guest_room", "living", "dining", "kitchen", "study", "family", "home_office", "prayer"}]
    if source.windows:
        win_n, win_m2, win_est = len(source.windows), sum(w.width_mm * w.height_mm for w in source.windows) / 1e6, False
    else:
        win_n, win_m2, win_est = len(habitable), 1.5 * len(habitable), True
        notes.append("The plan has no windows yet: allowed one 1.5 m² window per habitable room.")
    bathrooms = sum(1 for r in source.rooms if r.type in WET)
    kitchens = sum(1 for r in source.rooms if r.type == "kitchen")
    stairs = max(0, len(floors) - 1) if any(r.type == "stair" for r in source.rooms) else 0
    if electrical:
        e_points = sum(1 for e in electrical["endpoints"] if e["kind"] not in ("db", "riser"))
        e_est = False
    else:
        e_points, e_est = round(built / 3.7), True  # about one point per 40 sq ft
        notes.append("No electrical layout yet: estimated one electrical point per 40 sq ft.")
    if plumbing:
        kinds = [e["kind"] for e in plumbing["endpoints"] if e["kind"] not in ("water_entry", "drain_exit", "stack")]
        w_points = sum(1 for k in kinds if k != "floor_drain")
        d_points = sum(1 for k in kinds if k in {"wc", "basin", "shower", "sink", "washing_machine", "floor_drain"})
        p_est = False
    else:
        w_points, d_points, p_est = 3 * bathrooms + 2 * kitchens, 3 * bathrooms + kitchens, True
        notes.append("No plumbing layout yet: estimated three points per bathroom and two per kitchen.")
    top = max(floors) if floors else 0
    q = Quantities(
        floors=len(floors), built_up_m2=round(built, 1), built_up_sqft=round(built * SQFT_PER_M2),
        terrace_m2=round(sum(outdoor.values()), 1), roof_m2=round(indoor.get(top, 0) + sum(outdoor.values()), 1),
        external_wall_m=round(ext, 1), internal_wall_m=round(internal, 1), openings_m2=round(door_m2 + win_m2, 1),
        doors=dict(doors), windows=win_n, windows_m2=round(win_m2, 1), windows_estimated=win_est,
        bathrooms=bathrooms, kitchens=kitchens, stair_flights=stairs,
        electrical_points=e_points, electrical_estimated=e_est,
        water_points=w_points, drainage_points=d_points, plumbing_estimated=p_est)
    notes.append(f"Wall height {wall_h:.2f} m{' (default)' if not heights else ''}; built-up area is the indoor floor area of the saved plan.")
    return q, notes


def estimate(source: RoutingSource, *, electrical: dict | None = None, plumbing: dict | None = None,
             tier: Tier = "standard", location: Location = "non_metro") -> Estimate:
    q, notes = quantities(source, electrical, plumbing)
    t = {"basic": 1, "standard": 2, "premium": 3}[tier]
    factor = LOCATION_FACTOR[location]
    heights = [w.height_mm for w in source.walls if w.height_mm]
    h = (sum(heights) / len(heights) / 1000) if heights else DEFAULT_WALL_MM / 1000
    sqft = SQFT_PER_M2
    ext_face = q.external_wall_m * h * sqft
    int_face = q.internal_wall_m * h * sqft
    openings = q.openings_m2 * sqft
    bath_perimeter = sum(Polygon(r.polygon).length / 1000 for r in source.rooms if r.type in WET)
    kitchen_perimeter = sum(Polygon(r.polygon).length / 1000 for r in source.rooms if r.type == "kitchen")
    lines: list[Line] = []

    def add(group, item, key, qty, basis):
        if qty <= 0:
            return
        unit, *rates = RATES[key]
        rate = int(round(rates[t - 1] * factor))
        shown = round(qty, 1)  # the amount is computed from the quantity shown, so every line checks by hand
        lines.append(Line(group=group, item=item, quantity=shown, unit=unit, rate=rate,
                          amount=int(round(shown * rate)), basis=basis))

    add("Structure", "Foundation, RCC frame and slabs", "structure", q.built_up_sqft, f"{q.built_up_m2} m² built-up")
    add("Walls", "External walls (230 mm)", "masonry_230", max(0, ext_face - openings * 0.8), f"{q.external_wall_m} m × {h:.2f} m, less openings")
    add("Walls", "Internal walls (115 mm)", "masonry_115", max(0, int_face - openings * 0.2), f"{q.internal_wall_m} m × {h:.2f} m")
    add("Finishes", "Internal plaster", "plaster_internal", 2 * int_face + ext_face - openings, "both faces of internal walls + inner face of external")
    add("Finishes", "External plaster", "plaster_external", ext_face, "outer face of external walls")
    add("Finishes", "Internal paint (walls and ceilings)", "paint_internal", 2 * int_face + ext_face - openings + q.built_up_sqft, "plastered faces + ceilings")
    add("Finishes", "External paint", "paint_external", ext_face, "outer face of external walls")
    add("Finishes", "Floor tiles", "flooring", q.built_up_sqft, "indoor floor area")
    add("Finishes", "Wall tiles (bathrooms, kitchen)", "wall_tiles", (bath_perimeter * 2.1 + kitchen_perimeter * 0.5 * 0.6) * sqft,
        "bathroom walls to 2.1 m; kitchen splashback 0.6 m")
    add("Finishes", "Roof and terrace waterproofing", "waterproofing_terrace", q.roof_m2 * sqft, f"{q.roof_m2} m² roof and terraces")
    add("Doors and windows", "Main door", "door_main", q.doors.get("main", 0), "front entrance")
    add("Doors and windows", "Other external doors", "door_external", q.doors.get("external", 0), "terrace and side doors")
    add("Doors and windows", "Internal doors", "door_internal", q.doors.get("internal", 0), "from the plan")
    add("Doors and windows", "Garage door", "door_garage", q.doors.get("garage", 0), "from the plan")
    add("Doors and windows", "Windows", "windows", q.windows_m2 * sqft,
        f"{q.windows} window(s){' (allowance)' if q.windows_estimated else ' from the plan'}")
    add("Structure", "Staircase finishes and railing", "stair_finish", q.stair_flights, "per flight")
    add("Electrical", "Electrical points (wiring, switches, sockets)", "electrical_point", q.electrical_points,
        "estimated" if q.electrical_estimated else "from the electrical layout")
    add("Electrical", "Distribution board and earthing", "electrical_db", 1, "one per house")
    add("Plumbing", "Water supply points", "water_point", q.water_points, "estimated" if q.plumbing_estimated else "from the plumbing layout")
    add("Plumbing", "Drainage points", "drainage_point", q.drainage_points, "estimated" if q.plumbing_estimated else "from the plumbing layout")
    add("Plumbing", "Sanitaryware", "sanitaryware", q.bathrooms, "per bathroom")
    add("Plumbing", "Kitchen sink", "kitchen_sink", q.kitchens, "per kitchen")
    add("Plumbing", "Water tank and pump", "tank_pump", 1, "one per house")
    groups: dict[str, float] = {}
    for line in lines:
        groups[line.group] = groups.get(line.group, 0) + line.amount
    subtotal = sum(groups.values())
    contingency = int(round(subtotal * CONTINGENCY))
    total = subtotal + contingency
    per_sqft = int(round(total / q.built_up_sqft)) if q.built_up_sqft else 0
    low, high = (round(v * factor) for v in BENCHMARK[tier])
    verdict = "within" if low <= per_sqft <= high else "below" if per_sqft < low else "above"
    notes += [f"Rates: indicative 2026 {tier} finish, {'metro' if location == 'metro' else 'non-metro'} city, materials and labour (INR).",
              f"Contingency {CONTINGENCY:.0%}. Excludes land, approvals and fees, interiors, furniture, compound wall and GST.",
              "A planning estimate, not a quotation: get itemised local quotes before committing."]
    return Estimate(tier=tier, location=location, source_hash=source.source_hash, quantities_hash=source.quantities_hash,
                    electrical_diagram_id=(electrical or {}).get("id"), plumbing_diagram_id=(plumbing or {}).get("id"),
                    quantities=q, lines=lines, groups={k: round(v) for k, v in groups.items()}, subtotal=round(subtotal),
                    contingency=contingency, total=round(total), per_sqft=per_sqft,
                    benchmark={"low": low, "high": high, "verdict": verdict,
                               "basis": f"published 2026 planning band for {tier} construction"
                                        f"{' (metro factor applied)' if location == 'metro' else ''}"},
                    assumptions=notes)
