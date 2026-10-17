"""Runtime configuration loaded from the repository-level .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    use_vertex: bool
    gcp_project: str
    gcp_location: str
    model_reasoning: str
    model_fast: str
    port: int
    vertex_fallback: bool = False

    @property
    def ai_configured(self) -> bool:
        return bool(self.gcp_project) if self.use_vertex else bool(self.gemini_api_key)

    @property
    def ai_backend(self) -> str:
        primary = "vertex" if self.use_vertex else "gemini-api"
        if self.use_vertex and self.gemini_api_key:
            return f"{primary} (fallback: gemini-api)"
        if not self.use_vertex and self.gcp_project and self.vertex_fallback:
            return f"{primary} (fallback: vertex)"
        return primary


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        use_vertex=_flag("GOOGLE_GENAI_USE_VERTEXAI"),
        gcp_project=os.getenv("GOOGLE_CLOUD_PROJECT", "").strip(),
        gcp_location=os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip() or "global",
        model_reasoning=os.getenv("GEMINI_MODEL_REASONING", "").strip(),
        model_fast=os.getenv("GEMINI_MODEL_FAST", "").strip(),
        port=int(os.getenv("ENGINE_PORT", "8787")),
        vertex_fallback=_flag("GEMINI_VERTEX_FALLBACK"),
    )
