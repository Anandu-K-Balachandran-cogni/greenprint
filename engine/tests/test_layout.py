import pytest

from conftest import wait_run

from constructos_engine import brief as b
from constructos_engine import program as prog_mod
from constructos_engine import solver
from constructos_engine.layout_checks import check_allocation, shared_edge_mm
from constructos_engine.solver import Allocation, PlacedPassage, PlacedSpace, Setbacks


def single_floor_brief() -> b.Brief:
    raw = b.RawBrief(
        plot=b.RawPlot(width=40, depth=60, unit="ft"), storeys=1, parking_spaces=1, open_kitchen=True,
        rooms=[b.RawRoom(type="master_bedroom", name="Main bedroom", requirement="hard", quote=""),
               b.RawRoom(type="bedroom", name="Bedroom", requirement="hard", quote=""),
               b.RawRoom(type="kitchen", name="Kitchen", requirement="hard", quote="")],
    )
    return b.normalize(raw, "a two bedroom house")


def original_brief() -> b.Brief:
    raw = b.RawBrief(
        plot=b.RawPlot(width=40, depth=60, unit="ft"), storeys=2, parking_spaces=2, open_kitchen=True,
        rooms=[b.RawRoom(type="master_bedroom", name="Parents bedroom", floor_index=0, requirement="hard", quote=""),
               b.RawRoom(type="bedroom", name="Bedroom 2", requirement="hard", quote=""),
               b.RawRoom(type="bedroom", name="Bedroom 3", requirement="hard", quote=""),
               b.RawRoom(type="kitchen", name="Kitchen", requirement="hard", quote="")],
    )
    return b.normalize(raw, "three bedrooms")


# --- Programme ---------------------------------------------------------------

def test_original_programme_keeps_brief_and_adds_visible_assumptions():
    p = prog_mod.build_program(original_brief())
    by_id = {s.id: s for s in p.spaces}
    assert by_id["master-bedroom-1"].floor == 0
    assert by_id["bedroom-1"].floor == 1 and by_id["bedroom-2"].floor == 1
    assert by_id["parking-1"].min_w_mm == 5400 and by_id["parking-1"].front
    assert {s.id for s in p.spaces if s.vertical_core} == {"stair-0", "stair-1"}
    assert any("living room" in a for a in p.assumptions)
    assert any("first floor" in a for a in p.assumptions)
    # open kitchen is an opening, not a door
    assert any(x.b == "kitchen-1" and x.kind == "opening" for x in p.passages)


def test_every_programmed_space_is_reachable_in_the_passage_tree():
    for brief in (single_floor_brief(), original_brief()):
        p = prog_mod.build_program(brief)
        reached = {x.b for x in p.passages}
        assert all(s.id in reached or s.vertical_core for s in p.spaces)


# --- Checker oracle on hand-authored geometry ----------------------------------

def _two_room_case(gap_mm: int = 0, overlap_mm: int = 3000):
    p = prog_mod.Program(storeys=1, assumptions=[], spaces=[
        prog_mod.Space(id="living-1", type="living", name="Living", floor=0, min_w_mm=3000, min_h_mm=3000,
                       min_area_m2=9, target_area_m2=12, max_area_m2=30, zone="front", exterior="required",
                       max_aspect=None, front=True),
        prog_mod.Space(id="bedroom-1", type="bedroom", name="Bed", floor=0, min_w_mm=3000, min_h_mm=3000,
                       min_area_m2=9, target_area_m2=12, max_area_m2=30, zone="rear", exterior="required", max_aspect=None),
    ], passages=[
        prog_mod.Passage(a="exterior", b="living-1", kind="entrance", floor=0, min_overlap_mm=1200),
        prog_mod.Passage(a="living-1", b="bedroom-1", kind="door", floor=0, min_overlap_mm=1200),
    ])
    alloc = Allocation(status="OPTIMAL", alternative=0, seed=0, solve_seconds=0, plot={"width_mm": 10000, "depth_mm": 10000},
                       setbacks=Setbacks(), footprint={"x_mm": 1000, "y_mm": 1000, "width_mm": 3000, "depth_mm": 8000},
                       spaces=[
                           PlacedSpace(id="living-1", type="living", name="Living", floor=0, x_mm=1000, y_mm=1000,
                                       w_mm=3000, h_mm=4000, area_m2=12, exterior_sides=["front", "left", "right"]),
                           PlacedSpace(id="bedroom-1", type="bedroom", name="Bed", floor=0, x_mm=1000 + (3000 - overlap_mm),
                                       y_mm=5000 + gap_mm, w_mm=3000, h_mm=4000 - gap_mm, area_m2=12,
                                       exterior_sides=["rear", "left", "right"]),
                       ],
                       passages=[
                           PlacedPassage(a="exterior", b="living-1", kind="entrance", floor=0, overlap_mm=3000),
                           PlacedPassage(a="living-1", b="bedroom-1", kind="door", floor=0, side="rear", overlap_mm=3000),
                       ])
    return alloc, p


