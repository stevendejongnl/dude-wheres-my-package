FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY src/ src/

ENV POLL_INTERVAL_MINUTES=30

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "dwmp.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
