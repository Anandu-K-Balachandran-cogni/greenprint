import json
from types import SimpleNamespace

import pytest

from conftest import wait_run
from fastapi.testclient import TestClient

from constructos_engine import brief as b
from constructos_engine import program as prog_mod
from constructos_engine import review, solver, store
from constructos_engine.layout_checks import check_allocation
from constructos_engine.program import Change


def brief_single() -> b.Brief:
    raw = b.RawBrief(plot=b.RawPlot(width=40, depth=60, unit="ft"), storeys=1, parking_spaces=1, open_kitchen=True,
                     entrance_facing="east",
                     rooms=[b.RawRoom(type="master_bedroom", name="Main bedroom", requirement="hard", quote=""),
                            b.RawRoom(type="bedroom", name="Kids bedroom", requirement="hard", quote=""),
                            b.RawRoom(type="kitchen", name="Kitchen", requirement="hard", quote="")])
    return b.normalize(raw, "two bedroom house facing east")


@pytest.fixture(scope="module")
def solved():
    brief = brief_single()
    p = prog_mod.build_program(brief)
    a = solver.solve(p, brief.plot.width_mm, brief.plot.depth_mm, time_limit_s=20, seed=3)
    return brief, p, a, check_allocation(a, p)


class FakeModels:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.schemas = []

    def generate_content(self, **kw):
        self.calls += 1
        self.schemas.append(kw["config"].response_json_schema)
        return SimpleNamespace(text=json.dumps(self.payload))


def fake_gemini(monkeypatch, payload) -> FakeModels:
    fake = FakeModels(payload)
    monkeypatch.setattr(review.gemini, "get_client", lambda: SimpleNamespace(models=fake))
    monkeypatch.setattr(review.gemini, "get_fallback_client", lambda: None)
    monkeypatch.setattr(review.gemini, "select_models", lambda: SimpleNamespace(reasoning="fake-model", fast="fake-model"))
    return fake


# --- Facts -----------------------------------------------------------------

def test_fact_sheet_is_measured_and_oriented(solved):
    brief, p, a, r = solved
    facts = {f.id: f.text for f in review.build_facts(brief, p, a, r)}
    assert facts["B:orientation"] == "Entrance faces east (stated by the user)."
    kitchen = next(s for s in a.spaces if s.id == "kitchen-1")
    assert f"= {kitchen.area_m2:g} m²" in facts["R:kitchen-1"]
    if "front" in kitchen.exterior_sides:
        assert "front (east)" in facts["R:kitchen-1"]
    assert any(k.startswith("A:") and "share a" in v for k, v in facts.items())
    assert all(f"E:{e.check}" in facts for e in r.evidence)


def test_unknown_orientation_is_never_guessed(solved):
    brief, p, a, r = solved
    brief = brief.model_copy(update={"entrance_facing": None, "entrance_status": "unknown"})
    facts = {f.id: f.text for f in review.build_facts(brief, p, a, r)}
    assert "cannot be assessed" in facts["B:orientation"]
    assert "(east)" not in " ".join(facts.values())


# --- Review grounding ------------------------------------------------------------

VALID_REVIEW = {
    "summary": "A compact plan.",
    "strengths": [{"statement": "Bedrooms are at the rear.", "evidence": ["R:master-bedroom-1"]}],
    "concerns": [],
    "suggestions": [{"statement": "Give the dining room daylight.", "evidence": ["R:dining-1"],
                     "change": {"action": "require_exterior", "space_id": "dining-1"}},
                    {"statement": "A huge dining room.", "evidence": ["R:dining-1"],
                     "change": {"action": "set_min_area", "space_id": "dining-1", "value_m2": 150}}],
}


def test_review_schema_is_built_from_this_layout(solved):
    brief, p, a, r = solved
    facts = [f.id for f in review.build_facts(brief, p, a, r)]
    schema = json.dumps(review.review_schema(facts, p).model_json_schema())
    assert '"R:master-bedroom-1"' in schema and '"E:reachability"' in schema  # citations are an enum of real facts
    assert '"set_floor"' not in schema  # a single-floor plan cannot move rooms between floors
    variants = review.change_schema(p).model_json_schema()["$defs"]
    remove = next(v for v in variants.values() if v["properties"]["action"].get("const") == "remove_room")
    assert set(remove["properties"]["space_id"]["enum"]) == {"foyer-1", "dining-1", "toilet-1"} & {s.id for s in p.spaces}
    assert remove["required"] == ["action", "space_id"]