def test_checker_passes_hand_authored_valid_layout():
    alloc, p = _two_room_case()
    report = check_allocation(alloc, p)
    assert report.status == "PASS", report.findings


def test_checker_detects_gap_and_lost_passage():
    alloc, p = _two_room_case(gap_mm=300)
    codes = {f.code for f in check_allocation(alloc, p).findings}
    assert {"GAPS_IN_FLOOR", "NO_PASSAGE", "UNREACHABLE"} <= codes


def test_checker_detects_overlap():
    alloc, p = _two_room_case()
    alloc.spaces[1].y_mm -= 600
    codes = {f.code for f in check_allocation(alloc, p).findings}
    assert "OVERLAP" in codes


def test_shared_edge_measurement():
    a = PlacedSpace(id="a", type="x", name="a", floor=0, x_mm=0, y_mm=0, w_mm=3000, h_mm=3000, area_m2=9, exterior_sides=[])
    c = PlacedSpace(id="c", type="x", name="c", floor=0, x_mm=3000, y_mm=1800, w_mm=3000, h_mm=3000, area_m2=9, exterior_sides=[])
    assert shared_edge_mm(a, c) == 1200
    d = c.model_copy(update={"x_mm": 3300})
    assert shared_edge_mm(a, d) == 0


# --- Real solver -----------------------------------------------------------------

@pytest.fixture(scope="module")
def single_floor_solution():
    brief = single_floor_brief()
    p = prog_mod.build_program(brief)
    return p, solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=30, seed=1)


def test_single_floor_solve_passes_independent_checks(single_floor_solution):
    p, alloc = single_floor_solution
    assert alloc.status in ("OPTIMAL", "FEASIBLE")
    report = check_allocation(alloc, p)
    assert report.status == "PASS", [f.message for f in report.findings]


def test_alternative_differs_measurably(single_floor_solution):
    p, first = single_floor_solution
    brief = single_floor_brief()
    second = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=30, seed=2, alternative=1, avoid=[first])
    assert second.status in ("OPTIMAL", "FEASIBLE")
    assert check_allocation(second, p).status == "PASS"
    moved = sum(1 for a in first.spaces for s in second.spaces
                if a.id == s.id and abs(a.x_mm - s.x_mm) + abs(a.y_mm - s.y_mm) >= 900)
    assert moved >= 2


def test_impossible_brief_is_infeasible_not_invented():
    raw = b.RawBrief(plot=b.RawPlot(width=6, depth=6, unit="m"), storeys=1,
                     rooms=[b.RawRoom(type="bedroom", name=f"Bed {i}", requirement="hard", quote="") for i in range(4)])
    brief = b.normalize(raw, "four bedrooms on a tiny plot")
    p = prog_mod.build_program(brief)
    alloc = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=10)
    assert alloc.status in ("INFEASIBLE", "UNKNOWN")
    assert alloc.spaces == [] and alloc.footprint is None
    assert alloc.message


