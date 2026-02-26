# arecibo

Reusable **arecibo** runtime for Contrived services.

This repository uses an API-first workflow for Arecibo endpoints: define and agree on
`openapi.yml` before implementing handlers.

This project provides a canonical in-container companion agent pattern:

- CEA runs in the background inside the app container.
- App startup is never blocked by agent startup failures.
- The app remains PID 1 (`exec "$@"` behavior in launcher), referred to as the **primary application process**.
- Integration works across heterogeneous Dockerfiles (no shared base required).

## Time format

- All CEA timestamps use RFC 3339 in UTC with a trailing `Z` (Zulu time).
- Example: `2026-02-25T22:15:30Z`
- Any localization or timezone conversion is handled by upstream systems.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python agent/cea_agent.py
```

## Canonical launcher

Use `agent/entrypoint.sh` as the default service entrypoint wrapper.

It:

1. Starts CEA in the background.
2. Applies lower scheduling priority to CEA (`nice`, optional `ionice`).
3. Never blocks app startup.
4. `exec`s your app command so app remains PID 1.

## CEA environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CEA_ENABLED` | `true` | Toggle CEA startup from launcher |
| `CEA_NICE_LEVEL` | `10` | CPU niceness for CEA process |
| `CEA_IONICE_CLASS` | `3` | I/O class (1=realtime, 2=best-effort, 3=idle) |
| `CEA_IONICE_LEVEL` | `7` | I/O level used when class is `2` |
| `CEA_AGENT_BIN` | `python` | Agent executable |
| `CEA_AGENT_ARGS` | `/opt/cea/agent/cea_agent.py` | Agent arguments/path |

## Example integration (service Dockerfile)

```dockerfile
# pull CEA artifacts from dedicated image
FROM ghcr.io/contrived/arecibo:0.1.0 AS cea

FROM python:3.12-slim
WORKDIR /app
COPY . /app

# copy agent and canonical launcher
COPY --from=cea /opt/cea /opt/cea
COPY --from=cea /opt/cea/agent/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "server.py"]
```

## Escape hatches

Service repos may:

- invoke `python agent/cea_agent.py` directly in their own wrapper
- use a custom entrypoint script
- disable CEA at runtime with `CEA_ENABLED=false`

The canonical launcher is the default path, not a hard requirement.

## Schema conventions

- Schemas live under `schemas/`.
- Each schema uses JSON Schema draft 2020-12:
  - `"$schema": "https://json-schema.org/draft/2020-12/schema"`
- Each schema uses a semver-style `$id`, for example:
  - `"$id": "arecibo/schemas/ingest/events-batch/1.0.0"`
- All timestamps are RFC 3339 in UTC (`Z` suffix).
