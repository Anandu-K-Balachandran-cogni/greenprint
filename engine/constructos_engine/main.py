"""HTTP entry point for the engine."""

import json
import logging
import re
import os
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from . import brief as brief_mod
from . import gemini, store
from . import program as program_mod
from . import solver as solver_mod
from . import ai_schemas, electrical, exports, jobs, ledger, plumbing, services
from .feasibility import assess
from .layout_checks import check_allocation
from .config import load_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
@asynccontextmanager
async def _lifespan(_app):
    """Load the agent networks in the background so the first chat does not pay the start-up cost."""
    if os.getenv("CONSTRUCTOS_AGENT_WARMUP", "1") == "1" and load_settings().ai_configured:
        import threading
        from .agents import runtime
        threading.Thread(target=runtime.warm, name="agent-warmup", daemon=True).start()
    yield


app = FastAPI(title="Greenprint engine", version="0.1.0", lifespan=_lifespan)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@app.middleware("http")
async def _usage_context(request, call_next):
    """Tag Gemini usage with the feature (path without ids) and the project it served."""
    from . import usage
    parts = request.url.path.strip("/").split("/")
    pid = parts[1] if len(parts) > 1 and parts[0] == "projects" and _UUID.fullmatch(parts[1]) else None
    purpose = _UUID.sub("{id}", "/".join(parts[2:] if pid else parts)) or "root"
    token = usage.current.set({"project_id": pid, "purpose": purpose})
    try:
        return await call_next(request)
    finally:
        usage.current.reset(token)


@app.get("/ai/usage")
def ai_usage(project_id: str | None = None) -> dict:
    """Gemini calls, tokens, latency and estimated cost by feature (all projects, or one)."""
    from . import usage
    return usage.summary(project_id)


@app.exception_handler(services.ServiceError)
def _service_error(_, exc: services.ServiceError):
    from fastapi.responses import JSONResponse
    detail = exc.message if exc.code == "NOT_FOUND" else {"code": exc.code, "message": exc.message}
    return JSONResponse(status_code=exc.status, content={"detail": detail})


@app.exception_handler(ai_schemas.AIOutputInvalid)
def _ai_output_invalid(_, exc: ai_schemas.AIOutputInvalid):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=502, content={"detail": {
        "code": "AI_OUTPUT_INVALID", "message": "Something went wrong reading the answer; nothing was changed. Please try again."}})


@app.get("/health")
def health() -> dict:
    s = load_settings()
    status = gemini.backend_status() if s.ai_configured else {"active": None, "switched_for_s": 0}
    from .agents import runtime
    return {"status": "ok", "ai_configured": s.ai_configured, "ai_backend": s.ai_backend,
            "ai_active": status["active"], "ai_switched_for_s": status["switched_for_s"], "agents": runtime.status(),
            "interior_solver": _solver_ok()}


def _solver_ok() -> bool:
    from .interior import solver
    try:
        return bool(solver.node()) and solver.SOLVER.exists()
    except solver.SolverError:
        return False


@app.get("/ai/models")
def ai_models() -> dict:
    try:
        choice = gemini.select_models()
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"reasoning": choice.reasoning, "fast": choice.fast, "source": choice.source}


@app.post("/ai/smoke")
def ai_smoke() -> dict:
    try:
        return gemini.tool_smoke()
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))


# --- Projects and briefs ----------------------------------------------------

class CreateProject(BaseModel):
    name: str = Field("Untitled building", max_length=120)


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ConfirmRequest(BaseModel):
    brief: brief_mod.Brief
    expected_version: int = Field(ge=0)


