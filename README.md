# arecibo

Reusable **arecibo** runtime for Contrived services.

This repository uses an API-first workflow for Arecibo endpoints: define and agree on
`openapi.yml` before implementing handlers.

This project provides a canonical in-container companion transponder pattern:

- Transponder runs in the background inside the app container.
- App startup is never blocked by transponder startup failures.
- The app remains PID 1 (`exec "$@"` behavior in launcher), referred to as the **primary application process**.
- Integration works across heterogeneous Dockerfiles (no shared base required).

## Time format

- All transponder timestamps use RFC 3339 in UTC with a trailing `Z` (Zulu time).
- Example: `2026-02-25T22:15:30Z`
- Any localization or timezone conversion is handled by upstream systems.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python transponder/transponder.py
```

## API service

The API-first control-plane + ingest service is in `api/`.

- Contract source of truth: `openapi.yml`
- JSON schemas for validation: `schemas/`
- Runtime + tests: `api/src/`, `api/tests/`

Run/test details and GO_DARK verification mode are documented in `api/README.md`.

## Container and deployment artifacts

This repository includes production-facing artifacts used by homelab update automation:

- `Dockerfile` builds the `arecibo-api` container image.
- `docker-compose.yml` defines the `arecibo-api` service for host deployment.
- `.github/workflows/build_and_push.yml` builds and publishes `ghcr.io/contrived-com/arecibo` (`prod` and `latest` on `main`).
- `.github/workflows/build_and_push.yml` also builds `ghcr.io/contrived-com/arecibo-transponder` as a reusable transponder artifact image.
- `.env.example` documents pointer-only runtime env configuration for deploy environments.
- `terraform/vault/` defines app-level secrets and AppRole policy for runtime Vault fetch.

Default host binding for deployment is `127.0.0.1:8032 -> 8080` (nginx proxies `arecibo.contrived.com` to this port).

Production runtime follows the Vault-first pattern:

- App-level secret values (for example API key material) live in Vault, not `.env`.
- `.env` only contains pointers/credentials for Vault access (`VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`, secret path/field selectors).
- Compose joins the external `concordia` network for Vault connectivity.

## Canonical launcher

Use `transponder/entrypoint.sh` as the default service entrypoint wrapper.

It:

1. Starts the transponder in the background.
2. Applies lower scheduling priority to the transponder (`nice`, optional `ionice`).
3. Never blocks app startup.
4. `exec`s your app command so app remains PID 1.

## Transponder artifact and locking

The transponder runtime is packaged in `transponder/` and built as an atomic artifact image (`ghcr.io/contrived-com/arecibo-transponder`):

- `transponder/pyproject.toml` defines the package and CLI entrypoint.
- `Dockerfile.transponder` builds a locked virtualenv under `/opt/transponder/.venv`.
- Services can copy transponder runtime directly:

```dockerfile
FROM ghcr.io/contrived-com/arecibo-transponder:prod AS transponder

COPY --from=transponder /opt/transponder /opt/transponder
COPY --from=transponder /opt/transponder/transponder/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
```

This avoids runtime dependency installation in downstream service images.

For step-by-step integration into other service repos, see:

- `instructions-to-add-transponder.md`

## Transponder environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TRANSPONDER_ENABLED` | `true` | Toggle transponder startup from launcher |
| `TRANSPONDER_NICE_LEVEL` | `10` | CPU niceness for transponder process |
| `TRANSPONDER_IONICE_CLASS` | `3` | I/O class (1=realtime, 2=best-effort, 3=idle) |
| `TRANSPONDER_IONICE_LEVEL` | `7` | I/O level used when class is `2` |
| `TRANSPONDER_BIN` | `/opt/transponder/.venv/bin/transponder` | Transponder executable |
| `TRANSPONDER_ARGS` | `` (empty) | Optional additional args |

## Example integration (service Dockerfile)

```dockerfile
# pull transponder artifacts from dedicated image
FROM ghcr.io/contrived-com/arecibo-transponder:prod AS transponder

FROM python:3.12-slim
WORKDIR /app
COPY . /app

# copy transponder and canonical launcher
COPY --from=transponder /opt/transponder /opt/transponder
COPY --from=transponder /opt/transponder/transponder/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "server.py"]
```

## Escape hatches

Service repos may:

- invoke `python transponder/transponder.py` directly in their own wrapper
- use a custom entrypoint script
- disable transponder at runtime with `TRANSPONDER_ENABLED=false`

The canonical launcher is the default path, not a hard requirement.

## Schema conventions

- Schemas live under `schemas/`.
- Each schema uses JSON Schema draft 2020-12:
  - `"$schema": "https://json-schema.org/draft/2020-12/schema"`
- Each schema uses a semver-style `$id`, for example:
  - `"$id": "arecibo/schemas/ingest/events-batch/1.0.0"`
- All timestamps are RFC 3339 in UTC (`Z` suffix).
