"""Project store.

Projects, layout runs and diagram summaries are small mutable documents; diagram
payloads and exported drawings are immutable blobs. Two backends keep the same
semantics:

- ``files`` (default, local development): JSON documents and blob files under
  ``CONSTRUCTOS_DATA_DIR``, with a process lock for read-modify-write.
- ``gcp``: Firestore documents (``projects/{id}/layout_runs/{id}`` ...) updated
  in transactions, and Cloud Storage objects for blobs.

Confirmed brief versions are append-only and carry a content hash; writes check
the caller's expected version. Jobs mutate a run only while they hold its lease
(see ``mutate_run``), so a late or interrupted worker cannot overwrite a result.
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .brief import Brief, brief_hash

DATA_DIR = Path(os.getenv("CONSTRUCTOS_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))


class NotFound(KeyError):
    pass


class VersionConflict(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_id(value: str) -> str:
    if not value or len(value) > 80 or not value.replace("-", "").isalnum():
        raise NotFound(value)
    return value


# --- Backends -----------------------------------------------------------------

class FileBackend:
    """Documents as JSON files: <data>/<collection path>/<id>.json; blobs under <data>/blobs/."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()

    def _doc(self, collection: str, doc_id: str) -> Path:
        return self.root / collection / f"{_check_id(doc_id)}.json"

    @staticmethod
    def _write(path: Path, text: str | bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        (tmp.write_bytes if isinstance(text, bytes) else tmp.write_text)(text)
        os.replace(tmp, path)

    def get(self, collection: str, doc_id: str) -> dict | None:
        path = self._doc(collection, doc_id)
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, collection: str, doc_id: str, doc: dict) -> None:
        with self._lock:
            self._write(self._doc(collection, doc_id), json.dumps(doc, indent=1))

    def list(self, collection: str) -> list[dict]:
        folder = self.root / collection
        return [json.loads(p.read_text()) for p in folder.glob("*.json")] if folder.exists() else []

    def mutate(self, collection: str, doc_id: str, fn: Callable[[dict], dict]) -> dict:
        with self._lock:
            doc = self.get(collection, doc_id)
            if doc is None:
                raise NotFound(doc_id)
            doc = fn(doc)
            self.put(collection, doc_id, doc)
            return doc

    def put_blob(self, name: str, data: bytes, content_type: str) -> str:
        self._write(self.root / "blobs" / name, data)
        return f"file://{self.root / 'blobs' / name}"

    def get_blob(self, name: str) -> bytes | None:
        path = self.root / "blobs" / name
        return path.read_bytes() if path.exists() else None


class GcpBackend:
    """Firestore documents (stored as a JSON string, since Firestore forbids nested arrays) and GCS blobs."""

    def __init__(self, project: str, bucket: str, database: str = "(default)", prefix: str = ""):
        from google.cloud import firestore, storage
        self._fs = firestore
        self.db = firestore.Client(project=project, database=database)
        self.bucket = storage.Client(project=project).bucket(bucket)
        self.prefix = prefix  # "collection/doc/" root that tests use to isolate themselves

    def _ref(self, collection: str, doc_id: str):
        return self.db.document(f"{self.prefix}{collection}/{_check_id(doc_id)}")

    @staticmethod
    def _encode(doc: dict) -> dict:
        return {"json": json.dumps(doc), "created_at": doc.get("created_at", ""), "updated_at": _now()}

    def get(self, collection: str, doc_id: str) -> dict | None:
        snap = self._ref(collection, doc_id).get()
        return json.loads(snap.get("json")) if snap.exists else None

    def put(self, collection: str, doc_id: str, doc: dict) -> None:
        self._ref(collection, doc_id).set(self._encode(doc))

    def list(self, collection: str) -> list[dict]:
        return [json.loads(s.get("json")) for s in self.db.collection(f"{self.prefix}{collection}").stream()]

    def mutate(self, collection: str, doc_id: str, fn: Callable[[dict], dict]) -> dict:
        ref = self._ref(collection, doc_id)
        result: dict = {}

        @self._fs.transactional
        def run(tx):
            snap = ref.get(transaction=tx)
            if not snap.exists:
                raise NotFound(doc_id)
            doc = fn(json.loads(snap.get("json")))
            tx.set(ref, self._encode(doc))
            result["doc"] = doc

        run(self.db.transaction())
        return result["doc"]

    def put_blob(self, name: str, data: bytes, content_type: str) -> str:
        blob = self.bucket.blob(f"{self.prefix}{name}")
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self.bucket.name}/{blob.name}"

    def get_blob(self, name: str) -> bytes | None:
        blob = self.bucket.blob(f"{self.prefix}{name}")
        return blob.download_as_bytes() if blob.exists() else None


_backend = None
_backend_lock = threading.Lock()


def backend():
    global _backend
    with _backend_lock:
        if _backend is None:
            kind = os.getenv("CONSTRUCTOS_STORE", "files").strip().lower()
            if kind == "gcp":
                _backend = GcpBackend(project=os.environ["GOOGLE_CLOUD_PROJECT"], bucket=os.environ["CONSTRUCTOS_BUCKET"],
                                      database=os.getenv("CONSTRUCTOS_FIRESTORE_DB", "(default)"))
            else:
                _backend = FileBackend(DATA_DIR)
        return _backend


