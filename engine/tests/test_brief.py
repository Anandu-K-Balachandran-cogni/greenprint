import pytest
from fastapi.testclient import TestClient

from constructos_engine import brief as b
from constructos_engine import store
from constructos_engine.main import app

ORIGINAL = (
    "Design a house on a 40 x 60 ft site with three bedrooms across two floors, "
    "parking for two cars, an open kitchen and the parents' bedroom on the ground floor."
)


def raw_original(**overrides) -> b.RawBrief:
    data = dict(
        plot=b.RawPlot(width=40, depth=60, unit="ft", quote="40 x 60 ft"),
        storeys=2,
        rooms=[
            b.RawRoom(type="master_bedroom", name="Parents bedroom", floor_index=0, requirement="hard", quote="parents' bedroom on the ground floor"),
            b.RawRoom(type="bedroom", name="Bedroom", floor_index=None, requirement="hard", quote="three bedrooms"),
            b.RawRoom(type="bedroom", name="Bedroom", floor_index=None, requirement="hard", quote="three bedrooms"),
            b.RawRoom(type="kitchen", name="Open kitchen", floor_index=0, requirement="hard", quote="open kitchen"),
        ],
        parking_spaces=2,
        open_kitchen=True,
    )
    data.update(overrides)
    return b.RawBrief(**data)


def test_original_brief_normalises_exactly():
    brief = b.normalize(raw_original(), ORIGINAL)
    assert (brief.plot.width_mm, brief.plot.depth_mm, brief.plot.status) == (12192, 18288, "stated")
    assert [r.id for r in brief.rooms] == ["master-bedroom-1", "bedroom-1", "bedroom-2", "kitchen-1"]
    assert brief.parking_spaces == 2 and brief.open_kitchen
    assert brief.rooms[0].floor_index == 0
    assert brief.conflicts == []
    # orientation was not stated: it is a non-essential question, never a fact
    assert brief.entrance_status == "unknown"
    assert [(c.field, c.essential) for c in brief.clarifications] == [("entrance_facing", False)]


def test_missing_bedroom_is_flagged_against_the_text():
    raw = raw_original()
    raw.rooms = raw.rooms[:2] + raw.rooms[3:]
    brief = b.normalize(raw, ORIGINAL)
    assert any("states 3 bedrooms but 2" in c.statement for c in brief.conflicts)


def test_missing_plot_and_storeys_need_clarification():
    brief = b.normalize(raw_original(plot=b.RawPlot(), storeys=None), "a house with three bedrooms")
    assert {c.field for c in brief.clarifications if c.essential} == {"plot", "storeys"}
    errors = b.validate_for_confirmation(brief)
    assert {e["field"] for e in errors} == {"plot", "storeys"}


def test_room_on_missing_floor_is_a_conflict_and_blocks_confirmation():
    raw = raw_original(storeys=1)
    brief = b.normalize(raw, ORIGINAL)
    assert b.validate_for_confirmation(brief) == []  # floor_index 0 rooms only
    brief.rooms[1].floor_index = 1
    assert any(e["field"] == "rooms.bedroom-1.floor_index" for e in b.validate_for_confirmation(brief))


def test_parking_is_not_duplicated_as_a_garage_room():
    raw = raw_original()
    raw.rooms.append(b.RawRoom(type="garage", name="Car park", requirement="hard", quote="parking for two cars"))
    assert all(r.type != "garage" for r in b.normalize(raw, ORIGINAL).rooms)


def test_stated_bedroom_count_parsing():
    assert b.stated_bedroom_count("a 3BHK flat") == 3
    assert b.stated_bedroom_count("four bedrooms") == 4
    assert b.stated_bedroom_count("a 2-bed cottage") == 2
    assert b.stated_bedroom_count("a studio") is None


def test_confirm_flow_versions_and_hashes(monkeypatch):
    monkeypatch.setattr(b, "extract", lambda text: b.normalize(raw_original(), text, "test-model"))
    client = TestClient(app)
    project = client.post("/projects", json={"name": "Original brief"}).json()
    pid = project["id"]

    draft = client.post(f"/projects/{pid}/brief/extract", json={"text": ORIGINAL}).json()
    assert client.get(f"/projects/{pid}").json()["brief_draft"]["rooms"][0]["id"] == "master-bedroom-1"

    # a tampered derived value is recomputed on the server
    draft["plot"]["width_mm"] = 99999
    first = client.post(f"/projects/{pid}/brief/confirm", json={"brief": draft, "expected_version": 0})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1
    assert first.json()["brief"]["plot"]["width_mm"] == 12192
    assert first.json()["hash"].startswith("sha256-")

    stale = client.post(f"/projects/{pid}/brief/confirm", json={"brief": draft, "expected_version": 0})
    assert stale.status_code == 409

    draft["storeys"] = 3
    second = client.post(f"/projects/{pid}/brief/confirm", json={"brief": draft, "expected_version": 1}).json()
    assert second["version"] == 2 and second["hash"] != first.json()["hash"]
    assert client.get(f"/projects/{pid}").json()["brief_version"] == 2


def test_incomplete_brief_cannot_be_confirmed():
    client = TestClient(app)
    pid = client.post("/projects", json={}).json()["id"]
    brief = b.normalize(raw_original(plot=b.RawPlot()), ORIGINAL).model_dump(mode="json")
    r = client.post(f"/projects/{pid}/brief/confirm", json={"brief": brief, "expected_version": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "BRIEF_INCOMPLETE"


def test_unknown_project_and_path_traversal_are_rejected():
    client = TestClient(app)
    assert client.get("/projects/does-not-exist").status_code == 404
    assert client.get("/projects/..%2F..%2Fetc").status_code == 404


def test_square_feet_typed_as_square_metres_is_flagged_and_impossible_sizes_block_confirmation():
    brief = b.normalize(raw_original(storeys=1), "A house with 40*60 sqft - with 2 rooms 1 kitchen")
    brief.rooms[1].min_area_m2 = 150  # user meant 150 sq ft; fits the 223 m² plot but is implausible
    checked = b.apply_checks(brief)
    hint = next(c.statement for c in checked.conflicts if c.statement.startswith("Bedroom"))
    assert "If you meant 150 sq ft, that is 13.9 m²" in hint
    assert b.validate_for_confirmation(checked) == []  # a warning, not a blocker

    brief.rooms[1].min_area_m2 = 250  # larger than the whole plot
    errors = b.validate_for_confirmation(b.apply_checks(brief))
    assert any(e["field"] == "rooms.bedroom-1.min_area_m2" and "more than the whole plot (223 m²)" in e["message"] for e in errors)

    brief.rooms[1].min_area_m2 = 120
    brief.rooms[0].min_area_m2 = 120  # each fits, together they do not
    errors = b.validate_for_confirmation(b.apply_checks(brief))
    assert any(e["field"] == "rooms" and "add up to 240 m²" in e["message"] for e in errors)


def test_fixed_sizes_drop_stale_size_warnings_on_confirm():
    brief = b.normalize(raw_original(storeys=1), ORIGINAL)
    brief.rooms[1].min_area_m2 = 150
    flagged = b.apply_checks(brief)
    flagged.rooms[1].min_area_m2 = 13.9
    final = b.apply_checks(b.finalize_confirmed(flagged))
    assert not any("sq ft" in c.statement for c in final.conflicts)
