# ConstructOS engine: FastAPI + OR-Tools + Gemini + Neuro-SAN agents (+ Node for the interior solver).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /bin/uv
# Node runs the editor's interior design solver (constructos_engine/interior/solver.mjs) for the interior designer agent.
COPY --from=node:22.20.0-bookworm-slim /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/* && node --version
WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY constructos_engine ./constructos_engine
RUN uv sync --frozen --no-dev && useradd --uid 10001 --no-create-home engine
USER engine
ENV PATH=/srv/.venv/bin:$PATH PORT=8080
CMD ["sh", "-c", "exec uvicorn constructos_engine.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-graceful-shutdown 20"]