def _project(project_id: str) -> dict:
    try:
        return store.get(project_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Project not found")


def _summary(project: dict) -> dict:
    current = store.current_brief(project)
    return {
        "id": project["id"],
        "name": project["name"],
        "created_at": project["created_at"],
        "brief_draft": project["brief_draft"],
        "brief": current,
        "brief_version": current["version"] if current else 0,
        "site": project.get("site"),
        "layout_runs": [
            {"id": r["id"], "created_at": r["created_at"], "brief_version": r["brief_version"],
             "status": r.get("status", "complete"),
             "statuses": [c["allocation"]["status"] for c in r["candidates"]],
             "kind": r.get("kind", "generation"), "request": r.get("request"), "parent": r.get("parent")}
            for r in store.list_layout_runs(project["id"])
        ],
    }


@app.get("/projects")
def list_projects() -> list[dict]:
    return store.list_all()


@app.post("/projects", status_code=201)
def create_project(body: CreateProject) -> dict:
    return _summary(store.create(body.name))


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return _summary(_project(project_id))


@app.post("/projects/{project_id}/brief/extract")
def extract_brief(project_id: str, body: ExtractRequest) -> dict:
    _project(project_id)
    try:
        draft = brief_mod.extract(body.text)
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    store.save_draft(project_id, draft)
    ledger.record(project_id, "brief", "extracted", f"Requirements read from the brief: {len(draft.rooms)} rooms, "
                  f"{draft.storeys or '?'} floor(s).")
    return draft.model_dump(mode="json")


class SketchImage(BaseModel):
    data: str = Field(min_length=8, max_length=4_200_000, description="base64")
    mime: Literal["image/jpeg", "image/png", "image/webp"]


class SketchRequest(BaseModel):
    text: str | None = Field(default=None, max_length=8000)
    images: list[SketchImage] = Field(min_length=1, max_length=3)


@app.post("/projects/{project_id}/brief/read")
def read_sketch(project_id: str, body: SketchRequest) -> dict:
    """Read a hand sketch or plot drawing (plus any typed words, any language) into an English brief to review."""
    import base64
    import binascii
    from . import intake
    _project(project_id)
    images = []
    for i, img in enumerate(body.images):
        try:
            data = base64.b64decode(img.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail={"code": "IMAGE_INVALID", "message": f"Image {i + 1} is not valid base64."})
        if len(data) > intake.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "IMAGE_TOO_LARGE", "message": f"Image {i + 1} is larger than 3 MB."})
        images.append((data, img.mime))
    try:
        reading = intake.read(body.text, images)
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    ledger.record(project_id, "brief", "read_sketch", f"Read {len(images)} image(s){' and typed words (' + reading.language + ')' if body.text else ''}: "
                  + "; ".join(reading.image_notes)[:240])
    return reading.model_dump()


@app.post("/projects/{project_id}/brief/check")
def check_brief(project_id: str, body: brief_mod.Brief) -> dict:
    """Re-run deterministic checks on a user-edited brief without saving it."""
    _project(project_id)
    checked = brief_mod.apply_checks(brief_mod.finalize_confirmed(body))
    return {"brief": checked.model_dump(mode="json"), "errors": brief_mod.validate_for_confirmation(checked),
            "feasibility": assess(checked).model_dump(mode="json")}


@app.post("/projects/{project_id}/brief/confirm")
def confirm_brief(project_id: str, body: ConfirmRequest) -> dict:
    _project(project_id)
    final = brief_mod.apply_checks(brief_mod.finalize_confirmed(body.brief))
    errors = brief_mod.validate_for_confirmation(final)
    if errors:
        raise HTTPException(status_code=422, detail={"code": "BRIEF_INCOMPLETE", "errors": errors})
    try:
        entry = store.confirm_brief(project_id, final, body.expected_version)
    except store.VersionConflict as e:
        raise HTTPException(status_code=409, detail={"code": "BRIEF_VERSION_CONFLICT", "message": str(e)})
    ledger.record(project_id, "brief", "confirmed", f"Brief v{entry['version']} confirmed.", brief_version=entry["version"])
    return entry


class LayoutRequest(BaseModel):
    alternatives: int = Field(2, ge=1, le=3)
    time_limit_s: float = Field(25, ge=2, le=120)
    expected_brief_version: int = Field(ge=1)


