"""Vertex AI is primary in the cloud; the Gemini API key takes over on quota/availability errors."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from constructos_engine import gemini
from constructos_engine.config import load_settings


class Backend:
    def __init__(self, fail_code: int | None = None):
        self.fail_code, self.calls = fail_code, 0
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls += 1
        if self.fail_code:
            raise errors.APIError(self.fail_code, {"error": {"code": self.fail_code, "message": "x", "status": "X"}})
        return SimpleNamespace(text="ok")


@pytest.fixture
def backends(monkeypatch):
    monkeypatch.setattr(gemini, "_switched_until", 0.0)
    monkeypatch.setattr(gemini, "_primary_failures", [])
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")

    def use(primary: Backend, fallback: Backend | None):
        monkeypatch.setattr(gemini, "get_client", lambda: primary)
        monkeypatch.setattr(gemini, "get_fallback_client", lambda: fallback)
    return use


@pytest.mark.parametrize("code", [429, 403, 404, 503])
def test_quota_and_availability_errors_fall_back_once(backends, code):
    primary, fallback = Backend(code), Backend()
    backends(primary, fallback)
    assert gemini.generate_content(model="m", contents="c", config=None).text == "ok"
    assert (primary.calls, fallback.calls) == (1, 1)


def test_request_errors_are_not_retried_elsewhere(backends):
    primary, fallback = Backend(400), Backend()
    backends(primary, fallback)
    with pytest.raises(errors.APIError):
        gemini.generate_content(model="m", contents="c", config=None)
    assert fallback.calls == 0


def test_without_a_fallback_the_error_surfaces(backends):
    backends(Backend(429), None)
    with pytest.raises(errors.APIError):
        gemini.generate_content(model="m", contents="c", config=None)


def test_vertex_with_a_key_reports_the_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = load_settings()
    assert s.ai_configured and s.ai_backend == "vertex (fallback: gemini-api)"


def test_a_call_that_times_out_falls_back(backends):
    import httpx

    class Hanging(Backend):
        def _generate(self, **kwargs):
            self.calls += 1
            raise httpx.ReadTimeout("timed out")

    primary, fallback = Hanging(), Backend()
    primary.models = SimpleNamespace(generate_content=primary._generate)
    backends(primary, fallback)
    assert gemini.generate_content(model="m", contents="c", config=None).text == "ok"
    assert (primary.calls, fallback.calls) == (1, 1)


def test_repeated_primary_failures_switch_the_fallback_to_lead_for_a_while(backends, monkeypatch):
    primary, fallback = Backend(429), Backend()
    backends(primary, fallback)
    gemini.generate_content(model="m", contents="c", config=None)
    assert [n for n, _ in gemini.clients()][0] == "vertex"  # one failure: still primary first
    gemini.generate_content(model="m", contents="c", config=None)
    assert [n for n, _ in gemini.clients()] == ["gemini-api", "vertex"]  # second failure: switched
    gemini.generate_content(model="m", contents="c", config=None)
    assert (primary.calls, fallback.calls) == (2, 3)  # while switched, the failing primary is not tried first
    monkeypatch.setattr(gemini, "_switched_until", 0.0)  # cooldown over
    assert [n for n, _ in gemini.clients()][0] == "vertex"


def test_errors_of_the_request_itself_do_not_switch(backends):
    primary, fallback = Backend(400), Backend()
    backends(primary, fallback)
    for _ in range(3):
        with pytest.raises(errors.APIError):
            gemini.generate_content(model="m", contents="c", config=None)
    assert [n for n, _ in gemini.clients()][0] == "vertex"


def test_the_api_key_fallback_uses_the_gemini_api_even_when_vertex_is_the_default(monkeypatch):
    """With GOOGLE_GENAI_USE_VERTEXAI=true the SDK would route a key to Vertex AI, which refuses keys."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    gemini.get_client.cache_clear()
    gemini.get_fallback_client.cache_clear()
    try:
        primary, fallback = gemini.get_client(), gemini.get_fallback_client()
        assert primary._api_client.vertexai is True
        assert fallback is not None and fallback._api_client.vertexai is False
        assert "generativelanguage.googleapis.com" in fallback._api_client._http_options.base_url
    finally:
        gemini.get_client.cache_clear()
        gemini.get_fallback_client.cache_clear()
