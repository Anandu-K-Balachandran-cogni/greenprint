"""Coded tools for the cost_estimator agent network (Neuro-SAN).

sly_data (never shown to the LLM):
  project_id, routing_source   the project and its saved plan
  estimate_id                  set by estimate_cost; read by cost_facts
"""

import asyncio
import json
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from .. import services, store
from ..electrical import RoutingSource
from .plumbing_tools import _error


def summarize(e: dict) -> dict:
    lakh = lambda v: round(v / 1e5, 2)  # noqa: E731
    return {"estimate_id": e["id"], "tier": e["tier"], "location": e["location"], "total_lakh_inr": lakh(e["total"]),
            "per_sqft_inr": e["per_sqft"], "built_up_sqft": e["quantities"]["built_up_sqft"],
            "benchmark": e["benchmark"], "by_group_lakh_inr": {k: lakh(v) for k, v in e["groups"].items()},
            "largest_items": [f"{x['item']}: ₹{lakh(x['amount'])} lakh ({x['quantity']} {x['unit']} × ₹{x['rate']:,})"
                              for x in sorted(e["lines"], key=lambda x: -x["amount"])[:6]],
            "contingency_lakh_inr": lakh(e["contingency"]), "services_current": e.get("services_current"),
            "assumptions": e["assumptions"]}


class EstimateCost(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid, raw = sly_data.get("project_id"), sly_data.get("routing_source")
        if not pid:
            return _error("No project is attached to this conversation.")
        if not raw:
            return _error("The saved plan was not provided; ask the user to open the plan's Services page.")
        tier = str(args.get("tier") or "standard").lower()
        location = str(args.get("location") or "non_metro").lower().replace("-", "_").replace(" ", "_")
        try:
            e = await asyncio.to_thread(services.create_estimate, pid, RoutingSource.model_validate(raw), tier=tier,
                                        location=location, actor="agent:cost_estimator")
        except services.ServiceError as err:
            return _error(err.message)
        sly_data["estimate_id"] = e["id"]
        return json.dumps(summarize(e))


class CostFacts(CodedTool):
    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid = sly_data.get("project_id")
        if not pid:
            return _error("No project is attached to this conversation.")
        eid = sly_data.get("estimate_id")
        editor = (sly_data.get("routing_source") or {}).get("editor_project_id")
        if not eid:
            listed = [x for x in await asyncio.to_thread(store.list_estimates, pid) if editor is None or x.get("editor_project_id") == editor]
            if not listed:
                return _error("No cost estimate exists for this plan yet. Use estimate_cost first.")
            eid = listed[-1]["id"]
        e = await asyncio.to_thread(store.get_estimate, pid, eid)
        out = summarize(e)
        out["all_lines"] = [f"{x['group']} · {x['item']}: {x['quantity']} {x['unit']} × ₹{x['rate']:,} = ₹{x['amount']:,}" for x in e["lines"]]
        src_hash = (sly_data.get("routing_source") or {}).get("source_hash")
        out["matches_saved_plan"] = src_hash is None or src_hash == e["source_hash"]
        return json.dumps(out)