def _solve_alternatives(program, brief, alternatives: int, time_limit_s: float, actor: str = "user"):
    def work(job: jobs.Job) -> None:
        found = []
        for k in range(alternatives):
            allocation = solver_mod.solve(program, brief.plot.width_mm, brief.plot.depth_mm, seed=k + 1,
                                          alternative=k, avoid=found, time_limit_s=time_limit_s)
            report = check_allocation(allocation, program) if allocation.footprint else None
            candidate = {"allocation": allocation.model_dump(mode="json"),
                         "report": report.model_dump(mode="json") if report else None}

            def publish(run, candidate=candidate, done=k + 1):
                run["candidates"].append(candidate)
                run["job"]["progress"]["done"] = done
            job.update(publish)
            if allocation.footprint is None:
                break  # a further alternative cannot exist if the first search failed
            found.append(allocation)
        statuses = ", ".join(a.status for a in found) or "none solved"
        ledger.record(job.project_id, "layouts", "ready", f"{len(found)} layout option(s) solved ({statuses}).", actor=actor, run_id=job.run_id)
    return work


@app.post("/projects/{project_id}/layouts", status_code=202)
def generate_layouts(project_id: str, body: LayoutRequest) -> dict:
    """Queue fresh layout solving from the confirmed brief; poll the run for progress and results.

    No template or saved plan is used."""
    project = _project(project_id)
    current = store.current_brief(project)
    if current is None:
        raise HTTPException(status_code=409, detail={"code": "BRIEF_NOT_CONFIRMED", "message": "Confirm the brief first."})
    if current["version"] != body.expected_brief_version:
        raise HTTPException(status_code=409, detail={
            "code": "BRIEF_VERSION_CONFLICT",
            "message": f"Brief v{current['version']} is current; this request was for v{body.expected_brief_version}."})
    brief = brief_mod.Brief.model_validate(current["brief"])
    try:
        program = program_mod.build_program(brief)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "PROGRAMME_INVALID", "message": str(e)})
    run = {
        "id": str(uuid.uuid4()),
        "created_at": store._now(),
        "status": "queued",
        "job": jobs.new_job(body.alternatives),
        "brief_version": current["version"],
        "brief_hash": current["hash"],
        "program": program.model_dump(mode="json"),
        "candidates": [],
    }
    store.save_layout_run(project_id, run)
    jobs.start(project_id, run, _solve_alternatives(program, brief, body.alternatives, body.time_limit_s))
    ledger.record(project_id, "layouts", "queued", f"Solving {body.alternatives} layout option(s) for brief v{current['version']}.", run_id=run["id"])
    return _public_run(run)


def _public_run(run: dict) -> dict:
    """A run without its lease: the lease is the worker's write authority and never leaves the engine."""
    job = run.get("job")
    return {**run, "status": run.get("status", "complete"),
            "job": {k: v for k, v in job.items() if k != "lease"} if job else None}


@app.get("/projects/{project_id}/layouts/{run_id}")
def get_layout_run(project_id: str, run_id: str) -> dict:
    _project(project_id)
    try:
        return _public_run(jobs.reconcile(project_id, store.get_layout_run(project_id, run_id)))
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Layout run not found")


@app.post("/projects/{project_id}/layouts/{run_id}/candidates/{k}/review")
def review_candidate(project_id: str, run_id: str, k: int) -> dict:
    """Read-only Gemini review; it cannot change check results or the layout."""
    _project(project_id)
    try:
        return services.review_option(project_id, run_id, k)
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))


class ReviseRequest(BaseModel):
    request: str | None = Field(None, max_length=1000)
    changes: list[program_mod.Change] | None = None


