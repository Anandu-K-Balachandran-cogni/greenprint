"""Room programme: the brief turned into sized spaces and a passage tree.

The programme is deterministic. Spaces the brief implies but does not state
(living room, bathrooms, circulation, stair core) are added as visible
assumptions. Doors form a tree rooted at the entrance, so every space is
reachable by construction once the solver realises each passage.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from .brief import BEDROOM_TYPES, Brief

Zone = Literal["front", "middle", "rear", "any"]
Exposure = Literal["required", "preferred", "none"]
PassageKind = Literal["door", "opening", "entrance", "garage", "stair"]


@dataclass(frozen=True)
class SpaceRule:
    min_dim_mm: int
    min_area_m2: float
    target_area_m2: float
    max_area_m2: float
    zone: Zone
    exterior: Exposure
    max_aspect: float | None = 2.2


RULES: dict[str, SpaceRule] = {
    "foyer": SpaceRule(1500, 3.0, 4.5, 8, "front", "required"),
    "living": SpaceRule(3300, 12, 16, 30, "front", "required"),
    "family": SpaceRule(3000, 10, 13, 22, "middle", "preferred"),
    "dining": SpaceRule(2700, 8, 10, 18, "middle", "preferred"),
    "kitchen": SpaceRule(2400, 6.5, 8.5, 14, "middle", "required"),
    "master_bedroom": SpaceRule(3300, 12, 14.5, 22, "rear", "required"),
    "bedroom": SpaceRule(3000, 9.5, 12, 18, "rear", "required"),
    "guest_room": SpaceRule(3000, 9.5, 11, 16, "rear", "required"),
    "study": SpaceRule(2400, 6, 8, 12, "any", "required"),
    "home_office": SpaceRule(2400, 6, 8, 12, "any", "required"),
    "bathroom": SpaceRule(1500, 3.2, 4.2, 6.5, "any", "preferred"),
    "toilet": SpaceRule(1200, 1.8, 2.4, 3.5, "any", "preferred"),
    "utility": SpaceRule(1500, 2.5, 3.5, 6, "rear", "preferred"),
    "laundry": SpaceRule(1500, 2.5, 3.5, 6, "rear", "preferred"),
    "store": SpaceRule(1200, 2, 3, 5, "any", "none"),
    "prayer": SpaceRule(1500, 2.2, 3, 5, "any", "none"),
    "balcony": SpaceRule(1200, 3, 5, 9, "any", "required", 4.0),
    "corridor": SpaceRule(1200, 2.5, 5, 14, "any", "none", None),
    "hall": SpaceRule(1200, 2.5, 6, 16, "any", "none", None),
    "terrace": SpaceRule(1500, 2, 10, 80, "any", "required", None),
    "other": SpaceRule(2400, 6, 9, 16, "any", "preferred"),
}

# Compact plots use minimums in line with India's National Building Code (NBC 2016,
# Part 3) instead of comfortable targets. Only defaults are compacted; sizes the
# user stated are never reduced.
COMPACT_RULES: dict[str, tuple[int, float]] = {  # type -> (min dimension mm, min area m²)
    "master_bedroom": (3000, 9.5), "bedroom": (2700, 7.5), "guest_room": (2700, 7.5), "living": (3000, 9.5),
    "family": (2700, 7.5), "dining": (2400, 5.5), "kitchen": (1800, 5.0), "bathroom": (1200, 2.8),
    "toilet": (1200, 1.5), "study": (2100, 5.0), "home_office": (2100, 5.0), "utility": (1200, 2.0),
    "laundry": (1200, 2.0), "store": (1200, 1.5), "prayer": (1200, 1.5), "hall": (1200, 2.0),
}
COMPACT_BUILDABLE_M2 = 100


class Setbacks(BaseModel):
    front_mm: int = 1500
    rear_mm: int = 1000
    side_mm: int = 900


def default_setbacks(plot_w_mm: int, plot_d_mm: int) -> Setbacks:
    """Assumed open margins that shrink with the plot; replace with the local bylaw values."""
    front = 1500 if plot_d_mm > 15000 else 1000 if plot_d_mm > 9000 else 600
    rear = 1000 if plot_d_mm > 15000 else 600 if plot_d_mm > 9000 else 0
    side = 900 if plot_w_mm > 10000 else 600 if plot_w_mm > 7000 else 0
    return Setbacks(front_mm=front, rear_mm=rear, side_mm=side)


def buildable_m2(plot_w_mm: int, plot_d_mm: int, setbacks: Setbacks, grid_mm: int = 300) -> float:
    w = (plot_w_mm - 2 * setbacks.side_mm) // grid_mm * grid_mm
    d = (plot_d_mm - setbacks.front_mm - setbacks.rear_mm) // grid_mm * grid_mm
    return max(0, w) * max(0, d) / 1e6


CAR_WIDTH_MM = 2700
CAR_LENGTH_MM = 5400
STAIR_W_MM = 2400   # dog-leg stair: two 1.05 m flights plus a centre gap
STAIR_D_MM = 3300   # 8 treads x 250 mm plus a 1.2 m landing and clearances

HABITABLE = BEDROOM_TYPES | {"living", "family", "study", "home_office"}


class Space(BaseModel):
    id: str
    type: str
    name: str
    floor: int
    min_w_mm: int
    min_h_mm: int
    min_area_m2: float
    target_area_m2: float
    max_area_m2: float
    zone: Zone
    exterior: Exposure
    max_aspect: float | None
    front: bool = False          # must touch the road-side edge of the footprint
    vertical_core: bool = False  # same rectangle on every floor
    source: str = "brief"        # "brief" or "assumption"
    user_min: bool = False       # the minimum area was stated by the user


ChangeAction = Literal["set_min_area", "set_floor", "set_zone", "require_exterior", "add_room", "remove_room",
                       "require_adjacent"]
REMOVABLE = {"foyer-1": "foyer", "dining-1": "dining", "toilet-1": "guest toilet"}  # spaces we added, not the user


class Change(BaseModel):
    """One typed change to the room programme, requested by the user or suggested by the reviewer."""
    action: ChangeAction
    space_id: str | None = None
    other_space_id: str | None = None
    value_m2: float | None = None
    floor: int | None = None
    zone: Literal["front", "middle", "rear"] | None = None
    room_type: str | None = None
    name: str | None = None

    def describe(self, names: dict[str, str]) -> str:
        n = names.get(self.space_id or "", self.space_id or "")
        m = names.get(self.other_space_id or "", self.other_space_id or "")
        if self.action == "set_min_area":
            return f"{n}: at least {self.value_m2 or 0:g} m²"
        if self.action == "set_floor":
            return f"{n}: move to {_floor_name(self.floor or 0)}"
        if self.action == "set_zone":
            return f"{n}: towards the {self.zone} of the plot"
        if self.action == "require_exterior":
            return f"{n}: must have an outside wall"
        if self.action == "add_room":
            return f"Add {self.name} ({(self.room_type or '').replace('_', ' ')}) on {_floor_name(self.floor or 0)}"
        if self.action == "remove_room":
            return f"Remove {n}"
        return f"{n} connected to {m} by a door"


class Passage(BaseModel):
    """Access to `b`. `a` is the preferred parent; `options` are acceptable alternatives.

    Every option ranks above `b` in the access tree (it is created earlier and
    never depends on `b`), so whichever parent the solver picks, every space
    remains reachable from the entrance.
    """
    a: str  # preferred parent ("exterior" for the entrance)
    b: str
    options: list[str] = []
    kind: PassageKind
    floor: int
    min_overlap_mm: int
    extra: bool = False  # a requested connection in addition to the access tree


class Program(BaseModel):
    storeys: int
    changes: list[Change] = []
    compact: bool = False
    setbacks: Setbacks = Setbacks()
    spaces: list[Space]
    passages: list[Passage]
    assumptions: list[str]


@dataclass
class _Builder:
    storeys: int
    compact: bool = False
    spaces: list[Space] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def ids(self) -> set[str]:
        return {s.id for s in self.spaces}

    def new_id(self, prefix: str) -> str:
        n = 1
        while f"{prefix}-{n}" in self.ids():
            n += 1
        return f"{prefix}-{n}"

    def add(self, type_: str, name: str, floor: int, *, id_: str | None = None, source: str = "assumption",
            min_area: float | None = None, **extra) -> Space:
        rule = RULES.get(type_, RULES["other"])
        user_min = bool(min_area)
        min_dim, base_min, target = rule.min_dim_mm, rule.min_area_m2, rule.target_area_m2
        if self.compact and type_ in COMPACT_RULES:
            min_dim, base_min = COMPACT_RULES[type_]
            target = round(base_min * 1.2, 1)
        min_area = max(base_min, min_area or 0)
        space = Space(
            id=id_ or self.new_id(type_.replace("_", "-")), type=type_, name=name, floor=floor,
            min_w_mm=min_dim, min_h_mm=min_dim,
            min_area_m2=min_area, target_area_m2=max(target, min_area),
            max_area_m2=max(rule.max_area_m2, min_area * 1.5),
            zone=rule.zone, exterior=rule.exterior, max_aspect=rule.max_aspect, source=source, user_min=user_min,
        )
        space = space.model_copy(update=extra)
        self.spaces.append(space)
        return space

    def link(self, a: str, b: str, kind: PassageKind = "door", floor: int = 0, overlap: int = 1200) -> None:
        self.passages.append(Passage(a=a, b=b, kind=kind, floor=floor, min_overlap_mm=overlap))

    def access(self, child: Space, parents: list["Space | None"], kind: PassageKind, overlap: int) -> None:
        """Link `child` to the first available parent, accepting the others as alternatives."""
        ids = list(dict.fromkeys(p.id for p in parents if p is not None and p.id != child.id))
        if not ids:
            raise ValueError(f"no access available for {child.id}")
        self.passages.append(Passage(a=ids[0], b=child.id, options=ids[1:], kind=kind, floor=child.floor,
                                     min_overlap_mm=overlap))


def _floor_for(room_floor: int | None, type_: str, storeys: int) -> int:
    """Stated floor wins; otherwise bedrooms go upstairs and everything else stays on the ground."""
    if room_floor is not None:
        return room_floor
    return 1 if storeys > 1 and type_ in BEDROOM_TYPES else 0


def _names_an_ensuite(name: str) -> bool:
    n = name.lower().replace("-", "").replace(" ", "")
    return "ensuite" in n or "attached" in n


def _apply_brief_changes(brief: Brief, changes: list[Change]) -> Brief:
    """Changes to rooms the user stated are applied to the brief, so the access tree is rebuilt around them."""
    from .brief import Room
    b = brief.model_copy(deep=True)
    rooms = {r.id: r for r in b.rooms}
    for c in changes:
        if c.action == "set_min_area" and c.space_id in rooms:
            rooms[c.space_id].min_area_m2 = c.value_m2
        elif c.action == "set_floor" and c.space_id in rooms:
            rooms[c.space_id].floor_index = c.floor
        elif c.action == "add_room":
            prefix = (c.room_type or "other").replace("_", "-")
            n = 1
            while f"{prefix}-{n}" in rooms or f"{prefix}-{n}" in REMOVABLE:
                n += 1
            room = Room(id=f"{prefix}-{n}", type=c.room_type or "other", name=c.name or "Room", floor_index=c.floor,
                        requirement="hard", source="Requested change")
            b.rooms.append(room)
            rooms[room.id] = room
    return b


def build_program(brief: Brief, setbacks: Setbacks | None = None, changes: list[Change] | None = None) -> Program:
    changes = list(changes or [])
    brief = _apply_brief_changes(brief, changes)
    removed = {c.space_id for c in changes if c.action == "remove_room"}
    storeys = max(1, brief.storeys or 1)
    setbacks = setbacks or default_setbacks(brief.plot.width_mm or 0, brief.plot.depth_mm or 0)
    area = buildable_m2(brief.plot.width_mm or 0, brief.plot.depth_mm or 0, setbacks)
    compact = area < COMPACT_BUILDABLE_M2
    b = _Builder(storeys, compact)
    if compact:
        b.assumptions.append(f"Compact plot ({area:.0f} m² buildable): unstated room sizes use NBC-style minimums "
                             "instead of comfortable targets.")

    # 1. Spaces the user asked for, on their stated floor; unplaced bedrooms go upstairs.
    for r in brief.rooms:
        floor = _floor_for(r.floor_index, r.type, storeys)
        if r.floor_index is None and storeys > 1 and r.type in BEDROOM_TYPES:
            b.assumptions.append(f"{r.name} placed on the first floor (no floor was specified).")
        b.add(r.type, r.name, floor, id_=r.id, source="brief", min_area=r.min_area_m2)

    def on(floor: int, *types: str) -> list[Space]:
        return [s for s in b.spaces if s.floor == floor and (not types or s.type in types)]

    # 2. Implied spaces.
    if not [s for s in b.spaces if s.type == "living"]:
        b.add("living", "Living room", 0)
        b.assumptions.append("Added a living room on the ground floor (a home needs one; not stated).")
    if not [s for s in b.spaces if s.type == "kitchen"]:
        b.add("kitchen", "Kitchen", 0)
        b.assumptions.append("Added a kitchen on the ground floor.")
    kitchen = next(s for s in b.spaces if s.type == "kitchen")
    if not [s for s in b.spaces if s.type == "dining"] and len([s for s in b.spaces if s.type in BEDROOM_TYPES]) >= 2:
        if compact:
            living = next(s for s in b.spaces if s.type == "living")
            living.min_area_m2 = round(living.min_area_m2 + 3, 1)
            living.target_area_m2 = max(living.target_area_m2, round(living.min_area_m2 * 1.2, 1))
            if living.source == "assumption":
                living.name = "Living + dining"
            b.assumptions.append(f"Dining shares the {living.name.lower()} (compact plot; +3 m²).")
        elif "dining-1" in removed:
            b.assumptions.append("No separate dining area (removed at your request).")
        else:
            b.add("dining", "Dining", kitchen.floor)
            b.assumptions.append("Added a dining area next to the kitchen.")

    explicit_baths = [s for s in b.spaces if s.type in {"bathroom", "toilet"}]
    ensuites: list[tuple[Space, Space]] = []  # (bedroom, bathroom); linked once the bedroom has access
    for room in brief.rooms:
        if room.type not in BEDROOM_TYPES:
            continue  # an en-suite belongs to a bedroom; the flag on any other room is ignored
        if room.attached_bathroom or (room.type == "master_bedroom" and not explicit_baths):
            bed = next(s for s in b.spaces if s.id == room.id)
            # A bathroom the user listed as the en-suite is that bedroom's en-suite, not an extra room.
            listed = next((s for s in explicit_baths if s.type == "bathroom" and s.floor == bed.floor
                           and s.id not in {x.id for _, x in ensuites} and _names_an_ensuite(s.name)), None)
            if listed:
                ensuites.append((bed, listed))
                b.assumptions.append(f"{listed.name} is the en-suite of {bed.name}.")
                continue
            ensuites.append((bed, b.add("bathroom", f"En-suite ({bed.name})", bed.floor)))
            if not room.attached_bathroom:
                b.assumptions.append(f"Added an en-suite bathroom to {bed.name}.")
    ensuite_ids = {bath.id for _, bath in ensuites}
    # Every floor whose bedrooms lack an en-suite needs a shared bathroom on that floor, even when
    # the brief named bathrooms elsewhere (children upstairs should not walk down to wash).
    for f in range(storeys):
        ensuited = {bed.id for bed, _ in ensuites}
        open_beds = [x for x in on(f, *BEDROOM_TYPES) if x.id not in ensuited]
        shared = [s for s in on(f, "bathroom", "toilet") if s.id not in ensuite_ids]
        if open_beds and not shared:
            b.add("bathroom", "Common bathroom", f)
            names = ", ".join(x.name for x in open_beds)
            b.assumptions.append(f"Added a common bathroom on {_floor_name(f)} ({names} had none on that floor).")
        elif not explicit_baths and f == 0 and not shared and "toilet-1" not in removed:
            b.add("toilet", "Guest toilet", 0)
            b.assumptions.append("Added a guest toilet on the ground floor (the only other bathroom is en-suite).")

    if brief.parking_spaces:
        n = brief.parking_spaces
        b.add("garage", f"Parking ({n} car{'s' if n > 1 else ''})", 0, id_="parking-1",
              source="brief", min_w_mm=CAR_WIDTH_MM * n, min_h_mm=CAR_LENGTH_MM,
              min_area_m2=CAR_WIDTH_MM * n * CAR_LENGTH_MM / 1e6,
              target_area_m2=CAR_WIDTH_MM * n * CAR_LENGTH_MM / 1e6,
              max_area_m2=CAR_WIDTH_MM * n * CAR_LENGTH_MM / 1e6 * 1.4,
              zone="front", exterior="required", max_aspect=None, front=True)

    if storeys > 1:
        stair_w = 2100 if compact else STAIR_W_MM  # compact: two 0.9 m flights
        for f in range(storeys):
            b.add("stair", "Stair", f, id_=f"stair-{f}", min_w_mm=stair_w, min_h_mm=STAIR_D_MM,
                  min_area_m2=stair_w * STAIR_D_MM / 1e6, target_area_m2=stair_w * STAIR_D_MM / 1e6,
                  max_area_m2=stair_w * STAIR_D_MM / 1e6 * 1.3, zone="middle", exterior="none",
                  max_aspect=None, vertical_core=True)
        b.assumptions.append(f"A dog-leg stair core ({stair_w / 1000:g} x {STAIR_D_MM / 1000:g} m) connects the floors.")

    ground_count = len(on(0))
    if ground_count >= 5 and not compact and "foyer-1" not in removed:
        b.add("foyer", "Foyer", 0, front=True)
        b.assumptions.append("Added an entrance foyer at the front.")

    for f in range(storeys):
        private = [s for s in on(f, *BEDROOM_TYPES, "bathroom", "toilet", "study", "home_office") if s.id not in ensuite_ids]
        if f > 0 or len(private) >= 2:
            b.add("hall", "Hall" if f == 0 else f"Upper hall ({_floor_name(f)})", f)

    # 3. Access tree per floor. Parent lists run from preferred to acceptable,
    #    and only ever name spaces linked earlier, so any choice stays a tree.
    for f in range(storeys):
        spaces = on(f)
        first = lambda *t: next((s for s in spaces if s.type in t), None)  # noqa: E731
        hall, stair = first("hall"), first("stair")
        if f == 0:
            foyer, living = first("foyer"), first("living")
            front = foyer or living
            if front is None:
                continue
            front.front = True
            b.link("exterior", front.id, "entrance", 0, 1200)
            if foyer and living:
                b.access(living, [foyer], "door", 1200)
            social = living or front
            dining, kitchen_f = first("dining"), first("kitchen")
            if dining:
                b.access(dining, [social], "opening", 1800)
            if kitchen_f:
                if brief.open_kitchen:
                    b.access(kitchen_f, [dining, social], "opening", 1800)
                else:
                    b.access(kitchen_f, [dining, social], "door", 1200)
            if hall:
                b.access(hall, [foyer, social, dining], "opening", 1200)
            public = [hall, foyer, social, dining]
            if stair:
                b.access(stair, public, "stair", 1200)
            for x in [s for s in spaces if s.type in ("utility", "laundry", "store")]:
                b.access(x, [kitchen_f, dining, hall], "door", 1050)
            for x in [s for s in spaces if s.type in ("prayer", "family")]:
                b.access(x, [social, dining, hall, foyer], "door", 1050)
            parking = first("garage")
            if parking:
                b.link("exterior", parking.id, "garage", 0, CAR_WIDTH_MM * brief.parking_spaces - 300)
            private_parents = [hall, social, dining, foyer]
            bath_parents = [hall, social, dining]  # never straight off the entrance foyer
        else:
            if stair and hall:
                b.access(hall, [stair], "stair", 1200)
            family = first("family")
            if family:
                b.access(family, [hall], "opening", 1200)
            for x in [s for s in spaces if s.type in ("balcony", "utility", "laundry", "store", "prayer")]:
                b.access(x, [hall, family], "door", 1050)
            private_parents = [hall, family]
            bath_parents = [hall, family]
        for x in [s for s in spaces if s.type in BEDROOM_TYPES | {"study", "home_office", "other"}]:
            b.access(x, private_parents, "door", 1200)
        for x in [s for s in spaces if s.type in {"bathroom", "toilet"} and s.id not in ensuite_ids]:
            b.access(x, bath_parents, "door", 1050)
    for bed, bath in ensuites:
        b.access(bath, [bed], "door", 1050)

    _apply_space_changes(b, changes)
    _validate(b)
    return Program(storeys=storeys, spaces=b.spaces, passages=b.passages, assumptions=b.assumptions,
                   compact=compact, setbacks=setbacks, changes=changes)


def _apply_space_changes(b: _Builder, changes: list[Change]) -> None:
    """Changes to any space, including the ones Greenprint added, applied after the programme is built."""
    by_id = {s.id: s for s in b.spaces}
    for c in changes:
        s = by_id.get(c.space_id or "")
        if c.action == "set_min_area" and s:
            s.min_area_m2 = c.value_m2
            s.target_area_m2 = max(s.target_area_m2, round(c.value_m2 * 1.1, 1))
            s.max_area_m2 = max(s.max_area_m2, round(c.value_m2 * 1.5, 1))
            s.user_min = True
        elif c.action == "set_zone" and s:
            s.zone = c.zone
        elif c.action == "require_exterior" and s:
            s.exterior = "required"
        elif c.action == "require_adjacent" and s and c.other_space_id in by_id:
            b.passages.append(Passage(a=s.id, b=c.other_space_id, kind="door", floor=s.floor, min_overlap_mm=1050, extra=True))


def validate_changes(changes: list[Change], program: Program, buildable_m2_per_floor: float) -> list[str]:
    """Reasons why requested changes cannot be applied; an empty list means all are valid."""
    by_id = {s.id: s for s in program.spaces}
    errors = []
    for c in changes:
        s = by_id.get(c.space_id or "")
        if c.action != "add_room" and s is None:
            errors.append("A change did not say which room it applies to." if not c.space_id
                          else f"There is no space '{c.space_id}' in this layout.")
            continue
        if c.action == "set_min_area":
            if not c.value_m2 or c.value_m2 <= 0:
                errors.append(f"{s.name}: the area must be positive.")
            elif c.value_m2 > 0.6 * buildable_m2_per_floor:
                errors.append(f"{s.name}: {c.value_m2:g} m² is more than 60% of the {buildable_m2_per_floor:.0f} m² buildable floor.")
        elif c.action == "set_floor":
            if s.source != "brief" or s.vertical_core or s.type == "garage":
                errors.append(f"{s.name} cannot change floor; only rooms from your brief can move.")
            elif c.floor is None or not 0 <= c.floor < program.storeys:
                errors.append(f"{s.name}: the building has {program.storeys} floor(s).")
        elif c.action == "set_zone":
            if not c.zone or s.vertical_core or s.type == "garage":
                errors.append(f"{s.name} cannot be moved within the plot.")
        elif c.action == "remove_room":
            if s.id not in REMOVABLE:
                errors.append(f"{s.name} came from your brief; remove it on the Requirements page instead.")
        elif c.action == "add_room":
            if c.room_type not in RULES or c.room_type in ("hall", "corridor", "terrace"):
                errors.append(f"'{c.room_type}' is not a room type that can be added.")
            elif c.floor is None or not 0 <= c.floor < program.storeys:
                errors.append(f"{c.name or 'The new room'}: the building has {program.storeys} floor(s).")
        elif c.action == "require_adjacent":
            o = by_id.get(c.other_space_id or "")
            if o is None:
                errors.append(f"There is no space '{c.other_space_id}' in this layout.")
            elif o.floor != s.floor or o.id == s.id:
                errors.append(f"{s.name} and {o.name} must be different rooms on the same floor to connect.")
    return errors


def _floor_name(f: int) -> str:
    return "the ground floor" if f == 0 else "the first floor" if f == 1 else f"floor {f}"


def _validate(b: _Builder) -> None:
    ids = [s.id for s in b.spaces]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate space IDs in programme")
    reached = {p.b for p in b.passages}
    for s in b.spaces:
        if s.id not in reached and not s.vertical_core:
            raise ValueError(f"space {s.id} has no access in the passage tree")
    # Every acceptable parent must already be reachable when the child is linked,
    # so any combination of choices is a tree rooted at the entrance or stair.
    reachable = {"exterior"} | {s.id for s in b.spaces if s.vertical_core and s.floor > 0}
    for p in b.passages:
        for parent in [p.a, *p.options]:
            if parent != "exterior" and parent not in ids:
                raise ValueError(f"passage from unknown space {parent}")
            if parent not in reachable and not any(x.b == parent for x in b.passages if x.floor == p.floor
                                                    and b.passages.index(x) < b.passages.index(p)):
                raise ValueError(f"{p.b} may be reached through {parent}, which is linked later")
        reachable.add(p.b)
