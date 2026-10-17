"""Gemini client, model discovery and the tool-execution smoke test.

The engine never hard-codes a model ID. Unless a model is pinned in .env, it
asks the API which models exist and selects the newest-version model
(Pro preferred on a tie) for reasoning and the newest Flash for fast work.

When both Vertex AI and a Gemini API key are configured, the configured backend
is primary and the other is a fallback: a call that fails with a quota,
permission, missing-model, server or timeout error is retried once on the
fallback (new Google Cloud projects often start with low Vertex AI quotas). If
the primary fails repeatedly (SWITCH_AFTER failures within SWITCH_WINDOW_S),
the fallback leads for SWITCH_COOLDOWN_S so users stop paying the failure
latency, and then the primary is tried again.
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

from google import genai
from google.genai import errors, types

from .config import Settings, load_settings

log = logging.getLogger(__name__)
FALLBACK_CODES = {403, 404, 429, 500, 502, 503, 504}
# A call that has not answered in this time is abandoned (and retried on the fallback): typical calls
# take 10-40 s, and an unanswered request must never hold a user's request until the platform timeout.
CALL_TIMEOUT_MS = int(os.getenv("GEMINI_CALL_TIMEOUT_S", "60")) * 1000
SWITCH_AFTER, SWITCH_WINDOW_S, SWITCH_COOLDOWN_S = 2, 600, 900
_health_lock = threading.Lock()
_primary_failures: list[float] = []
_switched_until = 0.0
_MODEL_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-(pro|flash)(-preview(?:-\d+)*)?$")


@dataclass(frozen=True, order=True)
class _Rank:
    version: float
    pro: bool
    stable: bool
    name: str


def _short_name(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def rank_models(names: list[str], tiers: tuple[str, ...]) -> list[str]:
    """Newest version first; at equal version Pro beats Flash and stable beats preview.

    Only plain `gemini-<version>-<tier>[-preview[-date]]` text models qualify, so
    lite, image, TTS, `-latest` aliases and specialised variants are excluded.
    """
    ranked: list[_Rank] = []
    for raw in names:
        name = _short_name(raw)
        m = _MODEL_RE.match(name)
        if m and m.group(2) in tiers:
            ranked.append(_Rank(float(m.group(1)), m.group(2) == "pro", m.group(3) is None, name))
    return [r.name for r in sorted(ranked, reverse=True)]


class AINotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    s = load_settings()
    if not s.ai_configured:
        raise AINotConfigured(
            "Set GEMINI_API_KEY, or GOOGLE_GENAI_USE_VERTEXAI=true with GOOGLE_CLOUD_PROJECT, in .env"
        )
    if s.use_vertex:
        return _vertex_client(s)
    return _api_key_client(s)


def _api_key_client(s: Settings) -> genai.Client:
    # vertexai=False explicitly: with GOOGLE_GENAI_USE_VERTEXAI=true in the environment the SDK would
    # otherwise send the key to the Vertex AI endpoint, which rejects API keys (401).
    return genai.Client(vertexai=False, api_key=s.gemini_api_key, http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS))


def _vertex_client(s: Settings) -> genai.Client:
    return genai.Client(vertexai=True, project=s.gcp_project, location=s.gcp_location,
                        http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS))


@lru_cache(maxsize=1)
def get_fallback_client() -> genai.Client | None:
    """The other backend, when it is configured too (API key behind Vertex AI, or Vertex AI behind a key)."""
    s = load_settings()
    if s.use_vertex and s.gemini_api_key:
        return _api_key_client(s)
    if not s.use_vertex and s.gcp_project and s.vertex_fallback:
        return _vertex_client(s)
    return None


def clients() -> list[tuple[str, genai.Client]]:
    """Backends in the order to try them: the fallback leads while the primary is switched out."""
    s = load_settings()
    primary = "vertex" if s.use_vertex else "gemini-api"
    out = [(primary, get_client())]
    fallback = get_fallback_client()
    if fallback is not None:
        out.append(("gemini-api" if s.use_vertex else "vertex", fallback))
        with _health_lock:
            if time.monotonic() < _switched_until:
                out.reverse()
    return out


def _record_primary_failure(now: float) -> None:
    global _switched_until
    with _health_lock:
        _primary_failures[:] = [t for t in _primary_failures if now - t < SWITCH_WINDOW_S] + [now]
        if len(_primary_failures) >= SWITCH_AFTER:
            _switched_until = now + SWITCH_COOLDOWN_S
            _primary_failures.clear()
            log.warning("Gemini primary failed %d times in %d s; fallback leads for %d s",
                        SWITCH_AFTER, SWITCH_WINDOW_S, SWITCH_COOLDOWN_S)


def backend_status() -> dict:
    s = load_settings()
    with _health_lock:
        left = max(0.0, _switched_until - time.monotonic())
    return {"configured": s.ai_backend, "active": clients()[0][0] if s.ai_configured else None,
            "switched_for_s": round(left)}


def _should_fall_back(e: Exception) -> bool:
    import httpx
    if isinstance(e, (httpx.TimeoutException, httpx.NetworkError, TimeoutError)):
        return True
    return isinstance(e, errors.APIError) and getattr(e, "code", None) in FALLBACK_CODES


def generate_content(*, model: str, contents, config):
    """generate_content on the primary backend, retried once on the fallback for quota/availability errors."""
    available = clients()
    for i, (name, client) in enumerate(available):
        start = time.monotonic()
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            log.info("Gemini %s %s answered in %.1f s", name, model, time.monotonic() - start)
            from . import usage
            usage.from_response(response, model=model, seconds=time.monotonic() - start)
            return response
        except Exception as e:
            log.warning("Gemini %s %s failed after %.1f s: %s: %s", name, model, time.monotonic() - start,
                        type(e).__name__, str(e)[:300])
            primary = "vertex" if load_settings().use_vertex else "gemini-api"
            if name == primary and len(available) > 1 and _should_fall_back(e):
                _record_primary_failure(time.monotonic())
            if i + 1 < len(available) and _should_fall_back(e):
                log.warning("retrying on %s", available[i + 1][0])
                continue
            raise


def _available_model_names(client: genai.Client) -> list[str]:
    names = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None)
        if actions is None or "generateContent" in actions:
            names.append(model.name)
    return names


@dataclass(frozen=True)
class ModelChoice:
    reasoning: str
    fast: str
    source: str  # "pinned" or "discovered"


@lru_cache(maxsize=1)
def select_models() -> ModelChoice:
    s: Settings = load_settings()
    if s.model_reasoning and s.model_fast:
        return ModelChoice(s.model_reasoning, s.model_fast, "pinned")
    names: list[str] = []
    available = clients()
    for i, (name, client) in enumerate(available):
        try:
            names = _available_model_names(client)
            break
        except Exception as e:
            if i + 1 < len(available) and _should_fall_back(e):
                log.warning("Model listing on %s failed (%s); using %s", name, getattr(e, "code", e), available[i + 1][0])
                continue
            raise
    newest = rank_models(names, ("pro", "flash"))
    flash = rank_models(names, ("flash",))
    if not newest:
        raise RuntimeError("No Gemini Pro/Flash text models are available to this credential")
    reasoning = s.model_reasoning or newest[0]
    fast = s.model_fast or (flash or newest)[0]
    return ModelChoice(reasoning, fast, "discovered")


# --- Tool-execution smoke (plan story AI-01) --------------------------------

def measure_rectangle(width_mm: int, depth_mm: int) -> dict:
    """Deterministic measurement tool: exact area of a rectangle given in mm."""
    if width_mm <= 0 or depth_mm <= 0:
        raise ValueError("dimensions must be positive")
    return {"area_mm2": width_mm * depth_mm, "area_m2": round(width_mm * depth_mm / 1_000_000, 4)}


_MEASURE_DECL = types.FunctionDeclaration(
    name="measure_rectangle",
    description="Return the exact area of a rectangle whose sides are given in integer millimetres.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "width_mm": {"type": "integer", "description": "Width in millimetres"},
            "depth_mm": {"type": "integer", "description": "Depth in millimetres"},
        },
        "required": ["width_mm", "depth_mm"],
        "additionalProperties": False,
    },
)


def tool_smoke() -> dict:
    """Ask Gemini a question it must answer through the measurement tool.

    Returns the model used, the tool arguments the model chose, the tool's
    measured result and the model's final answer, so the caller can verify the
    answer came from the tool rather than from model arithmetic.
    """
    model = select_models().reasoning
    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[_MEASURE_DECL])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
        ),
    )
    contents: list[types.Content] = [
        types.Content(
            role="user",
            parts=[types.Part(text="A plot is 40 ft by 60 ft. Convert to whole millimetres and use the tool to measure its area.")],
        )
    ]
    first = generate_content(model=model, contents=contents, config=config)
    calls = first.function_calls or []
    if not calls or calls[0].name != "measure_rectangle":
        raise RuntimeError("Model did not call the measurement tool")
    args = dict(calls[0].args or {})
    result = measure_rectangle(int(args["width_mm"]), int(args["depth_mm"]))

    contents.append(first.candidates[0].content)  # keeps thought signatures intact
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_function_response(name="measure_rectangle", response=result)],
        )
    )
    final = generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(tools=config.tools),
    )
    return {"model": model, "tool_args": args, "tool_result": result, "answer": final.text}


# --- Image generation ----------------------------------------------------------------

_IMAGE_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-(flash|pro)-image(-preview)?$")


def rank_image_models(names: list[str]) -> list[str]:
    """Newest Gemini image models first; stable before preview, Flash (fast, low cost) before Pro at a version."""
    found = []
    for name in names:
        m = _IMAGE_RE.match(_short_name(name))
        if m:
            found.append((-float(m.group(1)), m.group(3) is not None, m.group(2) != "flash", _short_name(name)))
    return [n for *_, n in sorted(found)]


@lru_cache(maxsize=1)
def select_image_model() -> str:
    pinned = os.getenv("GEMINI_IMAGE_MODEL", "").strip()
    if pinned:
        return pinned
    for i, (name, client) in enumerate(available := clients()):
        try:
            ranked = rank_image_models(_available_model_names(client))
            if ranked:
                return ranked[0]
            break
        except Exception as e:
            if i + 1 < len(available) and _should_fall_back(e):
                continue
            raise
    raise RuntimeError("No Gemini image model is available to this credential")