@app.post("/projects/{project_id}/layouts/{run_id}/candidates/{k}/revise")
def revise_candidate(project_id: str, run_id: str, k: int, body: ReviseRequest) -> dict:
    """Turn a request (or a reviewer suggestion) into typed changes, then re-solve close to this option."""
    _project(project_id)
    if body.changes:
        run = services.queue_revision(project_id, run_id, k, body.changes, request=body.request,
                                      explanation="Applied the selected suggestion.")
        return {"status": "ok", "run": _public_run(run)}
    if not (body.request and body.request.strip()):
        raise HTTPException(status_code=422, detail={"code": "EMPTY_REQUEST", "message": "Describe the change you want."})
    try:
        interpretation = services.interpret_request(project_id, run_id, k, body.request.strip())
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    if interpretation["clarification"] or not interpretation["changes"]:
        return {"status": "clarify", "question": interpretation["clarification"] or
                "I could not turn that into a change to the plan. Could you say which room and what should change?",
                "rejected": interpretation["rejected"]}
    run = services.queue_revision(project_id, run_id, k, interpretation["changes"], request=body.request,
                                  explanation=interpretation["explanation"], rejected=interpretation["rejected"],
                                  interpreted_by=interpretation["model"])
    return {"status": "ok", "run": _public_run(run)}


class ElectricalRequest(BaseModel):
    source: electrical.RoutingSource
    db_room: str | None = Field(None, max_length=120)
    preferences: str | None = Field(None, max_length=1000)


_check_source = services.check_source


@app.post("/projects/{project_id}/diagrams/electrical", status_code=201)
def create_electrical(project_id: str, body: ElectricalRequest) -> dict:
    """Generate an electrical diagram from the saved architecture the browser sends."""
    _project(project_id)
    _check_source(body.source)
    extras, clarification, rejected = [], None, []
    if body.preferences and body.preferences.strip():
        try:
            extras, clarification, rejected = electrical.interpret_preferences(body.preferences.strip(), body.source)
        except gemini.AINotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
    return services.generate_electrical(project_id, body.source, db_room=body.db_room, extras=extras,
                                        preferences={"text": body.preferences, "clarification": clarification, "rejected": rejected})


class PlumbingRequest(BaseModel):
    source: electrical.RoutingSource
    entry_room: str | None = Field(None, max_length=120)
    exit_room: str | None = Field(None, max_length=120)


@app.post("/projects/{project_id}/diagrams/plumbing", status_code=201)
def create_plumbing(project_id: str, body: PlumbingRequest) -> dict:
    """Generate supply and directed drainage diagrams from the saved architecture."""
    _project(project_id)
    _check_source(body.source)
    return services.generate_plumbing(project_id, body.source, entry_room=body.entry_room, exit_room=body.exit_room)


class AgentLayout(BaseModel):
    run_id: str = Field(max_length=80)
    candidate: int = Field(0, ge=0, le=9)


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    source: electrical.RoutingSource | None = None
    layout: AgentLayout | None = None
    interior: "InteriorScope | None" = None
    floors: list[dict] | None = Field(default=None, max_length=8, description="The editor model's floors (for the sustainability advisor)")
    chat_context: dict | None = None


class InteriorScope(BaseModel):
    """The editor floor as the browser holds it (editor state is not on the server) and the rooms in scope."""
    floor: dict
    room_ids: list[str] | None = Field(default=None, max_length=60)
    furnish: bool = True
    home_has_pooja: bool = False
    editor_project_id: str | None = Field(default=None, max_length=120)


AgentChatRequest.model_rebuild()

AGENT_NETWORKS = {"constructos": "constructos", "architect": "architect", "electrical": "electrical_engineer",
                  "plumbing": "plumbing_engineer", "cost": "cost_estimator", "interior": "interior_designer",
                  "green": "sustainability_advisor"}