def test_layout_api_requires_current_confirmed_brief(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from constructos_engine.main import app

    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Layout"}).json()["id"]
    assert client.post(f"/projects/{pid}/layouts", json={"expected_brief_version": 1}).status_code == 409
    brief = single_floor_brief().model_dump(mode="json")
    assert client.post(f"/projects/{pid}/brief/confirm", json={"brief": brief, "expected_version": 0}).status_code == 200
    assert client.post(f"/projects/{pid}/layouts", json={"expected_brief_version": 2}).status_code == 409

    r = client.post(f"/projects/{pid}/layouts", json={"expected_brief_version": 1, "alternatives": 1, "time_limit_s": 20})
    assert r.status_code == 202 and r.json()["status"] == "queued" and "lease" not in r.json()["job"]
    run = wait_run(client, pid, r.json()["id"])
    assert run["status"] == "complete" and run["job"]["progress"] == {"done": 1, "total": 1}
    assert run["brief_version"] == 1 and run["brief_hash"].startswith("sha256-")
    cand = run["candidates"][0]
    assert cand["allocation"]["status"] in ("OPTIMAL", "FEASIBLE")
    assert cand["report"]["status"] == "PASS"
    assert client.get(f"/projects/{pid}/layouts/{run['id']}").json()["id"] == run["id"]
    assert client.get(f"/projects/{pid}").json()["layout_runs"][0]["id"] == run["id"]


def test_space_larger_than_buildable_area_is_explained_infeasibility():
    raw = b.RawBrief(plot=b.RawPlot(width=6, depth=4.5, unit="m"), storeys=1, parking_spaces=1,
                     rooms=[b.RawRoom(type="bedroom", name="Bed", requirement="hard", quote="")])
    brief = b.normalize(raw, "tiny")
    alloc = solver.solve(prog_mod.build_program(brief), brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=5)
    assert alloc.status == "INFEASIBLE"
    assert "Parking (1 car) needs at least 2.7 × 5.4 m" in alloc.message


def test_original_two_storey_brief_solves_with_aligned_stair_and_reachable_floors():
    brief = original_brief()
    p = prog_mod.build_program(brief)
    alloc = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=25, seed=1)
    assert alloc.status in ("OPTIMAL", "FEASIBLE"), alloc.message
    report = check_allocation(alloc, p)
    assert report.status == "PASS", [f.message for f in report.findings]
    by = {(s.floor, s.id): s for s in alloc.spaces}
    s0, s1 = by[(0, "stair-0")], by[(1, "stair-1")]
    assert (s0.x_mm, s0.y_mm, s0.w_mm, s0.h_mm) == (s1.x_mm, s1.y_mm, s1.w_mm, s1.h_mm)
    assert (0, "master-bedroom-1") in by and (1, "bedroom-1") in by and (1, "bedroom-2") in by
    assert by[(0, "parking-1")].w_mm >= 5400 and "front" in by[(0, "parking-1")].exterior_sides
    # every open terrace has a door from a roofed upper room
    terraces = [s for s in alloc.spaces if s.filler]
    assert all(any(x.b == t.id and x.floor == 1 for x in alloc.passages) for t in terraces)


def test_checker_rejects_access_through_a_space_that_is_not_allowed():
    alloc, p = _two_room_case()
    alloc.passages[1] = alloc.passages[1].model_copy(update={"a": "kitchen-9"})
    assert "NO_PASSAGE" in {f.code for f in check_allocation(alloc, p).findings}


