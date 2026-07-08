FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.5.7 /uv /uvx /usr/local/bin/

WORKDIR /app

# Venv lives outside /app since docker-compose bind-mounts the repo root over
# /app for local dev hot-reload — a venv under /app would get shadowed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app \
 && chown -R app:app /app /opt/venv
USER app

EXPOSE 8000

CMD ["uvicorn", "dsxlineage.main:app", "--host", "0.0.0.0", "--port", "8000"]