def use_backend(b) -> None:
    """Swap the backend (tests)."""
    global _backend
    with _backend_lock:
        _backend = b


def _runs(project_id: str) -> str:
    return f"projects/{_check_id(project_id)}/layout_runs"


def _diagrams(project_id: str) -> str:
    return f"projects/{_check_id(project_id)}/diagrams"


# --- Projects and briefs ------------------------------------------------------

def _migrate(project: dict) -> dict:
    """Older local projects embedded runs and diagrams in the project file; move them out once."""
    if "layout_runs" not in project and "diagrams" not in project:
        return project
    b = backend()
    for run in project.pop("layout_runs", []):
        run.setdefault("status", "complete")
        b.put(_runs(project["id"]), run["id"], run)
    for d in project.pop("diagrams", []):
        _write_diagram(project["id"], d)
    b.put("projects", project["id"], project)
    return project


def get(project_id: str) -> dict:
    project = backend().get("projects", _check_id(project_id))
    if project is None:
        raise NotFound(project_id)
    return _migrate(project)


def create(name: str) -> dict:
    project = {
        "id": str(uuid.uuid4()),
        "name": name.strip()[:120] or "Untitled building",
        "created_at": _now(),
        "brief_draft": None,
        "brief_versions": [],
    }
    backend().put("projects", project["id"], project)
    return project


def set_site(project_id: str, site: dict) -> dict:
    """Where the plot is (pinned on Google Maps): lat/lng, address, city, country."""
    def fn(project: dict) -> dict:
        project["site"] = site
        return project
    return backend().mutate("projects", _check_id(project_id), fn)


def list_all() -> list[dict]:
    items = [_migrate(p) for p in backend().list("projects")]
    return sorted(
        ({"id": p["id"], "name": p["name"], "created_at": p["created_at"],
          "brief_version": len(p["brief_versions"])} for p in items),
        key=lambda p: p["created_at"], reverse=True,
    )


def save_draft(project_id: str, draft: Brief) -> dict:
    get(project_id)

    def apply(project: dict) -> dict:
        project["brief_draft"] = draft.model_dump(mode="json")
        return project
    return backend().mutate("projects", project_id, apply)


def confirm_brief(project_id: str, brief: Brief, expected_version: int) -> dict:
    get(project_id)
    entry: dict = {}

    def apply(project: dict) -> dict:
        current = len(project["brief_versions"])
        if expected_version != current:
            raise VersionConflict(f"expected brief version {expected_version}, current is {current}")
        entry.update({"version": current + 1, "hash": brief_hash(brief), "confirmed_at": _now(),
                      "brief": brief.model_dump(mode="json")})
        project["brief_versions"].append(entry)
        return project
    backend().mutate("projects", project_id, apply)
    return entry


def current_brief(project: dict) -> dict | None:
    return project["brief_versions"][-1] if project["brief_versions"] else None


def brief_version(project: dict, version: int) -> dict:
    for v in project["brief_versions"]:
        if v["version"] == version:
            return v
    raise NotFound(f"brief v{version}")


# --- Layout runs --------------------------------------------------------------

def save_layout_run(project_id: str, run: dict) -> dict:
    backend().put(_runs(project_id), run["id"], run)
    return run


def get_layout_run(project_id: str, run_id: str) -> dict:
    run = backend().get(_runs(project_id), run_id)
    if run is None:
        raise NotFound(run_id)
    return run


def list_layout_runs(project_id: str) -> list[dict]:
    return sorted(backend().list(_runs(project_id)), key=lambda r: r["created_at"])


def mutate_run(project_id: str, run_id: str, fn: Callable[[dict], dict]) -> dict:
    return backend().mutate(_runs(project_id), run_id, fn)


def update_candidate(project_id: str, run_id: str, k: int, key: str, value) -> None:
    def apply(run: dict) -> dict:
        run["candidates"][k][key] = value
        return run
    mutate_run(project_id, run_id, apply)


# --- Diagrams and artifacts ---------------------------------------------------

def _diagram_blob(project_id: str, diagram_id: str) -> str:
    return f"projects/{_check_id(project_id)}/diagrams/{_check_id(diagram_id)}.json"


def diagram_summary(d: dict) -> dict:
    m = d["metrics"]
    return {"id": d["id"], "kind": d["kind"], "created_at": d["created_at"], "editor_project_id": d["editor_project_id"],
            "source_hash": d["source_hash"], "status": d["status"], "improvement_pct": m["improvement_pct"],
            "endpoints_connected": m["endpoints_connected"], "endpoints_total": m["endpoints_total"]}


def _write_diagram(project_id: str, diagram: dict) -> None:
    b = backend()
    uri = b.put_blob(_diagram_blob(project_id, diagram["id"]), json.dumps(diagram).encode(), "application/json")
    b.put(_diagrams(project_id), diagram["id"], {**diagram_summary(diagram), "blob": uri})


