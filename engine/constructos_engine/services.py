"""Project operations shared by the HTTP API (people) and the agents' coded tools.

Each operation validates its inputs, stores its result and records a ledger step with
who did it, so the app and the agents always see — and can continue — the same project.
"""

import uuid
from dataclasses import dataclass

from . import brief as brief_mod
from . import electrical, jobs, ledger, plumbing, store
from . import program as program_mod
from . import review as review_mod
from . import solver as solver_mod
from .grid import buildable_grid, grid_area_m2
from .layout_checks import CheckReport, check_allocation


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class OptionContext:
    project: dict
    run: dict
    brief: brief_mod.Brief
    program: program_mod.Program
    allocation: solver_mod.Allocation
    report: CheckReport
    buildable_m2: float


def option_context(project_id: str, run_id: str, k: int) -> OptionContext:
    try:
        project = store.get(project_id)
        run = store.get_layout_run(project_id, run_id)
        version = store.brief_version(project, run["brief_version"])
    except store.NotFound:
        raise ServiceError(404, "NOT_FOUND", "Layout run not found")
    if not 0 <= k < len(run["candidates"]):
        raise ServiceError(404, "NOT_FOUND", "Option not found")
    cand = run["candidates"][k]
    allocation = solver_mod.Allocation.model_validate(cand["allocation"])
    if allocation.footprint is None or not cand.get("report"):
        raise ServiceError(409, "NO_LAYOUT", "This option has no layout to review.")
    brief = brief_mod.Brief.model_validate(version["brief"])
    program = program_mod.Program.model_validate(run["program"])
    sb = program.setbacks
    max_w, max_h = buildable_grid(brief.plot.width_mm, brief.plot.depth_mm, sb.front_mm, sb.rear_mm, sb.side_mm)
    return OptionContext(project, run, brief, program, allocation, CheckReport.model_validate(cand["report"]),
                         grid_area_m2(max_w * max_h))


def review_option(project_id: str, run_id: str, k: int, actor: str = "user") -> dict:
    c = option_context(project_id, run_id, k)
    result = review_mod.review(c.brief, c.program, c.allocation, c.report, c.buildable_m2).model_dump(mode="json")
    store.update_candidate(project_id, run_id, k, "review", result)
    ledger.record(project_id, "layouts", "reviewed", f"AI review of option {k + 1}: {result['summary'][:180]}",
                  actor=actor, run_id=run_id, candidate=k)
    return result


def interpret_request(project_id: str, run_id: str, k: int, text: str) -> dict:
    c = option_context(project_id, run_id, k)
    out = review_mod.interpret_request(text, c.brief, c.program, c.allocation, c.report, c.buildable_m2).model_dump()
    out["changes"] = [program_mod.Change.model_validate(x) for x in out["changes"]]
    return out


def queue_revision(project_id: str, run_id: str, k: int, changes: list[program_mod.Change], *, request: str | None,
                   explanation: str, rejected: list[str] | None = None, interpreted_by: str | None = None,
                   actor: str = "user") -> dict:
    """Validate typed changes and re-solve close to option k in the background; returns the queued run."""
    c = option_context(project_id, run_id, k)
    problems = program_mod.validate_changes(changes, c.program, c.buildable_m2)
    if problems:
        raise ServiceError(422, "CHANGE_INVALID", " ".join(problems))
    names = {s.id: s.name for s in c.program.spaces}
    revised = program_mod.build_program(c.brief, c.program.setbacks, list(c.program.changes) + list(changes))
    new_run = {
        "id": str(uuid.uuid4()), "created_at": store._now(), "status": "queued", "job": jobs.new_job(1),
        "kind": "revision", "parent": {"run_id": run_id, "candidate": k}, "request": request,
        "changes": [x.model_dump(mode="json") for x in changes], "change_descriptions": [x.describe(names) for x in changes],
        "explanation": explanation, "rejected": rejected or [], "interpreted_by": interpreted_by,
        "brief_version": c.run["brief_version"], "brief_hash": c.run["brief_hash"],
        "program": revised.model_dump(mode="json"), "candidates": [],
    }
    brief, allocation = c.brief, c.allocation

    def work(job: jobs.Job) -> None:
        alloc = solver_mod.solve(revised, brief.plot.width_mm, brief.plot.depth_mm, seed=k + 11, near=allocation, time_limit_s=25)
        report = check_allocation(alloc, revised) if alloc.footprint else None

        def publish(run):
            run["candidates"].append({"allocation": alloc.model_dump(mode="json"),
                                      "report": report.model_dump(mode="json") if report else None})
            run["job"]["progress"]["done"] = 1
        job.update(publish)
        ledger.record(project_id, "layouts", "revised", f"Revision of option {k + 1} solved ({alloc.status}).",
                      actor=actor, run_id=job.run_id, parent_run_id=run_id)

    store.save_layout_run(project_id, new_run)
    jobs.start(project_id, new_run, work)
    ledger.record(project_id, "layouts", "revision_queued",
                  f"Re-planning option {k + 1}: {'; '.join(new_run['change_descriptions'])}", actor=actor,
                  run_id=new_run["id"], parent_run_id=run_id, candidate=k)
    return new_run


