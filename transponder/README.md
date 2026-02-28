# Arecibo Transponder Package

This directory defines a reproducible Python package for the CEA runtime transponder.

## Goals

- Lock dependencies with `uv.lock`.
- Build/install as a wheel.
- Ship as a stable artifact under `/opt/cea` for downstream image `COPY --from`.
- Implement canonical lifecycle:
  - `POST /announce`
  - `GET /policy`
  - periodic `POST /heartbeat`
  - policy-aware `POST /events:batch`
  - directive handling including `GO_DARK` / `RESUME`

## Local workflow

```bash
cd transponder
uv lock
uv sync --locked
uv run cea-transponder
```

## Runtime behavior

- Collector discovery order:
  1. `CEA_COLLECTOR_URL` (explicit override)
  2. `CEA_COLLECTOR_CANDIDATES` (default internal first, then external)
- If `GO_DARK` is active, outbound sends stop while local ingest remains available.
- Local ingest socket defaults to `unixgram` at `/tmp/cea-ingest.sock`.
