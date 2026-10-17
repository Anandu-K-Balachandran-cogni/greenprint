"""Coded tools for the electrical_engineer agent network (Neuro-SAN).

sly_data (never shown to the LLM):
  project_id              engine project id
  routing_source          the saved editor plan (RoutingSource dict)
  electrical_diagram_id   set by wire_plan; read by electrical_facts
"""

import asyncio
import json
from collections import Counter
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from .. import services, store
from ..electrical import ExtraEndpoint, RoutingSource
from .plumbing_tools import _error

KINDS = {"ac_point", "ev_charger", "power_socket", "socket", "geyser", "exhaust", "light"}


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def _room_list(source: RoutingSource) -> list[dict]:
    floors = {f.index: f.name for f in source.floors}
    return [{"name": r.name, "type": r.type, "floor": floors.get(r.floor, r.floor)} for r in source.rooms if not r.outdoor]


def _rooms_by_name(source: RoutingSource) -> dict[str, Any]:
    return {**{r.name.lower(): r for r in source.rooms}, **{r.id.lower(): r for r in source.rooms}}


def summarize(d: dict) -> dict:
    rooms = {(r["floor"], r["id"]): r["name"] for r in d["rooms"]}
    floors = {f["index"]: f["name"] for f in d["floors"]}
    points: dict[str, Counter] = {}
    for e in d["endpoints"]:
        if e["kind"] in ("db", "riser"):
            continue
        where = f"{rooms.get((e['floor'], e['room_id']), e['room_id'])} ({floors.get(e['floor'], e['floor'])})"
        points.setdefault(where, Counter())[e["label"].split(" — ")[0]] += 1
    db = next((rooms.get((e["floor"], e["room_id"])) for e in d["endpoints"] if e["kind"] == "db"), None)
    m = d["metrics"]
    return {"electrical_diagram_id": d["id"], "status": d["status"], "distribution_board_room": db,
            "points_by_room": {k: dict(v) for k, v in points.items()},
            "circuits": [{"id": c["id"], "label": c["label"], "points": len(c["endpoint_ids"])} for c in d["circuits"]],
            "connected": f"{m['endpoints_connected']} of {m['endpoints_total']}",
            "route_length_m": round(m["selected"]["length_mm"] / 1000, 1),
            "improvement_vs_shortest_pct": m["improvement_pct"],
            "extra_points_requested": (d.get("preferences") or {}).get("extras", []),
            "unresolved": [u["reason"] for u in d["unresolved"]], "assumptions": d["assumptions"][:8]}


def _latest(pid: str, editor: str | None) -> dict | None:
    listed = [x for x in store.list_diagrams(pid) if x["kind"] == "electrical" and (editor is None or x["editor_project_id"] == editor)]
    return store.get_diagram(pid, listed[-1]["id"]) if listed else None


class WirePlan(CodedTool):
    """Generate (or regenerate) the electrical layout, optionally with extra points the user asked for."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid, raw = sly_data.get("project_id"), sly_data.get("routing_source")
        if not pid:
            return _error("No project is attached to this conversation.")
        if not raw:
            return _error("The saved plan was not provided; ask the user to open the plan's Services page.")
        source = RoutingSource.model_validate(raw)
        by_name = _rooms_by_name(source)
        db_room = None
        if args.get("distribution_board_room"):
            r = by_name.get(str(args["distribution_board_room"]).strip().lower())
            if r is None or r.floor != min(f.index for f in source.floors):
                return _error(f"There is no ground-floor room called '{args['distribution_board_room']}'.")
            db_room = r.id
        extras: list[ExtraEndpoint] = []
        keep = args.get("keep_previous_points", True)
        if keep:
            prev = await asyncio.to_thread(_latest, pid, source.editor_project_id)
            extras += [ExtraEndpoint(**x) for x in ((prev or {}).get("preferences") or {}).get("extras", [])
                       if x.get("room_id") in {r.id for r in source.rooms}]
        problems = []
        for p in args.get("extra_points") or []:
            p = _as_dict(p)  # Neuro-SAN passes "object" arguments as pydantic models
            room = by_name.get(str(p.get("room", "")).strip().lower())
            kind = p.get("kind")
            if room is None:
                problems.append(f"no room called '{p.get('room')}'")
                continue
            if kind not in KINDS:
                problems.append(f"'{kind}' is not a point type ({', '.join(sorted(KINDS))})")
                continue
            extras.append(ExtraEndpoint(room_id=room.id, kind=kind, count=max(1, min(6, int(p.get("count") or 1))),
                                        reason=str(p.get("reason") or "requested")[:120]))
        if problems:
            return _error("Not applied: " + "; ".join(problems) + ". Rooms: " + ", ".join(sorted({r.name for r in source.rooms})) + ".")
        d = await asyncio.to_thread(services.generate_electrical, pid, source, db_room=db_room, extras=extras,
                                    preferences={"text": None, "clarification": None, "rejected": [],
                                                 "extras": [e.model_dump() for e in extras]},
                                    actor="agent:electrical_engineer")
        sly_data["electrical_diagram_id"] = d["id"]
        return json.dumps(summarize(d))


class ElectricalFacts(CodedTool):
    """Measured facts about the latest electrical layout of this plan."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid = sly_data.get("project_id")
        if not pid:
            return _error("No project is attached to this conversation.")
        did = sly_data.get("electrical_diagram_id")
        editor = (sly_data.get("routing_source") or {}).get("editor_project_id")
        d = await asyncio.to_thread(store.get_diagram, pid, did) if did else await asyncio.to_thread(_latest, pid, editor)
        raw = sly_data.get("routing_source")
        rooms = _room_list(RoutingSource.model_validate(raw)) if raw else []
        if d is None:
            # Still useful: the room names let the agent place requested points in one wire_plan call.
            return json.dumps({"no_layout_yet": True, "rooms": rooms,
                               "note": "Nothing is wired yet; wire_plan creates the layout (with any extra points)."})
        facts = summarize(d)
        facts["rooms"] = rooms
        source_hash = (sly_data.get("routing_source") or {}).get("source_hash")
        facts["matches_saved_plan"] = source_hash is None or source_hash == d["source_hash"]
        return json.dumps(facts)