def check_source(source: electrical.RoutingSource) -> None:
    if not source.rooms or len(source.rooms) > 400 or any(len(r.polygon) > 200 or len(r.polygon) < 3 for r in source.rooms):
        raise ServiceError(422, "SOURCE_INVALID", "The plan has no usable rooms.")
    if not source.source_hash.startswith("sha256-"):
        raise ServiceError(422, "SOURCE_INVALID", "Missing source fingerprint.")


def generate_plumbing(project_id: str, source: electrical.RoutingSource, *, entry_room: str | None = None,
                      exit_room: str | None = None, actor: str = "user") -> dict:
    check_source(source)
    d = plumbing.generate(source, entry_room=entry_room, exit_room=exit_room).model_dump(mode="json")
    store.save_diagram(project_id, d)
    m = d["network_metrics"]["drainage"]
    ledger.record(project_id, "services", "plumbing", f"Plumbing generated ({d['status']}; drains {m['endpoints_connected']}/"
                  f"{m['endpoints_total']}).", actor=actor, diagram_id=d["id"], editor_project_id=d["editor_project_id"],
                  source_hash=d["source_hash"])
    return d


def generate_electrical(project_id: str, source: electrical.RoutingSource, *, db_room: str | None = None,
                        extras: list | None = None, preferences: dict | None = None, actor: str = "user") -> dict:
    check_source(source)
    d = electrical.generate(source, db_room=db_room, extras=extras or []).model_dump(mode="json")
    if preferences is not None:
        d["preferences"] = preferences
    store.save_diagram(project_id, d)
    m = d["metrics"]
    ledger.record(project_id, "services", "electrical", f"Electrical generated ({d['status']}; points {m['endpoints_connected']}/"
                  f"{m['endpoints_total']}).", actor=actor, diagram_id=d["id"], editor_project_id=d["editor_project_id"],
                  source_hash=d["source_hash"])
    return d


def latest_diagram(project_id: str, kind: str, editor_project_id: str | None) -> dict | None:
    listed = [d for d in store.list_diagrams(project_id) if d["kind"] == kind
              and (editor_project_id is None or d["editor_project_id"] == editor_project_id)]
    return store.get_diagram(project_id, listed[-1]["id"]) if listed else None


def create_estimate(project_id: str, source: electrical.RoutingSource, *, tier: str = "standard",
                    location: str = "non_metro", actor: str = "user") -> dict:
    """Cost estimate from the saved plan and the latest services for it (estimated where none exist)."""
    from . import costing
    check_source(source)
    if tier not in ("basic", "standard", "premium") or location not in ("non_metro", "metro"):
        raise ServiceError(422, "ESTIMATE_INVALID", "Tier must be basic, standard or premium; location non_metro or metro.")
    el = latest_diagram(project_id, "electrical", source.editor_project_id)
    pl = latest_diagram(project_id, "plumbing", source.editor_project_id)
    est = costing.estimate(source, electrical=el, plumbing=pl, tier=tier, location=location).model_dump(mode="json")
    est.update(id=str(uuid.uuid4()), created_at=store._now(), editor_project_id=source.editor_project_id,
               services_current={k: (d["source_hash"] == source.source_hash) if d else None for k, d in (("electrical", el), ("plumbing", pl))})
    store.save_estimate(project_id, est)
    lakh = est["total"] / 1e5
    ledger.record(project_id, "cost", "estimated", f"Cost estimate ({tier}, {location.replace('_', '-')}): ₹{lakh:.1f} lakh, "
                  f"₹{est['per_sqft']:,}/sq ft ({est['benchmark']['verdict']} the published band).", actor=actor,
                  estimate_id=est["id"], source_hash=source.source_hash)
    return est


