FROM node:22-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src/dwmp/static/ts/ src/dwmp/static/ts/
RUN npm run build

FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/stevendejongnl/dude-wheres-my-package"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# Install Chromium for Playwright (Amazon browser automation)
RUN uv run playwright install --with-deps chromium

# Copy source and install the project itself (records version metadata)
COPY src/ src/
COPY --from=frontend /build/src/dwmp/static/js/ src/dwmp/static/js/
RUN uv sync --no-dev --frozen

ENV PYTHONIOENCODING=utf-8
ENV LANG=C.UTF-8
ENV POLL_INTERVAL_MINUTES=30

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "dwmp.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