@app.post("/projects/{project_id}/agents/{agent}/chat")
def agent_chat(project_id: str, agent: str, body: AgentChatRequest) -> dict:
    """One turn with a Neuro-SAN agent network. The plan and the open option travel as sly_data (never shown
    to the LLM); the browser keeps chat_context to continue, and every result is stored with the project."""
    _project(project_id)
    network = AGENT_NETWORKS.get(agent)
    if network is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    sly: dict = {"project_id": project_id}
    if body.source is not None:
        _check_source(body.source)
        sly["routing_source"] = body.source.model_dump(mode="json")
    if body.layout is not None:
        try:
            store.get_layout_run(project_id, body.layout.run_id)
        except store.NotFound:
            raise HTTPException(status_code=404, detail="Layout run not found")
        sly["layout"] = body.layout.model_dump()
    if body.interior is not None:
        if len(json.dumps(body.interior.floor)) > 2_000_000:
            raise HTTPException(status_code=413, detail={"code": "FLOOR_TOO_LARGE", "message": "This floor is too large to design at once."})
        sly["interior"] = body.interior.model_dump()
    if body.floors is not None:
        if len(json.dumps(body.floors)) > 4_000_000:
            raise HTTPException(status_code=413, detail={"code": "PLAN_TOO_LARGE", "message": "This plan is too large to send at once."})
        sly["editor_floors"] = body.floors
        if body.source is not None:
            sly["editor_project_id"] = body.source.editor_project_id
    if body.chat_context and len(json.dumps(body.chat_context)) > 200_000:
        raise HTTPException(status_code=413, detail={"code": "CONTEXT_TOO_LARGE", "message": "Start a new conversation."})
    from .agents import runtime
    try:
        result = runtime.chat(network, body.message.strip(), sly, body.chat_context)
    except Exception as e:  # noqa: BLE001 - surface agent failures as a clean 502
        logging.getLogger(__name__).exception("agent %s failed", network)
        raise HTTPException(status_code=502, detail={"code": "AGENT_FAILED", "message": "Greenprint could not answer just now. Please try again."})
    if not result["answer"]:
        raise HTTPException(status_code=502, detail={"code": "AGENT_NO_ANSWER", "message": "Greenprint could not answer just now. Please try again."})
    out = result["sly_data"] or {}
    return {"answer": result["answer"], "chat_context": result["chat_context"],
            "diagrams": {k: out[f"{k}_diagram_id"] for k in ("plumbing", "electrical") if out.get(f"{k}_diagram_id")},
            "estimate_id": out.get("estimate_id"), "green_id": out.get("green_id"),
            "design": _design_or_none(project_id, out.get("design_id")),
            "revision_run_id": out.get("revision_run_id"), "tools_called": result["tools_called"], "seconds": result["seconds"]}


def _design_or_none(project_id: str, design_id: str | None) -> dict | None:
    if not design_id:
        return None
    try:
        return store.get_design(project_id, design_id)
    except store.NotFound:
        return None


@app.get("/projects/{project_id}/designs")
def list_designs(project_id: str, editor: str | None = None) -> list[dict]:
    _project(project_id)
    return [d for d in store.list_designs(project_id) if editor is None or d.get("editor_project_id") == editor]


@app.get("/projects/{project_id}/designs/{design_id}")
def get_design(project_id: str, design_id: str) -> dict:
    _project(project_id)
    try:
        return store.get_design(project_id, design_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Design not found")


class RenderRequest(BaseModel):
    image: str = Field(min_length=8, max_length=6_000_000, description="base64 capture of the 3D view")
    mime: Literal["image/jpeg", "image/png"] = "image/jpeg"
    kind: Literal["room", "view"] = "room"
    room: str | None = Field(default=None, max_length=120)
    style: str | None = Field(default=None, max_length=600)
    editor_project_id: str | None = Field(default=None, max_length=120)


@app.post("/projects/{project_id}/renders", status_code=201)
def create_render(project_id: str, body: RenderRequest) -> dict:
    """Photo-real render of a captured 3D view with Gemini image generation (a picture only; the model is unchanged)."""
    import base64
    import binascii
    import uuid as _uuid
    from . import render as render_mod
    _project(project_id)
    if len(store.list_renders(project_id)) >= render_mod.MAX_RENDERS_PER_PROJECT:
        raise HTTPException(status_code=429, detail={"code": "RENDER_LIMIT", "message": "This project has reached its render limit."})
    try:
        image = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail={"code": "IMAGE_INVALID", "message": "The captured view is not valid base64."})
    try:
        data, mime, model = render_mod.render(image, body.mime, body.kind, body.style)
    except gemini.AINotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001 - a failed generation is reported, never fatal
        logging.getLogger(__name__).exception("render failed")
        raise HTTPException(status_code=502, detail={"code": "RENDER_FAILED", "message": "This view could not be rendered. Please try again."})
    meta = {"id": str(_uuid.uuid4()), "created_at": store._now(), "kind": body.kind, "room": body.room, "model": model,
            "editor_project_id": body.editor_project_id}
    doc = store.save_render(project_id, meta, data, mime)
    ledger.record(project_id, "interiors", "rendered", f"Photo-real render of {body.room or 'the 3D view'} with {model}.", render_id=meta["id"])
    return {k: doc[k] for k in ("id", "created_at", "kind", "room", "model", "mime")} | {"image_path": f"projects/{project_id}/renders/{meta['id']}/image"}


