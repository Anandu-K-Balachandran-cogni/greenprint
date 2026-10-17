"""Photo-real renders: API, storage, limits and model choice (Gemini stubbed)."""

import base64

from fastapi.testclient import TestClient

from constructos_engine import gemini, ledger, render
from constructos_engine.main import app

JPEG = b"\xff\xd8\xff\xe0fake-capture"
PNG = b"\x89PNG\r\n\x1a\nfake-render"


def test_render_is_stored_served_and_listed(monkeypatch):
    seen = {}

    def fake(image, mime, kind, style=None):
        seen.update(image=image, mime=mime, kind=kind, style=style)
        return PNG, "image/png", "gemini-test-image"
    monkeypatch.setattr(render, "render", fake)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Render"}).json()["id"]
    r = client.post(f"/projects/{pid}/renders", json={"image": base64.b64encode(JPEG).decode(), "kind": "room", "room": "Living room",
                                                    "style": "Japandi calm", "editor_project_id": "cos-1"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert seen == {"image": JPEG, "mime": "image/jpeg", "kind": "room", "style": "Japandi calm"}
    assert out["model"] == "gemini-test-image" and out["image_path"] == f"projects/{pid}/renders/{out['id']}/image"
    img = client.get(f"/{out['image_path']}")
    assert img.status_code == 200 and img.content == PNG and img.headers["content-type"] == "image/png"
    assert [x["id"] for x in client.get(f"/projects/{pid}/renders?editor=cos-1").json()] == [out["id"]]
    assert client.get(f"/projects/{pid}/renders?editor=other").json() == []
    assert any(s["action"] == "rendered" for s in ledger.steps(pid))
    assert client.get(f"/projects/{pid}/renders/00000000-0000-0000-0000-000000000000/image").status_code == 404


def test_render_rejects_bad_input_failures_and_the_project_limit(monkeypatch):
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Render"}).json()["id"]
    assert client.post(f"/projects/{pid}/renders", json={"image": "!!not-base64!!"}).status_code == 422
    monkeypatch.setattr(render, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no image")))
    failed = client.post(f"/projects/{pid}/renders", json={"image": base64.b64encode(JPEG).decode()})
    assert failed.status_code == 502 and failed.json()["detail"]["code"] == "RENDER_FAILED"
    monkeypatch.setattr(render, "render", lambda *a, **k: (PNG, "image/png", "m"))
    monkeypatch.setattr(render, "MAX_RENDERS_PER_PROJECT", 2)
    for _ in range(2):
        assert client.post(f"/projects/{pid}/renders", json={"image": base64.b64encode(JPEG).decode()}).status_code == 201
    capped = client.post(f"/projects/{pid}/renders", json={"image": base64.b64encode(JPEG).decode()})
    assert capped.status_code == 429 and capped.json()["detail"]["code"] == "RENDER_LIMIT"


def test_image_model_ranking_prefers_newest_stable_flash():
    names = ["models/gemini-2.5-flash-image", "models/gemini-3-pro-image", "models/gemini-3.1-flash-image-preview",
             "models/gemini-3.1-flash-image", "models/gemini-3.1-flash-lite-image", "models/gemini-3.8-flash"]
    assert gemini.rank_image_models(names)[:3] == ["gemini-3.1-flash-image", "gemini-3.1-flash-image-preview", "gemini-3-pro-image"]