def test_valid_review_is_kept_and_semantic_problems_still_disable_a_change(solved, monkeypatch):
    brief, p, a, r = solved
    fake = fake_gemini(monkeypatch, VALID_REVIEW)
    rv = review.review(brief, p, a, r, 159.1)
    sent = json.dumps(fake.schemas[0])
    assert fake.calls == 1 and '"R:dining-1"' in sent  # the enforced schema went to Gemini
    assert '"const"' not in sent and '"enum": ["require_exterior"]' in sent  # in the subset Gemini's decoder enforces
    assert [s.statement for s in rv.strengths] == ["Bedrooms are at the rear."]
    assert rv.suggestions[0].change.action == "require_exterior"
    assert rv.suggestions[1].change is None and "more than 60%" in rv.rejected_changes[0]


@pytest.mark.parametrize("bad", [
    {"strengths": [{"statement": "Meets the local fire code.", "evidence": ["X:fire-code"]}]},  # invented fact
    {"strengths": [{"statement": "Uncited.", "evidence": []}]},                                 # no citation
    {"suggestions": [{"statement": "Delete the kitchen.", "evidence": ["R:kitchen-1"],
                      "change": {"action": "remove_room", "space_id": "kitchen-1"}}]},          # not removable
    {"suggestions": [{"statement": "Bigger.", "evidence": ["R:bedroom-1"],
                      "change": {"action": "set_min_area", "space_id": None, "name": "Kids bedroom", "value_m2": 16}}]},
])
def test_output_outside_the_schema_is_retried_once_then_refused(solved, monkeypatch, bad):
    from constructos_engine.ai_schemas import AIOutputInvalid
    brief, p, a, r = solved
    fake = fake_gemini(monkeypatch, {**VALID_REVIEW, **bad})
    with pytest.raises(AIOutputInvalid):
        review.review(brief, p, a, r, 159.1)
    assert fake.calls == 2


# --- Typed changes ------------------------------------------------------------------

def test_change_validation_protects_the_brief(solved):
    _, p, _, _ = solved
    bad = [Change(action="remove_room", space_id="kitchen-1"), Change(action="set_min_area", space_id="bedroom-1", value_m2=500),
           Change(action="set_floor", space_id="bedroom-1", floor=3), Change(action="set_zone", space_id="nope", zone="rear"),
           Change(action="add_room", room_type="corridor", name="Extra", floor=0)]
    errors = prog_mod.validate_changes(bad, p, 159.1)
    assert len(errors) == 5
    assert prog_mod.validate_changes([Change(action="remove_room", space_id="dining-1")], p, 159.1) == []


def test_changes_reshape_the_programme(solved):
    brief, _, _, _ = solved
    p = prog_mod.build_program(brief, changes=[
        Change(action="set_min_area", space_id="bedroom-1", value_m2=15),
        Change(action="set_zone", space_id="kitchen-1", zone="rear"),
        Change(action="remove_room", space_id="dining-1"),
        Change(action="add_room", room_type="study", name="Study", floor=0),
        Change(action="require_adjacent", space_id="kitchen-1", other_space_id="living-1"),
    ])
    by = {s.id: s for s in p.spaces}
    assert by["bedroom-1"].min_area_m2 == 15 and by["bedroom-1"].user_min
    assert by["kitchen-1"].zone == "rear"
    assert "dining-1" not in by and "study-1" in by
    assert any(x.extra and {x.a, x.b} == {"kitchen-1", "living-1"} for x in p.passages)
    assert len(p.changes) == 5


def test_revision_solves_satisfies_the_change_and_stays_close(solved):
    brief, p, a, _ = solved
    before = next(s for s in a.spaces if s.id == "bedroom-1")
    want = round(before.area_m2 * 1.25, 1)
    revised = prog_mod.build_program(brief, changes=[Change(action="set_min_area", space_id="bedroom-1", value_m2=want)])
    alloc = solver.solve(revised, brief.plot.width_mm, brief.plot.depth_mm, near=a, time_limit_s=20, seed=4)
    assert alloc.status in ("OPTIMAL", "FEASIBLE")
    assert check_allocation(alloc, revised).status == "PASS"
    after = next(s for s in alloc.spaces if s.id == "bedroom-1")
    assert after.area_m2 >= want
    unchanged = sum(1 for s in alloc.spaces for o in a.spaces if s.id == o.id and (s.x_mm, s.y_mm) == (o.x_mm, o.y_mm))
    assert unchanged >= len(a.spaces) // 3  # not a from-scratch redesign