@app.get("/projects/{project_id}/renders")
def list_renders(project_id: str, editor: str | None = None) -> list[dict]:
    _project(project_id)
    return [{k: d.get(k) for k in ("id", "created_at", "kind", "room", "model")} | {"image_path": f"projects/{project_id}/renders/{d['id']}/image"}
            for d in store.list_renders(project_id) if editor is None or d.get("editor_project_id") == editor]


@app.get("/projects/{project_id}/renders/{render_id}/image")
def render_image(project_id: str, render_id: str) -> Response:
    _project(project_id)
    try:
        doc, data = store.get_render(project_id, render_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Render not found")
    return Response(content=data, media_type=doc["mime"], headers={"cache-control": "private, max-age=86400"})


class SiteRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    address: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=80)
    country: str = Field(default="", max_length=80)


@app.put("/projects/{project_id}/site")
def set_site(project_id: str, body: SiteRequest) -> dict:
    """The plot's location, pinned on Google Maps; the green check uses it for local climate and sun."""
    _project(project_id)
    site = body.model_dump() | {"pinned_at": store._now()}
    store.set_site(project_id, site)
    ledger.record(project_id, "site", "pinned", f"Plot pinned at {body.address or f'{body.lat:.5f}, {body.lng:.5f}'}.")
    return site


class GreenRequest(BaseModel):
    floors: list[dict] = Field(min_length=1, max_length=8)
    city: str | None = Field(default=None, max_length=80)
    facing: Literal["north", "north_east", "east", "south_east", "south", "south_west", "west", "north_west"] | None = None
    editor_project_id: str | None = Field(default=None, max_length=120)


@app.post("/projects/{project_id}/green", status_code=201)
def green_check(project_id: str, body: GreenRequest) -> dict:
    """Green performance of the editor model: daylight, ventilation, sun, water, solar, carbon; climate grounded per city."""
    _project(project_id)
    if len(json.dumps(body.floors)) > 4_000_000:
        raise HTTPException(status_code=413, detail={"code": "PLAN_TOO_LARGE", "message": "This plan is too large to check at once."})
    try:
        return services.green_check(project_id, body.floors, body.city, facing=body.facing, editor_project_id=body.editor_project_id)
    except services.ServiceError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


@app.get("/projects/{project_id}/green")
def list_green(project_id: str, editor: str | None = None) -> list[dict]:
    _project(project_id)
    return [d for d in store.list_green(project_id) if editor is None or d.get("editor_project_id") == editor]


