# Build frontend assets once; Django/WhiteNoise serves them in the runtime image.
FROM node:22.22.2-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/scripts/ ./scripts/
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Web + worker image. GDAL/GEOS for GeoDjango, Node + mystmd for rendering.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" MYST_COMMAND=myst

RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin libgdal-dev libgeos-dev libproj-dev binutils git curl ca-certificates gnupg procps \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g mystmd@1.12.0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the code already owned by the runtime user; a `chown -R` afterwards would duplicate the layer.
RUN useradd -m app && mkdir -p /app/data /app/staticfiles && chown app:app /app/data /app/staticfiles
COPY --chown=app:app . .
COPY --from=frontend --chown=app:app /factory/web/static/frontend ./factory/web/static/frontend
RUN uv sync --frozen --no-dev
USER app

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
