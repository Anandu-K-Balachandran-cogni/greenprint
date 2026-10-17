"""Project ledger: an append-only record of every step, by people (the app) and agents.

Together with the stored artifacts (brief versions, layout runs, diagrams) it lets any
session — a person on another device, an agent in a new conversation, another server —
see exactly where a project is and continue from there ("restart from anywhere").
"""

import uuid
from typing import Any

from . import store


def record(project_id: str, stage: str, action: str, summary: str, *, actor: str = "user", **refs: Any) -> dict:
    step = {"id": str(uuid.uuid4()), "created_at": store._now(), "stage": stage, "action": action,
            "actor": actor, "summary": summary[:300], "refs": {k: v for k, v in refs.items() if v is not None}}
    store.backend().put(f"projects/{store._check_id(project_id)}/ledger", step["id"], step)
    return step


def steps(project_id: str) -> list[dict]:
    return sorted(store.backend().list(f"projects/{store._check_id(project_id)}/ledger"), key=lambda s: s["created_at"])


def status(project_id: str, source_hash: str | None = None, editor_project_id: str | None = None) -> dict:
    """Where the project stands, from stored artifacts (authoritative) plus the ledger (history)."""
    project = store.get(project_id)
    brief = store.current_brief(project)
    runs = store.list_layout_runs(project_id)
    gens = [r for r in runs if r.get("kind", "generation") == "generation"]
    revs = [r for r in runs if r.get("kind") == "revision"]

    def run_view(r: dict) -> dict:
        return {"run_id": r["id"], "kind": r.get("kind", "generation"), "status": r.get("status", "complete"),
                "brief_version": r["brief_version"], "request": r.get("request"),
                "options": [{"candidate": k, "solver": c["allocation"]["status"],
                             "checks": (c.get("report") or {}).get("status"), "reviewed": bool(c.get("review"))}
                            for k, c in enumerate(r["candidates"])]}

    diagrams = {}
    for d in store.list_diagrams(project_id):
        if editor_project_id and d["editor_project_id"] != editor_project_id:
            continue
        diagrams[d["kind"]] = {"diagram_id": d["id"], "status": d["status"], "created_at": d["created_at"],
                               "connected": f"{d['endpoints_connected']}/{d['endpoints_total']}",
                               "matches_saved_plan": None if source_hash is None else d["source_hash"] == source_hash}
    est = [e for e in store.list_estimates(project_id) if not editor_project_id or e.get("editor_project_id") == editor_project_id]
    estimate = ({"estimate_id": est[-1]["id"], "tier": est[-1]["tier"], "total_inr": est[-1]["total"], "per_sqft_inr": est[-1]["per_sqft"],
                 "matches_saved_plan": None if source_hash is None else est[-1]["source_hash"] == source_hash} if est else None)
    history = steps(project_id)
    if project["brief_draft"] is None and brief is None:
        nxt = "Describe the home in plain words (Requirements page)."
    elif brief is None:
        nxt = "Review and confirm the requirements."
    elif not gens:
        nxt = "Generate layout options."
    elif any(r.get("status") in ("queued", "running") for r in runs):
        nxt = "Layouts are being solved; wait for them to finish."
    elif not diagrams:
        nxt = "Open an option in the editor, then generate the electrical and plumbing layouts."
    elif any(v["matches_saved_plan"] is False for v in diagrams.values()):
        nxt = "The plan changed since the services were drawn; regenerate them."
    elif len(diagrams) < 2:
        nxt = f"Generate the {'plumbing' if 'electrical' in diagrams else 'electrical'} layout."
    elif estimate is None:
        nxt = "Get a cost estimate for the plan."
    else:
        nxt = "Export the drawings (PDF/DXF) to share with a builder."
    return {"project": project["name"], "brief": {"confirmed_version": brief["version"] if brief else None,
                                                  "draft": project["brief_draft"] is not None},
            "latest_generation": run_view(gens[-1]) if gens else None,
            "revisions": [run_view(r) for r in revs[-3:]], "services": diagrams, "cost_estimate": estimate,
            "recent_steps": [{"at": s["created_at"][:19], "actor": s["actor"], "summary": s["summary"]} for s in history[-8:]],
            "next_step": nxt}
