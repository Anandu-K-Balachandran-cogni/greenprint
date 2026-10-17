"""Exports, background jobs and the store backends."""

import io
import json
import math
import os
import time
import uuid
from pathlib import Path

import ezdxf
import pdfplumber
import pytest
from fastapi.testclient import TestClient

from constructos_engine import electrical, exports, jobs, plumbing, store
from constructos_engine.electrical import SourceWall
from constructos_engine.main import app
from constructos_engine.solver import Allocation
from routing_fixtures import source_from_allocation

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "tests" / "fixtures" / "constructos"
PT_PER_MM = 72 / 25.4


def with_walls(src):
    """Walls along every room edge (deduplicated), as the compiler produces them."""
    seen, walls = set(), []
    for r in src.rooms:
        pts = r.polygon
        for a, b in zip(pts, pts[1:] + pts[:1]):
            key = (r.floor, min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            walls.append(SourceWall(floor=r.floor, x1=a[0], y1=a[1], x2=b[0], y2=b[1], thickness_mm=115))
    return src.model_copy(update={"walls": walls})


@pytest.fixture(scope="module")
def source():
    alloc = Allocation.model_validate(json.loads((FIXTURES / "two-storey-allocation.json").read_text()))
    return with_walls(source_from_allocation(alloc, source_hash="sha256-" + "ab" * 32))


@pytest.fixture(scope="module")
def plumbing_diagram(source):
    return plumbing.generate(source).model_dump(mode="json")


# --- DXF -----------------------------------------------------------------------------

def test_dxf_is_millimetres_with_layers_and_true_lengths(source, plumbing_diagram):
    data = exports.render_dxf(plumbing_diagram, "Test house")
    doc = ezdxf.read(io.StringIO(data.decode()))
    assert doc.header["$INSUNITS"] == 4  # millimetres
    for layer in ("A-WALL", "A-WALL-CL", "A-ROOM", "A-DOOR", "P-SUPPLY", "P-DRAINAGE", "P-SYMBOL"):
        assert layer in doc.layers
    msp = doc.modelspace()
    offsets = exports.floor_offsets(plumbing_diagram)
    # Every wall centreline is exported at its true length, at its floor's offset, Y flipped.
    lines = {(round(l.dxf.start.x), round(l.dxf.start.y), round(l.dxf.end.x), round(l.dxf.end.y))
             for l in msp.query('LINE[layer=="A-WALL-CL"]')}
    assert len(lines) == len(source.walls)
    for w in source.walls:
        dx = offsets[w.floor]
        assert (round(w.x1 + dx), round(-w.y1), round(w.x2 + dx), round(-w.y2)) in lines
    # Drainage linework adds up to the diagram's unique drainage length on each floor.
    for f, dx in offsets.items():
        x0, _, x1, _ = exports.floor_bounds(plumbing_diagram, f)
        expected = sum(math.dist(a, b) for c, segs in exports.segments(plumbing_diagram, f).items()
                       if c.startswith("DR") for a, b in segs)
        got = sum(math.dist(l.dxf.start, l.dxf.end) for l in msp.query('LINE[layer=="P-DRAINAGE"]')
                  if x0 + dx - 1 <= l.dxf.start.x <= x1 + dx + 1)
        assert expected > 0 and got == pytest.approx(expected, abs=1)
    stacks = [e for e in msp.query("INSERT") if e.dxf.name == "SYM-STACK"]
    assert len(stacks) == 2  # the same stack on both floors
    assert any("sha256-abababab" in t.dxf.text for t in msp.query("TEXT"))


# --- PDF -----------------------------------------------------------------------------

def test_pdf_has_one_sheet_per_floor_at_a_stated_scale_with_a_true_scale_bar(source, plumbing_diagram):
    data = exports.render_pdf(plumbing_diagram, "Test house")
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        assert len(pdf.pages) == 2
        for page in pdf.pages:
            assert page.width == pytest.approx(420 * PT_PER_MM, abs=1) and page.height == pytest.approx(297 * PT_PER_MM, abs=1)
            text = page.extract_text()
            assert "Plumbing layout" in text and "sha256-abababab" in text and "not verified" in text
            scale = int(text.split("Scale 1:")[1].split()[0])
            assert scale in exports.SCALES
            five_m = 5000 / scale * PT_PER_MM
            assert any(abs((l["x1"] - l["x0"]) - five_m) < 0.05 and abs(l["linewidth"] - 0.3) < 0.01 for l in page.lines)
            # The longest wall on the sheet measures its true length at that scale.
            floor = 0 if "Ground floor" in text else 1
            longest = max(math.hypot(w.x2 - w.x1, w.y2 - w.y1) for w in source.walls if w.floor == floor)
            drawn = max(math.hypot(l["x1"] - l["x0"], l["bottom"] - l["top"]) for l in page.lines if l["linewidth"] > 0.3
                        and l.get("stroking_color") not in (None,) and l["linewidth"] == pytest.approx(max(0.4, 115 * PT_PER_MM / scale), abs=0.01))
            assert drawn == pytest.approx(longest / scale * PT_PER_MM, abs=0.1)


def test_scale_choice_is_the_largest_standard_scale_that_fits():
    assert exports.choose_scale(12_000, 18_000, 300, 230) == 100
    assert exports.choose_scale(12_000, 18_000, 100, 80) == 250
    assert exports.choose_scale(80_000, 80_000, 100, 80) == 500


def test_pdf_is_deterministic(plumbing_diagram):
    assert exports.render_pdf(plumbing_diagram) == exports.render_pdf(plumbing_diagram)


# --- API -----------------------------------------------------------------------------

def test_export_endpoints_return_files_and_keep_artifacts(source, tmp_path):
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Export house"}).json()["id"]
    body = {"source": source.model_dump(mode="json")}
    d = client.post(f"/projects/{pid}/diagrams/plumbing", json=body).json()
    r = client.get(f"/projects/{pid}/diagrams/{d['id']}/export/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert 'filename="constructos-plumbing-abababab.pdf"' in r.headers["content-disposition"]
    r = client.get(f"/projects/{pid}/diagrams/{d['id']}/export/dxf")
    assert r.status_code == 200 and b"AC1032" in r.content[:2000]  # R2018
    assert client.get(f"/projects/{pid}/diagrams/{d['id']}/export/dwg").status_code == 422
    plan = client.post(f"/projects/{pid}/exports/plan/pdf", json=body)
    assert plan.status_code == 200 and "constructos-plan-" in plan.headers["content-disposition"]
    with pdfplumber.open(io.BytesIO(plan.content)) as pdf:
        text = pdf.pages[0].extract_text()
        assert "Floor plan" in text and "m²" in text and "Export house" in text
    assert store.backend().get_blob(f"projects/{pid}/artifacts/{d['id']}.pdf") is not None
    # Diagram listing comes from summaries; the payload is an immutable blob.
    listed = client.get(f"/projects/{pid}/diagrams").json()
    assert [x["id"] for x in listed] == [d["id"]] and "blob" not in listed[0]
    assert client.get(f"/projects/{pid}/diagrams/{d['id']}").json()["routes"] == d["routes"]


# --- Jobs ----------------------------------------------------------------------------

def _queued_run(pid: str) -> dict:
    run = {"id": str(uuid.uuid4()), "created_at": store._now(), "status": "queued", "job": jobs.new_job(1),
           "brief_version": 1, "brief_hash": "sha256-x", "program": {}, "candidates": []}
    return store.save_layout_run(pid, run)


def test_job_publishes_once_and_records_failures():
    pid = store.create("Jobs")["id"]
    ok = _queued_run(pid)
    jobs.start(pid, ok, lambda job: job.update(lambda r: r["candidates"].append({"n": 1})))
    bad = _queued_run(pid)
    jobs.start(pid, bad, lambda job: 1 / 0)
    assert jobs.wait_idle(10)
    ok, bad = store.get_layout_run(pid, ok["id"]), store.get_layout_run(pid, bad["id"])
    assert ok["status"] == "complete" and ok["candidates"] == [{"n": 1}] and ok["job"]["lease"] is None
    assert bad["status"] == "failed" and "ZeroDivisionError" in bad["job"]["error"]


def test_stale_job_is_interrupted_and_a_late_worker_cannot_overwrite_it(monkeypatch):
    pid = store.create("Stale")["id"]
    run = _queued_run(pid)
    late = jobs.Job(pid, run["id"], run["job"]["lease"])
    monkeypatch.setattr(jobs, "STALE_S", 0.0)
    time.sleep(0.01)
    after = jobs.reconcile(pid, store.get_layout_run(pid, run["id"]))
    assert after["status"] == "interrupted" and after["job"]["lease"] is None
    with pytest.raises(jobs.LeaseLost):
        late.update(lambda r: r["candidates"].append({"late": True}))
    assert store.get_layout_run(pid, run["id"])["candidates"] == []


def test_fresh_job_is_not_interrupted():
    pid = store.create("Fresh")["id"]
    run = _queued_run(pid)
    assert jobs.reconcile(pid, run)["status"] == "queued"


# --- Store ---------------------------------------------------------------------------

def test_old_single_file_projects_are_migrated(tmp_path):
    pid = str(uuid.uuid4())
    old = {"id": pid, "name": "Old", "created_at": store._now(), "brief_draft": None, "brief_versions": [],
           "layout_runs": [{"id": str(uuid.uuid4()), "created_at": store._now(), "brief_version": 1, "candidates": []}]}
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "projects" / f"{pid}.json").write_text(json.dumps(old))
    project = store.get(pid)
    assert "layout_runs" not in project
    runs = store.list_layout_runs(pid)
    assert len(runs) == 1 and runs[0]["status"] == "complete"
    assert "layout_runs" not in json.loads((tmp_path / "projects" / f"{pid}.json").read_text())


def test_ids_cannot_escape_the_store():
    with pytest.raises(store.NotFound):
        store.get("../../etc/passwd")
    with pytest.raises(store.NotFound):
        store.get_layout_run(str(uuid.uuid4()), "../x")


@pytest.mark.skipif(os.getenv("CONSTRUCTOS_TEST_GCP") != "1", reason="set CONSTRUCTOS_TEST_GCP=1 to run against Firestore/GCS")
def test_gcp_backend_round_trip():
    prefix = f"test-runs/{uuid.uuid4().hex[:8]}/"  # a collection/document pair, so paths stay valid
    b = store.GcpBackend(project=os.environ["GOOGLE_CLOUD_PROJECT"], bucket=os.environ["CONSTRUCTOS_BUCKET"], prefix=prefix)
    store.use_backend(b)
    try:
        p = store.create("GCP round trip")
        run = _queued_run(p["id"])
        run["candidates"] = [{"polygon": [[0, 0], [1, 1]]}]  # nested arrays are fine: documents are stored as JSON
        store.save_layout_run(p["id"], run)
        store.update_candidate(p["id"], run["id"], 0, "review", {"ok": True})
        assert store.get_layout_run(p["id"], run["id"])["candidates"][0]["review"] == {"ok": True}
        diagram = {"id": str(uuid.uuid4()), "kind": "plumbing", "created_at": store._now(), "editor_project_id": "e",
                   "source_hash": "sha256-x", "status": "complete", "metrics": {"improvement_pct": None,
                   "endpoints_connected": 1, "endpoints_total": 1}, "routes": [{"points": [[0, 1, 2]]}]}
        store.save_diagram(p["id"], diagram)
        assert store.get_diagram(p["id"], diagram["id"]) == diagram
        assert store.list_diagrams(p["id"])[0]["blob"].startswith("gs://")
        assert [x["id"] for x in store.list_all()] == [p["id"]]
    finally:
        for doc in b.db.collection(f"{prefix}projects").list_documents():
            for coll in doc.collections():
                for d in coll.list_documents():
                    d.delete()
            doc.delete()
        for blob in b.bucket.list_blobs(prefix=prefix):
            blob.delete()