def test_common_30x40_ft_plot_with_car_uses_compact_defaults_and_solves():
    raw = b.RawBrief(plot=b.RawPlot(width=30, depth=40, unit="ft"), storeys=1, parking_spaces=1,
                     rooms=[b.RawRoom(type="master_bedroom", name="Master", requirement="hard", quote=""),
                            b.RawRoom(type="bedroom", name="Bedroom", requirement="hard", quote=""),
                            b.RawRoom(type="kitchen", name="Kitchen", requirement="hard", quote="")])
    brief = b.normalize(raw, "2BHK on a 30x40 site")
    p = prog_mod.build_program(brief)
    assert p.compact and (p.setbacks.front_mm, p.setbacks.rear_mm, p.setbacks.side_mm) == (1000, 600, 600)
    assert not any(s.type in ("foyer", "dining") for s in p.spaces)
    assert any("Compact plot" in a for a in p.assumptions)
    alloc = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=20)
    assert alloc.status in ("OPTIMAL", "FEASIBLE")
    assert check_allocation(alloc, p).status == "PASS"


def test_user_stated_sizes_are_never_compacted_and_are_named_when_impossible():
    raw = b.RawBrief(plot=b.RawPlot(width=30, depth=40, unit="ft"), storeys=1, parking_spaces=1,
                     rooms=[b.RawRoom(type="bedroom", name="Big bedroom", min_area_m2=60, requirement="hard", quote=""),
                            b.RawRoom(type="living", name="Hall", min_area_m2=40, requirement="hard", quote="")])
    brief = b.normalize(raw, "x")
    p = prog_mod.build_program(brief)
    assert next(s for s in p.spaces if s.id == "bedroom-1").min_area_m2 == 60
    alloc = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=5)
    assert alloc.status == "INFEASIBLE"
    assert "Big bedroom 60 m² (your minimum)" in alloc.message
    assert "Parking (1 car) 14.58 m² (your minimum)" not in alloc.message


def test_feasibility_explains_fit_with_numbers_and_matches_the_solver():
    from constructos_engine.feasibility import assess
    f = assess(original_brief())
    assert f.verdict == "fits"
    assert f.buildable == {"width_m": 10.2, "depth_m": 15.6, "area_m2": 159.1}
    ground = next(c for c in f.checks if c.code == "FLOOR_AREA")
    assert ground.status == "pass" and ground.limit == 159.1 and "of 159 m²" in ground.message
    frontage = next(c for c in f.checks if c.code == "FRONTAGE")
    assert "Parking (2 cars) 5.4 m + Foyer 1.5 m = 6.9 m" in frontage.message
    assert {s.source for fl in f.floors for s in fl.spaces} >= {"typical", "added", "standard"}


def test_feasibility_failure_is_the_solver_infeasibility():
    from constructos_engine.feasibility import assess
    raw = b.RawBrief(plot=b.RawPlot(width=20, depth=60, unit="ft"), storeys=1, parking_spaces=2,
                     rooms=[b.RawRoom(type="bedroom", name="Bed", requirement="hard", quote="")])
    brief = b.normalize(raw, "narrow plot with two cars")
    f = assess(brief)
    assert f.verdict == "does_not_fit"
    fail = [c for c in f.checks if c.status == "fail"]
    assert any(c.code in ("FRONTAGE", "SPACE_DIMENSIONS") for c in fail)
    alloc = solver.solve(prog_mod.build_program(brief), brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=5)
    assert alloc.status == "INFEASIBLE" and fail[0].message in alloc.message


def test_layout_report_carries_measured_evidence_and_metrics(single_floor_solution):
    p, alloc = single_floor_solution
    r = check_allocation(alloc, p)
    assert [e.check for e in r.evidence] == r.checked
    assert all(e.status in ("pass", "warn") for e in r.evidence)
    tiling = next(e for e in r.evidence if e.check == "exact_tiling")
    fp = alloc.footprint["width_mm"] * alloc.footprint["depth_mm"] / 1e6
    assert f"{fp:.1f} of {fp:.1f} m²" in tiling.detail
    m = r.metrics
    assert m.footprint_m2 == round(fp, 1) and 0 < m.ground_coverage_pct < 100
    assert m.built_up_m2 == round(sum(s.area_m2 for s in alloc.spaces), 1)
    assert m.rooms_total == len(m.rooms) > 0
