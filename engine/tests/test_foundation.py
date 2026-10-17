from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from constructos_engine import gemini
from constructos_engine.main import app
from constructos_engine.units import solver_mm_floor, to_cm, to_mm


def test_original_plot_converts_exactly():
    assert to_mm(40, "ft") == Decimal("12192.0")
    assert to_mm(60, "ft") == Decimal("18288.0")
    assert to_cm(40, "ft") == Decimal("1219.2")
    assert to_cm(60, "ft") == Decimal("1828.8")


def test_solver_rounding_never_expands_the_plot():
    assert solver_mm_floor(10.75, "in") == 273  # 273.05 mm
    assert solver_mm_floor(40, "ft") == 12192


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        to_mm(1, "yd")  # type: ignore[arg-type]


def test_model_ranking_prefers_newest_then_stable():
    names = [
        "models/gemini-2.5-pro",
        "models/gemini-3-pro-preview",
        "models/gemini-2.5-flash",
        "models/gemini-2.5-flash-lite",
        "models/gemini-3-flash-preview",
        "models/gemini-2.5-flash-image",
        "models/gemini-pro-latest",
        "models/text-embedding-004",
        "publishers/google/models/gemini-3.5-pro",
        "models/gemini-3.5-pro-preview-06-2026",
        "models/gemini-3.5-pro-preview-customtools",
        "models/gemini-3.5-flash",
        "models/gemini-3.8-flash",
    ]
    assert gemini.rank_models(names, ("pro",)) == [
        "gemini-3.5-pro",
        "gemini-3.5-pro-preview-06-2026",
        "gemini-3-pro-preview",
        "gemini-2.5-pro",
    ]
    assert gemini.rank_models(names, ("pro", "flash"))[:3] == ["gemini-3.8-flash", "gemini-3.5-pro", "gemini-3.5-pro-preview-06-2026"]
    assert gemini.rank_models(names, ("flash",))[:2] == ["gemini-3.8-flash", "gemini-3.5-flash"]


def test_measure_tool_is_exact():
    assert gemini.measure_rectangle(12192, 18288) == {"area_mm2": 222967296, "area_m2": 222.9673}
    with pytest.raises(ValueError):
        gemini.measure_rectangle(0, 5)


def test_health_reports_ai_configuration():
    body = TestClient(app).get("/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["ai_configured"], bool)
