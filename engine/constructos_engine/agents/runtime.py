"""Run Greenprint agent networks in-process with Neuro-SAN's direct session.

The networks live in ./registries (manifest + HOCON); coded tools are importable
modules of this package. Each call is stateless on the server side: the caller
keeps the returned chat_context (to continue a conversation) and the project
store keeps every result, so work can resume from any session.
"""

import os
import threading
import time
from pathlib import Path
from typing import Any

from .. import config  # noqa: F401  (loads the repository .env, e.g. GEMINI_API_KEY)

REGISTRIES = Path(__file__).resolve().parent / "registries"
NETWORKS = ("constructos", "architect", "electrical_engineer", "plumbing_engineer", "cost_estimator", "interior_designer", "sustainability_advisor")

_factory = None
_lock = threading.Lock()


def _get_factory():
    global _factory
    with _lock:
        if _factory is None:
            os.environ["AGENT_MANIFEST_FILE"] = str(REGISTRIES / "manifest.hocon")
            os.environ["AGENT_LLM_INFO_FILE"] = str(REGISTRIES / "llm_info.hocon")
            from neuro_san.client.direct_agent_session_factory import DirectAgentSessionFactory
            _factory = DirectAgentSessionFactory()
        return _factory


_ready = threading.Event()
_warm_seconds: float | None = None


def warm() -> None:
    """Load Neuro-SAN and warm the LLM client (one tiny tool-free turn), so the first real chat is fast."""
    global _warm_seconds
    started = time.monotonic()
    try:
        _get_factory()
        chat(NETWORKS[0], "Reply with the single word: ready. Do not call any tools.", {})
    except Exception:  # noqa: BLE001 - warm-up is best effort; a real request will surface errors
        import logging
        logging.getLogger(__name__).exception("agent warm-up failed")
    finally:
        _warm_seconds = round(time.monotonic() - started, 2)
        _ready.set()


def status() -> dict[str, Any]:
    return {"ready": _ready.is_set(), "warm_seconds": _warm_seconds, "networks": list(NETWORKS)}


def chat(network: str, message: str, sly_data: dict[str, Any], chat_context: dict | None = None) -> dict[str, Any]:
    """One user turn. Returns the answer, the chat_context to continue with, allowed sly_data and a trace."""
    if network not in NETWORKS:
        raise KeyError(network)
    started = time.monotonic()
    session = _get_factory().create_session(network, use_direct=True, metadata={})
    request = {"user_message": {"text": message}, "sly_data": sly_data, "chat_filter": {"chat_filter_type": "MAXIMAL"}}
    if chat_context:
        request["chat_context"] = chat_context
    from neuro_san.internals.messages.chat_message_type import ChatMessageType as T

    def kind_of(value) -> str:  # the direct session reports numeric message types
        try:
            return T(value).name if isinstance(value, int) else str(value)
        except ValueError:
            return str(value)

    answer, context, out_sly, trace, tokens = None, None, {}, [], None
    try:
        for item in session.streaming_chat(request):
            msg = item.get("response") or {}
            kind, origin = kind_of(msg.get("type")), [o.get("tool") for o in msg.get("origin") or []]
            t = round(time.monotonic() - started, 2)
            if kind == "AGENT_TOOL_RESULT" or (kind == "AGENT" and len(origin) > 1):
                trace.append({"t": t, "type": kind, "origin": origin, "text": (msg.get("text") or "")[:300]})
            structure = msg.get("structure") or {}
            if kind == "AGENT" and len(origin) <= 1 and structure.get("total_tokens") is not None:
                tokens = structure  # Neuro-SAN's final token accounting for the whole session
            if kind == "AGENT_FRAMEWORK":
                answer = msg.get("text") or answer
                context = msg.get("chat_context") or context
                out_sly = msg.get("sly_data") or out_sly
    finally:
        session.close()
    tools_called = sorted({o for step in trace for o in step["origin"][1:] if o and o != network})
    from .. import usage
    usage.record(model="gemini (agents)", seconds=time.monotonic() - started, purpose=f"agent:{network}",
                 prompt_tokens=(tokens or {}).get("prompt_tokens", 0), output_tokens=(tokens or {}).get("completion_tokens", 0),
                 requests=int((tokens or {}).get("successful_requests", 1) or 1))
    return {"answer": answer, "chat_context": context, "sly_data": out_sly, "tools_called": tools_called,
            "trace": trace, "tokens": tokens, "seconds": round(time.monotonic() - started, 2)}