# --- API ------------------------------------------------------------------------------

@pytest.fixture
def api():
    from constructos_engine.main import app
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Review"}).json()["id"]
    brief = brief_single().model_dump(mode="json")
    assert client.post(f"/projects/{pid}/brief/confirm", json={"brief": brief, "expected_version": 0}).status_code == 200
    run = client.post(f"/projects/{pid}/layouts", json={"expected_brief_version": 1, "alternatives": 1, "time_limit_s": 15}).json()
    return client, pid, wait_run(client, pid, run["id"])


def test_revise_with_a_typed_change_creates_a_linked_revision(api):
    client, pid, run = api
    r = client.post(f"/projects/{pid}/layouts/{run['id']}/candidates/0/revise",
                    json={"changes": [{"action": "require_exterior", "space_id": "dining-1"}]})
    assert r.status_code == 200, r.text
    assert r.json()["run"]["status"] == "queued"
    rev = wait_run(client, pid, r.json()["run"]["id"])
    assert rev["status"] == "complete"
    assert rev["kind"] == "revision" and rev["parent"] == {"run_id": run["id"], "candidate": 0}
    assert rev["change_descriptions"] == ["Dining: must have an outside wall"]
    alloc = rev["candidates"][0]["allocation"]
    if alloc["status"] in ("OPTIMAL", "FEASIBLE"):
        dining = next(s for s in alloc["spaces"] if s["id"] == "dining-1")
        assert dining["exterior_sides"]
    listed = client.get(f"/projects/{pid}").json()["layout_runs"]
    assert listed[-1]["kind"] == "revision"


def test_ambiguous_request_asks_instead_of_guessing(api, monkeypatch):
    client, pid, run = api
    fake_gemini(monkeypatch, {"changes": [], "clarification": "Which bedroom?", "explanation": ""})
    r = client.post(f"/projects/{pid}/layouts/{run['id']}/candidates/0/revise", json={"request": "make the bedroom bigger"})
    assert r.json() == {"status": "clarify", "question": "Which bedroom?", "rejected": []}
    assert len(client.get(f"/projects/{pid}").json()["layout_runs"]) == 1


def test_invalid_typed_change_is_refused(api):
    client, pid, run = api
    r = client.post(f"/projects/{pid}/layouts/{run['id']}/candidates/0/revise",
                    json={"changes": [{"action": "remove_room", "space_id": "kitchen-1"}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CHANGE_INVALID"


def test_change_request_naming_a_room_instead_of_its_id_is_refused_not_guessed(solved, monkeypatch):
    from constructos_engine.ai_schemas import AIOutputInvalid
    brief, p, a, r = solved
    fake_gemini(monkeypatch, {"changes": [{"action": "set_min_area", "space_id": None, "name": "Kids bedroom", "value_m2": 16}],
                              "clarification": None, "explanation": "x"})
    with pytest.raises(AIOutputInvalid):
        review.interpret_request("make the kids room 16 m2", brief, p, a, r, 159.1)


def test_duplicate_changes_are_applied_once(solved, monkeypatch):
    brief, p, a, r = solved
    change = {"action": "set_min_area", "space_id": "bedroom-1", "value_m2": 16}
    fake_gemini(monkeypatch, {"changes": [change, change], "clarification": None, "explanation": "x"})
    assert review.interpret_request("16 m2 kids room", brief, p, a, r, 159.1).descriptions == ["Kids bedroom: at least 16 m²"]


def test_api_reports_ai_output_errors_without_changing_anything(api, monkeypatch):
    client, pid, run = api
    fake_gemini(monkeypatch, {"changes": [{"action": "explode"}], "clarification": None, "explanation": "x"})
    r = client.post(f"/projects/{pid}/layouts/{run['id']}/candidates/0/revise", json={"request": "anything"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "AI_OUTPUT_INVALID"
    assert len(client.get(f"/projects/{pid}").json()["layout_runs"]) == 1


def test_missing_room_reference_is_explained(solved):
    _, p, _, _ = solved
    assert prog_mod.validate_changes([Change(action="require_exterior")], p, 159.1) == ["A change did not say which room it applies to."]
