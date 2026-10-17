"""Gemini usage log: tagging by feature and project, and the summary used by the benchmark report."""

import base64

from fastapi.testclient import TestClient

from constructos_engine import intake, usage
from constructos_engine.main import app


def test_calls_are_tagged_with_feature_and_project_and_summarised(monkeypatch):
    def fake_read(text, images):
        usage.record(model="gemini-x", seconds=4.0, prompt_tokens=1200, output_tokens=300)
        return intake.SketchReading(english_brief="A house.", language="none", image_notes=["n"], unclear=[])
    monkeypatch.setattr(intake, "read", fake_read)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "Usage"}).json()["id"]
    img = base64.b64encode(b"\x89PNG fake").decode()
    for _ in range(2):
        assert client.post(f"/projects/{pid}/brief/read", json={"images": [{"data": img, "mime": "image/png"}]}).status_code == 200
    usage.record(model="gemini-image", seconds=9.0, prompt_tokens=500, output_tokens=1300, images=1, purpose="renders")
    s = client.get(f"/ai/usage?project_id={pid}").json()
    row = next(r for r in s["by_purpose"] if r["purpose"] == "brief/read")
    assert row["calls"] == 2 and row["prompt_tokens"] == 2400 and row["output_tokens"] == 600 and row["median_s"] == 4.0
    assert s["calls"] == 2   # the render above had no project in context
    p = s["prices_usd"]
    assert abs(row["est_usd"] - (2400 / 1e6 * p["in_per_m"] + 600 / 1e6 * p["out_per_m"])) < 1e-4
    everything = client.get("/ai/usage").json()
    assert any(r["purpose"] == "renders" and r["images"] == 1 for r in everything["by_purpose"])
