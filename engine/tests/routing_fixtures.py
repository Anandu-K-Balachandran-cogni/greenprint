"""Build a RoutingSource from a solved allocation, as the browser does from the editor model."""

from constructos_engine.electrical import RoutingSource, SourceDoor, SourceFloor
from constructos_engine.layout_checks import shared_edge_mm
from constructos_engine.routing import RoomShape
from constructos_engine.solver import Allocation


def source_from_allocation(alloc: Allocation, editor_id: str = "cos-test-0", source_hash: str = "sha256-test") -> RoutingSource:
    rooms = [RoomShape(id=s.id, name=s.name, type=s.type, floor=s.floor, outdoor=s.filler,
                       polygon=[(s.x_mm, s.y_mm), (s.x_mm + s.w_mm, s.y_mm), (s.x_mm + s.w_mm, s.y_mm + s.h_mm), (s.x_mm, s.y_mm + s.h_mm)])
             for s in alloc.spaces]
    by = {(s.floor, s.id): s for s in alloc.spaces}
    doors = []
    for p in alloc.passages:
        b = by[(p.floor, p.b)]
        if p.a == "exterior":
            doors.append(SourceDoor(floor=p.floor, x_mm=b.x_mm + b.w_mm / 2, y_mm=b.y_mm, width_mm=1000, entrance=p.kind == "entrance"))
            continue
        a = by[(p.floor, p.a)]
        if a.x_mm + a.w_mm == b.x_mm or b.x_mm + b.w_mm == a.x_mm:
            x = a.x_mm + a.w_mm if a.x_mm + a.w_mm == b.x_mm else a.x_mm
            y = (max(a.y_mm, b.y_mm) + min(a.y_mm + a.h_mm, b.y_mm + b.h_mm)) / 2
        else:
            y = a.y_mm + a.h_mm if a.y_mm + a.h_mm == b.y_mm else a.y_mm
            x = (max(a.x_mm, b.x_mm) + min(a.x_mm + a.w_mm, b.x_mm + b.w_mm)) / 2
        assert shared_edge_mm(a, b) > 0
        doors.append(SourceDoor(floor=p.floor, x_mm=x, y_mm=y, width_mm=900))
    floors = sorted({s.floor for s in alloc.spaces})
    return RoutingSource(editor_project_id=editor_id, source_hash=source_hash, rooms=rooms, doors=doors,
                         floors=[SourceFloor(index=f, name="Ground floor" if f == 0 else f"Floor {f}") for f in floors])
