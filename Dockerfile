# syntax=docker/dockerfile:1
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependency layer (cached until pyproject changes)
COPY pyproject.toml ./
RUN uv sync --no-dev

# App code
COPY bridge ./bridge

RUN useradd -m app && chown -R app /app
USER app

EXPOSE 15000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:15000/healthz').status==200 else 1)"

ENTRYPOINT ["python", "-m", "bridge.app"]
