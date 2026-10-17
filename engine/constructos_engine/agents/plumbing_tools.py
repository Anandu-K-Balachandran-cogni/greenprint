"""Coded tools for the plumbing_engineer agent network (Neuro-SAN).

The agent decides what to do; these tools do the work with the same engine code the
Plumbing 2D button uses, and persist results in the project store, so any later
session (or a person using the app) sees the same diagram.

sly_data (never shown to the LLM):
  project_id      engine project id
  routing_source  the saved editor plan (RoutingSource dict) — needed to (re)generate
  plumbing_diagram_id  set by plumb_plan; read by plumbing_facts
"""

import asyncio
import json
from collections import Counter
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from .. import services, store
from ..electrical import RoutingSource


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _room_lookup(source: RoutingSource, value: str | None) -> tuple[str | None, str | None]:
    """Resolve a room the agent named (id or name, any case) to its id; else an error listing the options."""
    if not value:
        return None, None
    ground = min(f.index for f in source.floors)
    rooms = [r for r in source.rooms if r.floor == ground and not r.outdoor]
    v = value.strip().lower()
    for r in rooms:
        if v in (r.id.lower(), r.name.lower()):
            return r.id, None
    return None, f"There is no ground-floor room called '{value}'. Choose one of: " + ", ".join(r.name for r in rooms) + "."


def summarize(d: dict) -> dict:
    """The facts an agent may state about a plumbing diagram — measured values only."""
    rooms = {(r["floor"], r["id"]): r["name"] for r in d["rooms"]}
    floors = {f["index"]: f["name"] for f in d["floors"]}
    fixtures: dict[str, Counter] = {}
    for e in d["endpoints"]:
        if e["kind"] in ("water_entry", "drain_exit", "stack"):
            continue
        where = f"{rooms.get((e['floor'], e['room_id']), e['room_id'])} ({floors.get(e['floor'], e['floor'])})"
        fixtures.setdefault(where, Counter())[e["label"].split(" — ")[0]] += 1
    special = {k: sorted({rooms.get((e["floor"], e["room_id"]), "?") for e in d["endpoints"] if e["kind"] == k})
               for k in ("water_entry", "drain_exit")}
    stacks = sorted({e["label"] for e in d["endpoints"] if e["kind"] == "stack"})
    nm = d.get("network_metrics") or {}
    networks = {k: {"connected": m["endpoints_connected"], "total": m["endpoints_total"],
                    "length_m": round(m["selected"]["length_mm"] / 1000, 1), "bends": m["selected"]["bends"],
                    "improvement_vs_shortest_pct": m["improvement_pct"]} for k, m in nm.items()}
    return {"diagram_id": d["id"], "status": d["status"], "plan_fingerprint": d["source_hash"][:19],
            "fixtures_by_room": {k: dict(v) for k, v in fixtures.items()},
            "water_entry_room": special["water_entry"], "manhole_room": special["drain_exit"], "stacks": stacks,
            "networks": networks, "unresolved": [u["reason"] for u in d["unresolved"]],
            "assumptions": d["assumptions"], "gravity_falls_verified": d.get("gravity") != "unverified"}


class PlumbPlan(CodedTool):
    """Generate (or regenerate) the plumbing for the saved plan and store it."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        if not sly_data.get("project_id"):
            return _error("No project is attached to this conversation.")
        if not sly_data.get("routing_source"):
            return _error("The saved plan was not provided; ask the user to open the plan's Services page.")
        try:
            source = RoutingSource.model_validate(sly_data["routing_source"])
        except ValueError as e:
            return _error(f"The saved plan could not be read: {e}")
        entry, problem = _room_lookup(source, args.get("water_entry_room"))
        if problem:
            return _error(problem)
        exit_, problem = _room_lookup(source, args.get("manhole_room"))
        if problem:
            return _error(problem)
        diagram = await asyncio.to_thread(services.generate_plumbing, sly_data["project_id"], source, entry_room=entry,
                                          exit_room=exit_, actor="agent:plumbing_engineer")
        sly_data["plumbing_diagram_id"] = diagram["id"]
        return json.dumps(summarize(diagram))


class PlumbingFacts(CodedTool):
    """Measured facts about the latest plumbing diagram of this plan."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid = sly_data.get("project_id")
        if not pid:
            return _error("No project is attached to this conversation.")
        diagram_id = sly_data.get("plumbing_diagram_id")
        if not diagram_id:
            editor = (sly_data.get("routing_source") or {}).get("editor_project_id")
            listed = [d for d in await asyncio.to_thread(store.list_diagrams, pid)
                      if d["kind"] == "plumbing" and (editor is None or d["editor_project_id"] == editor)]
            if not listed:
                return _error("No plumbing has been generated for this plan yet. Use plumb_plan first.")
            diagram_id = listed[-1]["id"]
        d = await asyncio.to_thread(store.get_diagram, pid, diagram_id)
        facts = summarize(d)
        source_hash = (sly_data.get("routing_source") or {}).get("source_hash")
        facts["matches_saved_plan"] = source_hash is None or source_hash == d["source_hash"]
        return json.dumps(facts)
