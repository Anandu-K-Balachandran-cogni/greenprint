"""Coded tools for the architect agent network, plus the shared project_status tool.

sly_data (never shown to the LLM):
  project_id        engine project id
  layout            {"run_id", "candidate"} — the option the user has open (optional)
  routing_source    the saved editor plan (optional; used by project_status)
  revision_run_id   set by request_change; read by layout_status
"""

import asyncio
import json
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from .. import ledger, services, store
from ..review import build_facts
from .plumbing_tools import _error


def _option(sly: dict) -> tuple[str, int] | None:
    pid = sly["project_id"]
    layout = sly.get("layout") or {}
    if layout.get("run_id") is not None:
        return layout["run_id"], int(layout.get("candidate") or 0)
    runs = [r for r in store.list_layout_runs(pid) if r.get("status", "complete") == "complete" and r["candidates"]]
    gens = [r for r in runs if r.get("kind", "generation") == "generation"] or runs
    return (gens[-1]["id"], 0) if gens else None


def _context(sly: dict):
    if not sly.get("project_id"):
        raise services.ServiceError(400, "NO_PROJECT", "No project is attached to this conversation.")
    opt = _option(sly)
    if opt is None:
        raise services.ServiceError(409, "NO_LAYOUT", "No layout has been generated yet. Generate layouts first.")
    return opt, services.option_context(sly["project_id"], *opt)


class LayoutFacts(CodedTool):
    """Measured facts about the current layout option (rooms, sizes, adjacency, checks, metrics)."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            (run_id, k), c = await asyncio.to_thread(_context, sly_data)
        except services.ServiceError as e:
            return _error(e.message)
        facts = build_facts(c.brief, c.program, c.allocation, c.report)
        return json.dumps({"option": k + 1, "run_id": run_id, "facts": {f.id: f.text for f in facts}})


class ReviewLayout(CodedTool):
    """Run the grounded architectural review of the current option and store it."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            (run_id, k), _ = await asyncio.to_thread(_context, sly_data)
            r = await asyncio.to_thread(services.review_option, sly_data["project_id"], run_id, k, "agent:architect")
        except services.ServiceError as e:
            return _error(e.message)
        return json.dumps({"option": k + 1, "summary": r["summary"],
                           "strengths": [x["statement"] for x in r["strengths"]],
                           "concerns": [f"[{x['severity']}] {x['statement']}" for x in r["concerns"]],
                           "suggestions": [x["statement"] for x in r["suggestions"]]})


class RequestChange(CodedTool):
    """Turn the homeowner's words into checked plan changes and re-plan the option in the background."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        text = str(args.get("request") or "").strip()
        if not text:
            return _error("Say what should change, for example 'make the kids bedroom at least 14 m²'.")
        try:
            (run_id, k), _ = await asyncio.to_thread(_context, sly_data)
            pid = sly_data["project_id"]
            interp = await asyncio.to_thread(services.interpret_request, pid, run_id, k, text[:1000])
            if interp["clarification"] or not interp["changes"]:
                return json.dumps({"clarification": interp["clarification"] or "Which room should change, and how?",
                                   "not_applied": interp["rejected"]})
            run = await asyncio.to_thread(lambda: services.queue_revision(
                pid, run_id, k, interp["changes"], request=text, explanation=interp["explanation"],
                rejected=interp["rejected"], interpreted_by=interp["model"], actor="agent:architect"))
        except services.ServiceError as e:
            return _error(e.message)
        sly_data["revision_run_id"] = run["id"]
        return json.dumps({"queued": True, "changes": run["change_descriptions"], "not_applied": interp["rejected"],
                           "note": "The revised layout is being solved now (about 30 seconds); it appears under Generated layouts."})


class LayoutStatus(CodedTool):
    """Whether a revision is ready, and how its rooms compare with the original option."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid = sly_data.get("project_id")
        run_id = sly_data.get("revision_run_id")
        if not pid or not run_id:
            return _error("No re-planning has been requested in this conversation.")
        run = await asyncio.to_thread(store.get_layout_run, pid, run_id)
        out: dict[str, Any] = {"status": run.get("status"), "changes": run.get("change_descriptions")}
        if run.get("status") == "complete" and run["candidates"]:
            after = run["candidates"][0]
            parent = await asyncio.to_thread(store.get_layout_run, pid, run["parent"]["run_id"])
            before = {s["id"]: s for s in parent["candidates"][run["parent"]["candidate"]]["allocation"]["spaces"]}
            out["solver"] = after["allocation"]["status"]
            out["checks"] = (after.get("report") or {}).get("status")
            out["room_changes"] = [f"{s['name']}: {before[s['id']]['area_m2']} → {s['area_m2']} m²"
                                   for s in after["allocation"]["spaces"] if s["id"] in before and abs(before[s["id"]]["area_m2"] - s["area_m2"]) >= 0.3]
        return json.dumps(out)


class ProjectStatus(CodedTool):
    """Where the project stands and what comes next — read this first in any new conversation."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        pid = sly_data.get("project_id")
        if not pid:
            return _error("No project is attached to this conversation.")
        src = sly_data.get("routing_source") or {}
        try:
            st = await asyncio.to_thread(ledger.status, pid, src.get("source_hash"), src.get("editor_project_id"))
        except store.NotFound:
            return _error("The project was not found.")
        return json.dumps(st)
