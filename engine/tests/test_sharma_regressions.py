"""Regressions from the live "Sharma family home" demo project (fixtures saved from the deployment)."""

import json
from pathlib import Path

from constructos_engine import plumbing, program, solver
from constructos_engine.brief import Brief, Room
from constructos_engine.electrical import RoutingSource
from routing_fixtures import source_from_allocation

FIX = Path(__file__).parent / "fixtures"


def sharma_brief() -> Brief:
    return Brief.model_validate(json.loads((FIX / "sharma_brief.json").read_text()))


def test_terrace_only_upper_floor_drains_through_a_rainwater_stack():
    # This saved plan has no wet room upstairs, only two terraces with floor drains; it used to report "partial".
    src = RoutingSource.model_validate(json.loads((FIX / "sharma_routing_source.json").read_text()))
    d = plumbing.generate(src)
    assert d.status == "complete" and d.unresolved == [] and d.validation == []
    dr = d.network_metrics["drainage"]
    assert dr.endpoints_connected == dr.endpoints_total == 12
    assert any("rainwater pipe from Terrace" in a for a in d.assumptions)
    assert any(a.startswith("Stack S1 (rainwater pipe)") for a in d.assumptions)
    stacks = [e for e in d.endpoints if e.kind == "stack"]
    assert {e.floor for e in stacks} == {0, 1}


def test_listed_ensuite_is_used_and_the_kids_floor_gets_a_bathroom():
    p = program.build_program(sharma_brief())
    baths = {(s.floor, s.name) for s in p.spaces if s.type in ("bathroom", "toilet")}
    assert baths == {(0, "En-suite bathroom"), (1, "Common bathroom")}
    assert "En-suite bathroom is the en-suite of Our bedroom." in p.assumptions
    # The en-suite opens only from its bedroom.
    assert [(x.a, x.b) for x in p.passages if x.b == "bathroom-1"] == [("bedroom-1", "bathroom-1")]


def test_ensuite_flag_on_a_non_bedroom_is_ignored():
    b = sharma_brief()
    b.rooms.append(Room(id="study-1", type="study", name="Study", floor_index=0, requirement="hard", attached_bathroom=True, source="test"))
    names = [s.name for s in program.build_program(b).spaces]
    assert not any(n.startswith("En-suite (Study") or n.startswith("En-suite (En-suite") for n in names)


def test_each_bedroom_floor_gets_a_shared_bathroom_even_when_other_bathrooms_are_named():
    b = sharma_brief()
    b.rooms = [r for r in b.rooms if r.id != "bathroom-1"]  # only the parents' en-suite flag remains
    p = program.build_program(b)
    assert {(s.floor, s.name) for s in p.spaces if s.type == "bathroom"} == {(0, "En-suite (Our bedroom)"), (1, "Common bathroom")}


def test_freshly_solved_sharma_home_plumbs_completely():
    b = sharma_brief()
    p = program.build_program(b)
    a = solver.solve(p, b.plot.width_mm, b.plot.depth_mm, seed=1, time_limit_s=25)
    assert a.footprint is not None
    d = plumbing.generate(source_from_allocation(a))
    assert d.status == "complete", d.unresolved
    upper_wet = [s for s in a.spaces if s.floor == 1 and s.type == "bathroom"]
    assert upper_wet, "the kids' floor has its own bathroom"
