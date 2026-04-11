# Dude, Where's My Package?

Package tracking service for Dutch carriers. Runs as a container with a REST API and background poller.

## Supported Carriers

- PostNL
- DHL
- DPD

## Quick Start

```bash
uv sync --all-extras
uv run pytest
uv run uvicorn dwmp.api.app:app --reload
```

## API

```
GET    /health                        # Health check
GET    /api/v1/carriers               # List supported carriers
GET    /api/v1/packages               # List tracked packages
POST   /api/v1/packages               # Add package
GET    /api/v1/packages/{id}          # Package details + events
DELETE /api/v1/packages/{id}          # Stop tracking
POST   /api/v1/packages/{id}/refresh  # Force refresh
```

## Docker

```bash
docker build -t dwmp .
docker run -p 8000:8000 -v dwmp-data:/app/data dwmp
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `dwmp.db` | SQLite database file path |
| `POLL_INTERVAL_MINUTES` | `30` | Background polling interval |

## Kubernetes

```bash
kubectl apply -f kubernetes/
```

## License

MIT
