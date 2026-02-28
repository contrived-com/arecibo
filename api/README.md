# Arecibo API Service

Minimal API-first control-plane and ingest service for the transponder runtime.

## Location

- Runtime code: `api/src/`
- Tests: `api/tests/`
- Contract source of truth: `openapi.yml`
- JSON schemas used for request/response validation: `schemas/`

## Local configuration

Environment variables:

- `VAULT_ADDR` Vault base URL (for example `http://concordia-vault:8200` in compose network)
- `VAULT_ROLE_ID` AppRole role id for runtime auth
- `VAULT_SECRET_ID` AppRole secret id for runtime auth
- `ARECIBO_VAULT_PATH` (default: `arecibo/config`) KV v2 path under mount `secret`
- `ARECIBO_API_KEYS_FIELD` (default: `arecibo_api_keys`) field storing comma-separated keys accepted by `X-API-Key`
- `ARECIBO_POLICY_TTL_SEC` (default: `60`) policy response TTL, minimum `5`
- `ARECIBO_POLICY_FILE` optional JSON override file for policies keyed as `<service>:<environment>`
- `ARECIBO_FORCE_GO_DARK` (`true`/`false`) deterministic test mode for all heartbeat/events responses
- `ARECIBO_FORCE_GO_DARK_ON` comma-separated endpoint targets: `heartbeat`, `events`

Local-only fallback (when Vault is not configured):

- `ARECIBO_API_KEYS` comma-separated keys for development/testing

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

GO_DARK is a non-terminating quiet mode: the transponder remains alive and stable, continues local behavior, and stops outbound sends.

Use deterministic test mode in API responses:

```bash
ARECIBO_FORCE_GO_DARK=true uvicorn src.app:app --port 8080
```

or endpoint-scoped:

```bash
ARECIBO_FORCE_GO_DARK_ON=heartbeat uvicorn src.app:app --port 8080
```

When enabled, `POST /heartbeat` and/or `POST /events:batch` returns `result.directives` with `GO_DARK` so transponder handling can be validated safely.
