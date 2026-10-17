"""Coded tools for the interior_designer agent network (Neuro-SAN).

sly_data (never shown to the LLM):
  project_id                       the engine project
  interior: {floor, room_ids, furnish, home_has_pooja, editor_project_id}
                                   the editor floor as the browser holds it, and the rooms in scope
  design_id, design                set by propose_design; sent back to the editor to preview
"""

import asyncio
import json
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from .. import services
from ..interior import solver
from .electrical_tools import _as_dict
from .plumbing_tools import _error

BRIEF_KEYS = ("base_theme", "title", "rationale", "curtain_style", "blind_style", "rug_shape", "plants", "pooja", "rooms")
# Flat tool arguments (nested objects are unreliable as LLM tool arguments) → the brief's groups.
COLOR_ARGS = {"wall_color": "walls", "accent_color": "accent", "ceiling_color": "ceiling", "wet_wall_color": "wet_walls",
              "fabric_color": "fabric", "wood_color": "wood", "metal_color": "metal", "curtain_color": "curtain",
              "blind_color": "blind", "rug_color": "rug", "art_color": "art"}
GROUP_ARGS = {"floors": ("floor_", ("living", "bedroom", "wet", "hall")), "lights": ("light_", ("living", "bedroom", "dining", "other"))}
PIECE_ARGS = ("sofa", "coffee_table", "tv", "chair", "bed", "wardrobe", "dining_table", "dining_chair")


# Short forms the model sometimes uses for enum values.
ALIASES = {"curtain_style": {"sheer": "sheer_curtain", "sheers": "sheer_curtain", "curtains": "curtain", "drape": "curtain", "drapes": "curtain"},
           "blind_style": {"roller": "roller_blind", "roman": "roman_blind"},
           "rug_shape": {"round": "round_rug", "circle": "round_rug", "circular": "round_rug", "rect": "rug", "rectangle": "rug", "rectangular": "rug"}}


def brief_from_args(args: dict[str, Any]) -> dict:
    brief = {k: _plain(args[k]) for k in BRIEF_KEYS if args.get(k) not in (None, "", [], {})}
    for key, table in ALIASES.items():
        if isinstance(brief.get(key), str):
            brief[key] = table.get(brief[key].strip().lower(), brief[key].strip().lower())
    colors = {v: str(args[k]).strip().lower() for k, v in COLOR_ARGS.items() if args.get(k)}
    if colors:
        brief["colors"] = colors
    for group, (prefix, names) in GROUP_ARGS.items():
        picked = {n: args[prefix + n] for n in names if args.get(prefix + n)}
        if picked:
            brief[group] = picked
    pieces = {k: args[k] for k in PIECE_ARGS if args.get(k)}
    if pieces:
        brief["pieces"] = pieces
    return brief


def _plain(value: Any) -> Any:
    """Neuro-SAN passes object arguments as pydantic models; turn them (and lists of them) into plain data."""
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict) or hasattr(value, "model_dump") or hasattr(value, "dict"):
        return {k: _plain(v) for k, v in _as_dict(value).items() if v is not None}
    return value


class RoomFacts(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        interior = sly_data.get("interior") or {}
        if not interior.get("floor"):
            return _error("The editor floor was not provided; ask the user to open the design panel in the 3D editor.")
        try:
            rooms = await asyncio.to_thread(solver.facts, interior["floor"], None)
        except solver.SolverError as e:
            return _error(str(e))
        scope = interior.get("room_ids")
        return json.dumps({"scope": "the selected room" if scope else "the whole floor",
                           "rooms_in_scope": [r for r in rooms if not scope or r["room_id"] in scope],
                           "other_rooms": [r["name"] for r in rooms if scope and r["room_id"] not in scope],
                           "pooja_elsewhere_in_home": bool(interior.get("home_has_pooja"))})


class CatalogueSearch(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            cat = await asyncio.to_thread(solver.catalog)
        except solver.SolverError as e:
            return _error(str(e))
        category = str(args.get("category") or "").strip().lower()
        query = str(args.get("query") or "").strip().lower()
        pieces = [p for p in cat["pieces"] if (not category or p["category"].lower() == category)
                  and (not query or query in p["name"].lower() or query in p["id"])]
        return json.dumps({"pieces": pieces or cat["pieces"], "themes": cat["themes"], "floor_materials": cat["floor_materials"],
                           "note": "Use piece ids, theme ids and floor material ids exactly as listed."})


class ProposeDesign(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid, interior = sly_data.get("project_id"), sly_data.get("interior") or {}
        if not pid or not interior.get("floor"):
            return _error("The editor floor was not provided; ask the user to open the design panel in the 3D editor.")
        brief = brief_from_args(args)
        tier = str(args.get("budget_tier") or "standard").lower()
        try:
            design = await asyncio.to_thread(
                services.propose_design, pid, interior["floor"], brief, room_ids=interior.get("room_ids"),
                furnish=interior.get("furnish", True), home_has_pooja=bool(interior.get("home_has_pooja")),
                editor_project_id=interior.get("editor_project_id"), tier=tier if tier in ("basic", "standard", "premium") else "standard",
                actor="agent:interior_designer")
        except services.ServiceError as err:
            return _error(f"{err.message} Fix the brief and call propose_design again.")
        sly_data["design_id"] = design["id"]
        p, b = design["plan"], design["budget"]
        return json.dumps({
            "design_id": design["id"],
            "rooms": [{"name": r["name"], "pieces": r["pieces"], "wall_color": r["wall_color"], "accent": r.get("accent"), "floor": r["floor"]} for r in p["rooms"]],
            "could_not_place": p["skipped"], "clearance_problems": p["issues"],
            "pieces_added": sum(a["count"] for a in p["added"]), "pieces_restyled": p["restyled"],
            "budget_lakh_inr": round(b["total"] / 1e5, 2), "budget_tier": b["tier"],
            "budget_by_group_lakh_inr": {k: round(v / 1e5, 2) for k, v in b["groups"].items()}, "budget_notes": b["notes"],
        })