def save_diagram(project_id: str, diagram: dict) -> dict:
    """The full diagram is an immutable blob; the document holds its summary for listing."""
    _write_diagram(project_id, diagram)
    return diagram


def list_diagrams(project_id: str) -> list[dict]:
    return sorted(backend().list(_diagrams(project_id)), key=lambda d: d["created_at"])


def get_diagram(project_id: str, diagram_id: str) -> dict:
    data = backend().get_blob(_diagram_blob(project_id, diagram_id))
    if data is None:
        raise NotFound(diagram_id)
    return json.loads(data)


def save_artifact(project_id: str, name: str, data: bytes, content_type: str) -> str:
    return backend().put_blob(f"projects/{_check_id(project_id)}/artifacts/{name}", data, content_type)


# --- Cost estimates -------------------------------------------------------------

def save_estimate(project_id: str, est: dict) -> dict:
    b = backend()
    uri = b.put_blob(f"projects/{_check_id(project_id)}/estimates/{_check_id(est['id'])}.json", json.dumps(est).encode(), "application/json")
    b.put(f"projects/{_check_id(project_id)}/estimates", est["id"], {
        "id": est["id"], "created_at": est["created_at"], "tier": est["tier"], "location": est["location"], "total": est["total"],
        "per_sqft": est["per_sqft"], "source_hash": est["source_hash"], "quantities_hash": est.get("quantities_hash"),
        "editor_project_id": est.get("editor_project_id"), "blob": uri})
    return est


def list_estimates(project_id: str) -> list[dict]:
    return sorted(backend().list(f"projects/{_check_id(project_id)}/estimates"), key=lambda d: d["created_at"])


def get_estimate(project_id: str, estimate_id: str) -> dict:
    data = backend().get_blob(f"projects/{_check_id(project_id)}/estimates/{_check_id(estimate_id)}.json")
    if data is None:
        raise NotFound(estimate_id)
    return json.loads(data)


# --- Interior designs (agent proposals the editor previews and accepts) ---------------------

def save_design(project_id: str, design: dict) -> dict:
    b = backend()
    uri = b.put_blob(f"projects/{_check_id(project_id)}/designs/{_check_id(design['id'])}.json", json.dumps(design).encode(), "application/json")
    b.put(f"projects/{_check_id(project_id)}/designs", design["id"], {
        "id": design["id"], "created_at": design["created_at"], "title": design["brief"].get("title"),
        "base_theme": design["brief"].get("base_theme"), "editor_project_id": design.get("editor_project_id"),
        "total": design["budget"]["total"], "blob": uri})
    return design


def list_designs(project_id: str) -> list[dict]:
    return sorted(backend().list(f"projects/{_check_id(project_id)}/designs"), key=lambda d: d["created_at"])


def get_design(project_id: str, design_id: str) -> dict:
    data = backend().get_blob(f"projects/{_check_id(project_id)}/designs/{_check_id(design_id)}.json")
    if data is None:
        raise NotFound(design_id)
    return json.loads(data)


# --- Renders (photo-real images of the 3D model) -------------------------------------------

def save_render(project_id: str, meta: dict, image: bytes, mime: str) -> dict:
    b = backend()
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    uri = b.put_blob(f"projects/{_check_id(project_id)}/renders/{_check_id(meta['id'])}.{ext}", image, mime)
    doc = {**meta, "mime": mime, "blob": uri, "ext": ext}
    b.put(f"projects/{_check_id(project_id)}/renders", meta["id"], doc)
    return doc


def list_renders(project_id: str) -> list[dict]:
    return sorted(backend().list(f"projects/{_check_id(project_id)}/renders"), key=lambda d: d["created_at"])


def get_render(project_id: str, render_id: str) -> tuple[dict, bytes]:
    doc = backend().get(f"projects/{_check_id(project_id)}/renders", render_id)
    if doc is None:
        raise NotFound(render_id)
    data = backend().get_blob(f"projects/{_check_id(project_id)}/renders/{_check_id(render_id)}.{doc['ext']}")
    if data is None:
        raise NotFound(render_id)
    return doc, data


# --- Green performance reports -------------------------------------------------------------

def save_green(project_id: str, report: dict) -> dict:
    b = backend()
    uri = b.put_blob(f"projects/{_check_id(project_id)}/green/{_check_id(report['id'])}.json", json.dumps(report).encode(), "application/json")
    b.put(f"projects/{_check_id(project_id)}/green", report["id"], {
        "id": report["id"], "created_at": report["created_at"], "city": report["climate"]["city"], "score": report["report"]["score"]["total"],
        "editor_project_id": report.get("editor_project_id"), "blob": uri})
    return report


def list_green(project_id: str) -> list[dict]:
    return sorted(backend().list(f"projects/{_check_id(project_id)}/green"), key=lambda d: d["created_at"])


def get_green(project_id: str, report_id: str) -> dict:
    data = backend().get_blob(f"projects/{_check_id(project_id)}/green/{_check_id(report_id)}.json")
    if data is None:
        raise NotFound(report_id)
    return json.loads(data)