def propose_design(project_id: str, floor: dict, brief: dict, *, room_ids: list[str] | None = None, furnish: bool = True,
                   home_has_pooja: bool = False, editor_project_id: str | None = None, tier: str = "standard",
                   actor: str = "user") -> dict:
    """Check and place a design brief with the editor's solver, price what it adds, and keep it with the project."""
    from .interior import costing as interior_costing, solver
    try:
        planned = solver.plan(floor, brief, room_ids, furnish, home_has_pooja)
    except solver.SolverError as e:
        raise ServiceError(502, "SOLVER_FAILED", str(e)) from e
    if not planned.get("ok"):
        raise ServiceError(422, "DESIGN_INVALID", "; ".join(planned.get("problems") or ["The design could not be planned."]))
    design = {
        "id": str(uuid.uuid4()), "created_at": store._now(), "editor_project_id": editor_project_id, "brief": brief,
        "scope": {"room_ids": room_ids, "furnish": furnish, "home_has_pooja": home_has_pooja},
        "plan": {k: planned[k] for k in ("rooms", "skipped", "issues", "added", "restyled", "summary")},
        "budget": interior_costing.budget(planned["added"], planned["rooms"], tier),
    }
    store.save_design(project_id, design)
    rooms = ", ".join(r["name"] for r in planned["rooms"])
    ledger.record(project_id, "interiors", "proposed", f"Interior design '{brief.get('title') or brief.get('base_theme')}' for {rooms}: "
                  f"{sum(a['count'] for a in planned['added'])} pieces, about ₹{design['budget']['total'] / 1e5:.1f} lakh.",
                  actor=actor, design_id=design["id"])
    return design


COMPASS = ("north", "north_east", "east", "south_east", "south", "south_west", "west", "north_west")


def green_check(project_id: str, floors: list[dict], city: str, *, facing: str | None = None, editor_project_id: str | None = None,
                actor: str = "user") -> dict:
    """Green performance of the editor model for a city: grounded climate facts + the editor's own analysis code."""
    from . import climate as climate_mod
    from .interior import solver
    if not floors:
        raise ServiceError(422, "GREEN_NO_PLAN", "The plan was not provided.")
    site = store.get(project_id).get("site")
    if (not city or not city.strip()) and site and site.get("city"):
        city = ", ".join(x for x in (site["city"], site.get("country")) if x)
    if not city or not city.strip():
        raise ServiceError(422, "GREEN_NO_CITY", "Say which city the home is in (or pin the plot on the map), so local rainfall and sunshine can be used.")
    front, front_source = None, None
    if facing:
        if facing not in COMPASS:
            raise ServiceError(422, "GREEN_FACING", f"Facing must be one of {', '.join(COMPASS)}.")
        front, front_source = facing, "given"
    else:
        version = store.current_brief(store.get(project_id))
        brief = (version or {}).get("brief") or {}
        if brief.get("entrance_status") == "stated" and brief.get("entrance_facing"):
            front, front_source = brief["entrance_facing"], "brief"
    try:
        climate = climate_mod.lookup(city)
    except Exception as e:  # noqa: BLE001 - search or extraction failures are reported plainly
        raise ServiceError(502, "CLIMATE_FAILED", f"Could not look up the climate for {city.strip()}. Try a nearby larger city.") from e
    climate_in = {k: climate[k] for k in ("zone", "rainfall_mm", "sun_hours", "tariff", "currency", "hemisphere")} | {"city": climate["city"]}
    solar = None
    if site and site.get("lat") is not None:
        climate_in["hemisphere"] = "north" if site["lat"] >= 0 else "south"   # from the pin, not the search
        from . import solar_site
        try:
            solar = {"available": True, **solar_site.site_sun(site["lat"], site["lng"])}
            climate_in["site_yield"] = solar["yield_kwh_per_kwp"] * solar_site.DC_TO_AC
        except solar_site.NotCovered as e:
            solar = {"available": False, "reason": str(e)}
        except Exception as e:  # noqa: BLE001 - the site check is an extra; the climate estimate still works
            solar = {"available": False, "reason": "Sun measurement at the plot could not be reached right now."}
    try:
        analysis = solver.run({"cmd": "green", "floors": floors, "climate": climate_in, "front": front}, timeout=60)
    except solver.SolverError as e:
        raise ServiceError(502, "SOLVER_FAILED", str(e)) from e
    report = {"id": str(uuid.uuid4()), "created_at": store._now(), "editor_project_id": editor_project_id, "site": site, "solar_site": solar,
              "climate": climate, "front": front, "front_source": front_source, "report": analysis}
    store.save_green(project_id, report)
    s = analysis["score"]
    ledger.record(project_id, "green", "checked", f"Green check for {climate['city']} ({climate['zone'].replace('_', '-')}): score {s['total']}/100, "
                  f"{s['after_fixes']} after the suggested window fixes.", actor=actor, green_id=report["id"])
    return report
