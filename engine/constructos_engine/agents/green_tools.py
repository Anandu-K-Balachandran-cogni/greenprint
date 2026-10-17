"""Coded tools for the sustainability_advisor agent network (Neuro-SAN).

sly_data (never shown to the LLM):
  project_id                   the engine project
  editor_floors                the editor model's floors (window positions and sizes live only there)
  editor_project_id            the editor project
  green_id                     set by green_check; read by green_facts
"""

import asyncio
import json
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from .. import services, store
from .plumbing_tools import _error


def summarize(g: dict) -> dict:
    r, c = g["report"], g["climate"]
    return {
        "green_id": g["id"], "city": c["city"], "climate_zone": c["zone"], "rainfall_mm": c["rainfall_mm"], "sun_hours": c["sun_hours"],
        "tariff_per_kwh": f"{c['tariff']} {c['currency']}", "local_schemes": c["schemes"],
        "sources": [s["title"] for s in c["sources"]], "orientation": g["front"] and f"entrance faces {g['front'].replace('_', '-')} ({g['front_source']})",
        "score": r["score"]["total"], "score_after_fixes": r["score"]["after_fixes"],
        "score_parts": [f"{p['name']}: {p['points']}/{p['max']} ({p['note']})" for p in r["score"]["parts"]],
        "rooms": [{"room": x["name"], "window_to_floor": f"{x['wfr'] * 100:.0f}% (needs {x['required_wfr'] * 100:.0f}%)",
                   "daylight_ok": x["daylight_ok"], "cross_ventilation": x["cross_ventilation"], "windows_face": x["window_facings"],
                   "shading": x["shading"],
                   "suggested_fix": (f"widen the window from {x['fix']['from_cm']} to {x['fix']['to_cm']} cm" if x.get("fix", {}).get("kind") == "widen"
                                     else f"add a {x['fix']['width_cm']} x {x['fix']['height_cm']} cm window on the {(x['fix'].get('facing') or 'other').replace('_', '-')} wall"
                                     if x.get("fix") else None),
                   "after_fix": f"{x['wfr_after_fix'] * 100:.0f}%" if x.get("wfr_after_fix") else None} for x in r["rooms"]],
        "rainwater": {"harvest_litres_per_year": r["water"]["harvest_litres_year"], "share_of_household_use": f"{r['water']['share'] * 100:.0f}%",
                      "tank_litres": r["water"]["tank_litres"]},
        "solar_at_the_plot": (g.get("solar_site") or {}) and ({"measured_by": "satellite imagery of the plot", "imagery": g["solar_site"].get("quality"),
                              "shade_loss_pct": g["solar_site"].get("shade_loss_pct")} if (g.get("solar_site") or {}).get("available")
                             else {"not_available": g["solar_site"].get("reason")}),
        "solar": {"system_kwp": r["solar"]["kwp"], "yield_kwh_per_kwp": r["solar"]["yield_kwh_per_kwp"], "yield_from": r["solar"]["yield_source"], "roof_could_host_kwp": r["solar"]["max_kwp"], "kwh_per_year": r["solar"]["kwh_year"],
                  "share_of_use": f"{r['solar']['share'] * 100:.0f}%", "saving_per_year": f"{r['solar']['saving_year']} {r['solar']['currency']}"},
        "embodied_carbon_tonnes": r["carbon"]["total_t"],
        "material_swaps": [f"{s['change']}: saves {s['saving_t']} t CO2e ({s['pct']}%)" for s in r["carbon"]["swaps"]],
        "note": "Embodied carbon uses indicative factors; window targets follow India's National Building Code guidance for the climate zone.",
    }


class GreenCheck(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid, floors = sly_data.get("project_id"), sly_data.get("editor_floors")
        if not pid:
            return _error("No project is attached to this conversation.")
        if not floors:
            return _error("The plan was not provided; ask the user to open the plan's Services page.")
        city = str(args.get("city") or "").strip()
        facing = str(args.get("entrance_facing") or "").strip().lower().replace("-", "_").replace(" ", "_") or None
        try:
            g = await asyncio.to_thread(services.green_check, pid, floors, city, facing=facing,
                                        editor_project_id=sly_data.get("editor_project_id"), actor="agent:sustainability_advisor")
        except services.ServiceError as err:
            return _error(err.message)
        sly_data["green_id"] = g["id"]
        return json.dumps(summarize(g))


class GreenFacts(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid = sly_data.get("project_id")
        if not pid:
            return _error("No project is attached to this conversation.")
        gid = sly_data.get("green_id")
        if not gid:
            editor = sly_data.get("editor_project_id")
            listed = [x for x in await asyncio.to_thread(store.list_green, pid) if editor is None or x.get("editor_project_id") == editor]
            if not listed:
                return _error("No green check exists for this plan yet. Ask which city the home is in and use green_check.")
            gid = listed[-1]["id"]
        return json.dumps(summarize(await asyncio.to_thread(store.get_green, pid, gid)))
