# Arecibo API Service

Minimal API-first control-plane and ingest service for CEA.

## Location

- Runtime code: `api/src/`
- Tests: `api/tests/`
- Contract source of truth: `openapi.yml`
- JSON schemas used for request/response validation: `schemas/`

## Local configuration

Environment variables:

- `ARECIBO_API_KEYS` (default: `local-dev-key`) comma-separated keys accepted by `X-API-Key`
- `ARECIBO_POLICY_TTL_SEC` (default: `60`) policy response TTL, minimum `5`
- `ARECIBO_POLICY_FILE` optional JSON override file for policies keyed as `<service>:<environment>`
- `ARECIBO_FORCE_GO_DARK` (`true`/`false`) deterministic test mode for all heartbeat/events responses
- `ARECIBO_FORCE_GO_DARK_ON` comma-separated endpoint targets: `heartbeat`, `events`

## Run locally

```bash
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.app:app --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl -s http://localhost:8080/health
```

Authenticated example:

```bash
curl -s \
  -H "X-API-Key: local-dev-key" \
  "http://localhost:8080/policy?serviceName=demo-service&environment=local"
```

## Run tests

```bash
cd api
source .venv/bin/activate
pytest -q
```

## GO_DARK verification mode

GO_DARK is a non-terminating quiet mode: CEA remains alive and stable, continues local behavior, and stops outbound sends.

Use deterministic test mode in API responses:

```bash
ARECIBO_FORCE_GO_DARK=true uvicorn src.app:app --port 8080
```

or endpoint-scoped:

```bash
ARECIBO_FORCE_GO_DARK_ON=heartbeat uvicorn src.app:app --port 8080
```

When enabled, `POST /heartbeat` and/or `POST /events:batch` returns `result.directives` with `GO_DARK` so CEA handling can be validated safely.
