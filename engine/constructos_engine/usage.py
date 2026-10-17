"""Gemini usage log: every model call (direct or through an agent network) with tokens and latency.

Each entry is tagged with the feature (the request path, ids removed) and the project, from a context
variable the API middleware sets. Summaries feed the benchmark report; cost is an estimate at the
stated list prices (override with AI_PRICE_IN_PER_M, AI_PRICE_OUT_PER_M, AI_PRICE_IMAGE).
"""

import contextvars
import logging
import os
import statistics
import uuid
from datetime import datetime, timezone

from . import store

current: contextvars.ContextVar[dict] = contextvars.ContextVar("ai_usage_context", default={})
log = logging.getLogger(__name__)


def price() -> dict:
    return {"in_per_m": float(os.getenv("AI_PRICE_IN_PER_M", "0.30")), "out_per_m": float(os.getenv("AI_PRICE_OUT_PER_M", "2.50")),
            "image": float(os.getenv("AI_PRICE_IMAGE", "0.039"))}


def record(*, model: str, seconds: float, prompt_tokens: int = 0, output_tokens: int = 0, images: int = 0,
           purpose: str | None = None, requests: int = 1) -> None:
    ctx = current.get()
    doc = {"id": str(uuid.uuid4()), "at": datetime.now(timezone.utc).isoformat(), "model": model, "seconds": round(seconds, 2),
           "prompt_tokens": int(prompt_tokens or 0), "output_tokens": int(output_tokens or 0), "images": images, "requests": requests,
           "purpose": purpose or ctx.get("purpose") or "other", "project_id": ctx.get("project_id")}
    try:
        store.backend().put("ai_usage", doc["id"], doc)
    except Exception:  # noqa: BLE001 - usage logging must never break a request
        log.exception("could not record AI usage")


def from_response(response, *, model: str, seconds: float) -> None:
    meta = getattr(response, "usage_metadata", None)
    images = sum(1 for c in (getattr(response, "candidates", None) or []) for p in ((c.content.parts if c.content else None) or [])
                 if getattr(p, "inline_data", None) and (p.inline_data.mime_type or "").startswith("image/"))
    record(model=model, seconds=seconds, prompt_tokens=getattr(meta, "prompt_token_count", 0) or 0,
           output_tokens=(getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0), images=images)


def summary(project_id: str | None = None) -> dict:
    rows = [r for r in store.backend().list("ai_usage") if project_id is None or r.get("project_id") == project_id]
    p = price()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["purpose"], []).append(r)

    def cost(rs):
        text = sum(r["prompt_tokens"] for r in rs) / 1e6 * p["in_per_m"] + sum(r["output_tokens"] for r in rs if not r["images"]) / 1e6 * p["out_per_m"]
        return round(text + sum(r["images"] for r in rs) * p["image"], 4)

    def pct(xs, q):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None

    by = [{"purpose": k, "calls": len(v), "model_requests": sum(r.get("requests", 1) for r in v), "models": sorted({r["model"] for r in v}),
           "prompt_tokens": sum(r["prompt_tokens"] for r in v), "output_tokens": sum(r["output_tokens"] for r in v), "images": sum(r["images"] for r in v),
           "median_s": round(statistics.median([r["seconds"] for r in v]), 1), "p90_s": pct([r["seconds"] for r in v], 0.9), "est_usd": cost(v)}
          for k, v in sorted(groups.items())]
    return {"calls": len(rows), "prompt_tokens": sum(r["prompt_tokens"] for r in rows), "output_tokens": sum(r["output_tokens"] for r in rows),
            "images": sum(r["images"] for r in rows), "est_usd": cost(rows), "prices_usd": p, "by_purpose": by}
