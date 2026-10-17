"""Sketch and plot-drawing intake: request checks and ledger (Gemini stubbed). Live accuracy: evals/sketch_intake_eval.py."""

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from constructos_engine import intake, ledger
from constructos_engine.main import app

SKETCH = (Path(__file__).parent / "fixtures/sketches/plot_survey.png").read_bytes()
b64 = lambda data: base64.b64encode(data).decode()  # noqa: E731


def test_read_sketch_passes_images_and_text_to_gemini_and_records_it(monkeypatch):
    seen = {}

    def fake_read(text, images):
        seen.update(text=text, images=images)
        return intake.SketchReading(english_brief="A single-floor house on a 30 x 40 ft plot.", language="Hindi",
                                    image_notes=["Plot 30 x 40 ft, road on the south side."], unclear=[])
    monkeypatch.setattr(intake, "read", fake_read)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Sketch"}).json()["id"]
    r = client.post(f"/projects/{pid}/brief/read", json={"text": "पूजा कमरा चाहिए", "images": [{"data": b64(SKETCH), "mime": "image/png"}]})
    assert r.status_code == 200, r.text
    assert r.json()["english_brief"].startswith("A single-floor house") and r.json()["language"] == "Hindi"
    assert seen["text"] == "पूजा कमरा चाहिए" and seen["images"] == [(SKETCH, "image/png")]
    step = [s for s in ledger.steps(pid) if s["action"] == "read_sketch"][0]
    assert "1 image(s)" in step["summary"] and "Hindi" in step["summary"] and "30 x 40" in step["summary"]


def test_read_sketch_rejects_bad_requests(monkeypatch):
    monkeypatch.setattr(intake, "read", lambda *a: (_ for _ in ()).throw(AssertionError("must not call Gemini")))
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Sketch"}).json()["id"]
    url = f"/projects/{pid}/brief/read"
    assert client.post(url, json={"text": "x", "images": []}).status_code == 422                                   # no image
    assert client.post(url, json={"images": [{"data": b64(SKETCH), "mime": "image/gif"}]}).status_code == 422       # type
    assert client.post(url, json={"images": [{"data": b64(SKETCH), "mime": "image/png"}] * 4}).status_code == 422   # count
    bad = client.post(url, json={"images": [{"data": "not base64!!", "mime": "image/png"}]})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "IMAGE_INVALID"
    big = client.post(url, json={"images": [{"data": b64(b"x" * (3 * 1024 * 1024 + 1)), "mime": "image/jpeg"}]})
    assert big.status_code == 413
    assert client.post("/projects/00000000-0000-0000-0000-000000000000/brief/read",
                       json={"images": [{"data": b64(SKETCH), "mime": "image/png"}]}).status_code == 404
