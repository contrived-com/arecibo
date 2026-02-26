# AGENTS.md

## Purpose

This repository defines the reusable **embedded-agent** pattern for Contrived services.
The agent runs **inside** each app container as a background companion process (not a sidecar).

Primary objective: make fleet observability low-friction and low-maintenance so adoption is "it just works".

## API-First Requirement

- This project follows API-first development for Arecibo APIs.
- Define and review `openapi.yml` before implementing API handlers.
- Keep OpenAPI and JSON schemas aligned; implementation follows the spec, not the reverse.

## Canonical Runtime Contract

- Canonical launcher is `agent/entrypoint.sh`.
- Services may copy this script and use it as `ENTRYPOINT`.
- Script starts CEA in the background with lower scheduling priority, then `exec "$@"`.
- The app command remains PID 1 and is called the **primary application process**.
- Agent startup failure must never block app startup.
- Agent package is defined in `agent/pyproject.toml` and built as a locked artifact image (`ghcr.io/contrived-com/arecibo-agent`).
- Downstream services should consume `/opt/cea` via `COPY --from=...` instead of installing agent dependencies at runtime.

## Time Standard

- All timestamps sent or received by CEA must use RFC 3339 date-time format in UTC.
- UTC is represented with a trailing `Z` (Zulu time), for example `2026-02-25T22:15:30Z`.
- Localization/timezone presentation happens upstream in Arecibo web or other consumers.

## Naming

- Use `CEA_*` env vars (not `EA_*`).
- Pattern name: `embedded-agent`.
- Do not call this sidecar or Docker-in-Docker.

## Integration Philosophy

- Keep per-service integration minimal:
  - copy CEA artifacts into image
  - use canonical `entrypoint.sh`
  - set a small set of `CEA_*` env vars
- No shared app base image requirement.
- Heterogeneous Dockerfiles are expected and supported.

## Telemetry Architecture Direction

- App emits telemetry locally to CEA using a best-effort interface.
- Preferred local ingest is Unix socket `SOCK_DGRAM` (no network dependency).
- If CEA is absent/unavailable, app should continue without blocking.
- CEA owns sampling/filtering/redaction and uplink behavior based on homebase policy.
- CEA can run independently of app telemetry and still provide announce/heartbeat.
- In `GO_DARK`, CEA stays alive and keeps local ingest behavior stable while dropping outbound sends.

## Reliability and Safety

- Delivery is at-least-once; duplicates are expected and tolerated.
- CEA queues must be bounded; never risk unbounded disk growth in default mode.
- Resource usage should be observable (memory/process stats in heartbeats).
- CEA should run lower priority than app (`nice` and optional `ionice`).

## Security

- TLS for collector endpoints.
- Auth via scoped token (or signed payload in future evolution).
- Never emit secrets in payloads.
- Collect only required identity + operational metadata.
- Production secret pattern follows Concordia Vault:
  - app-level secrets are defined in `terraform/vault/` and applied to Vault
  - runtime containers fetch app secrets from Vault via AppRole
  - `.env` is pointer-only (`VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`, secret path/field selectors)
  - do not place app secret values in `.env` for production

## Deployment Network Pattern

- Services that need Vault access should join the external Docker network `concordia`.
- Keep host port binding on `127.0.0.1` and terminate external traffic at nginx.

## Repo Boundaries

- This repo owns:
  - embedded agent code
  - canonical launcher script
  - shared schemas under `schemas/`
  - shared env var/config contract
  - integration examples
- Service repos own:
  - app logic and app entry command
  - service-specific rollout timing
  - optional custom wrapper when needed