@app.get("/projects/{project_id}/green/{report_id}")
def get_green(project_id: str, report_id: str) -> dict:
    _project(project_id)
    try:
        return store.get_green(project_id, report_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Green report not found")


class EstimateRequest(BaseModel):
    source: electrical.RoutingSource
    tier: Literal["basic", "standard", "premium"] = "standard"
    location: Literal["non_metro", "metro"] = "non_metro"


@app.post("/projects/{project_id}/estimates", status_code=201)
def create_estimate(project_id: str, body: EstimateRequest) -> dict:
    """Construction cost estimate from the saved plan's quantities and its latest services."""
    _project(project_id)
    return services.create_estimate(project_id, body.source, tier=body.tier, location=body.location)


@app.get("/projects/{project_id}/estimates")
def list_estimates(project_id: str, editor: str | None = None) -> list[dict]:
    _project(project_id)
    return [{k: v for k, v in e.items() if k != "blob"} for e in store.list_estimates(project_id)
            if editor is None or e.get("editor_project_id") == editor]


@app.get("/projects/{project_id}/estimates/{estimate_id}")
def get_estimate(project_id: str, estimate_id: str) -> dict:
    _project(project_id)
    try:
        return store.get_estimate(project_id, estimate_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Estimate not found")


@app.get("/projects/{project_id}/status")
def project_status(project_id: str, source_hash: str | None = None, editor: str | None = None) -> dict:
    """Where the project stands and what comes next (artifacts + ledger) — the basis for resuming anywhere."""
    _project(project_id)
    return ledger.status(project_id, source_hash=source_hash, editor_project_id=editor)


@app.get("/projects/{project_id}/ledger")
def project_ledger(project_id: str) -> list[dict]:
    _project(project_id)
    return ledger.steps(project_id)


@app.get("/projects/{project_id}/diagrams")
def list_diagrams(project_id: str, editor: str | None = None) -> list[dict]:
    _project(project_id)
    return [{k: v for k, v in d.items() if k != "blob"} for d in store.list_diagrams(project_id)
            if editor is None or d["editor_project_id"] == editor]


@app.get("/projects/{project_id}/diagrams/{diagram_id}")
def get_diagram(project_id: str, diagram_id: str) -> dict:
    _project(project_id)
    try:
        return store.get_diagram(project_id, diagram_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Diagram not found")


ExportFormat = Literal["pdf", "dxf"]
MEDIA = {"pdf": "application/pdf", "dxf": "image/vnd.dxf"}


def _export(project_id: str, payload: dict, fmt: str, project_name: str, artifact: str) -> Response:
    data = exports.render_pdf(payload, project_name) if fmt == "pdf" else exports.render_dxf(payload, project_name)
    store.save_artifact(project_id, artifact, data, MEDIA[fmt])  # kept with the project's evidence
    ledger.record(project_id, "drawings", "exported", f"{exports.KIND_TITLE[payload['kind']]} exported as {fmt.upper()}.",
                  artifact=artifact, source_hash=payload["source_hash"])
    name = exports.filename(payload, fmt)
    return Response(content=data, media_type=MEDIA[fmt], headers={"content-disposition": f'attachment; filename="{name}"'})


@app.get("/projects/{project_id}/diagrams/{diagram_id}/export/{fmt}")
def export_diagram(project_id: str, diagram_id: str, fmt: ExportFormat) -> Response:
    """Scaled PDF sheets or a layered DXF of a stored diagram, with its source fingerprint."""
    project = _project(project_id)
    try:
        diagram = store.get_diagram(project_id, diagram_id)
    except store.NotFound:
        raise HTTPException(status_code=404, detail="Diagram not found")
    return _export(project_id, diagram, fmt, project["name"], f"{diagram_id}.{fmt}")


class PlanExportRequest(BaseModel):
    source: electrical.RoutingSource


@app.post("/projects/{project_id}/exports/plan/{fmt}")
def export_plan(project_id: str, fmt: ExportFormat, body: PlanExportRequest) -> Response:
    """Architecture-only drawing of the saved editor model the browser sends."""
    project = _project(project_id)
    _check_source(body.source)
    payload = exports.plan_payload(body.source.model_dump(mode="json"), project["name"])
    return _export(project_id, payload, fmt, project["name"], f"plan-{body.source.source_hash[7:23]}.{fmt}")


def run() -> None:
    import uvicorn

    uvicorn.run("constructos_engine.main:app", host=os.getenv("ENGINE_HOST", "127.0.0.1"), port=load_settings().port)
