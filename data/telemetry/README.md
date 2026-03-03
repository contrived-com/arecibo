# Telemetry Storage

Append-only JSONL storage for transponder telemetry data.

## Directory Layout

```
data/telemetry/
├── YYYY-MM-DD/                    # Date partition (UTC)
│   ├── {serviceName}/             # Sanitized service name
│   │   └── {environment}/         # Sanitized environment
│   │       ├── announce.jsonl     # Transponder startup announcements
│   │       ├── heartbeat.jsonl    # Periodic heartbeats
│   │       └── events.jsonl       # Event batches
```

Partitioning by date/service/environment enables efficient range queries
without full-scan reads.

## File Format

Each file uses **JSONL** (one JSON object per line). Every record has:

```json
{"receivedAt":"2026-03-03T12:00:00Z","payload":{...}}
```

- `receivedAt`: UTC timestamp (RFC 3339, trailing Z) when the API received the payload
- `payload`: The original ingest payload as submitted by the transponder

## Timestamp Format

All timestamps use **RFC 3339 UTC with trailing Z**, e.g. `2026-03-03T12:00:00Z`.
No timezone offsets are stored.

## Name Sanitization

Service names and environments are sanitized for safe filesystem use:
- Characters outside `[a-zA-Z0-9._-]` are replaced with `_`
- Names are truncated to 128 characters
- Empty names become `_`

## Data Flow

1. Transponder sends announce/heartbeat/events to API ingest endpoints
2. API validates the payload against JSON schemas
3. `TelemetryStore` appends the record to the appropriate JSONL file
4. Writes are fire-and-forget: failures are logged but don't block the API response
5. Query endpoints in `TelemetryReader` scan partitions to serve Grafana dashboards

## Retention

Stale date partitions are pruned automatically.

- **Default retention**: 180 days
- **Configuration**: `ARECIBO_RETENTION_DAYS` environment variable (minimum: 1)
- **Execution**: Runs on API startup as a non-blocking background task
- **Behavior**: Walks top-level date directories, removes any older than the cutoff
- **Dry-run**: Set in code for operational safety testing (see `telemetry_retention.py`)

### Lifecycle Logs

Retention emits structured JSON logs:

| Event | Description |
|-------|-------------|
| `retention_skip_no_dir` | Base telemetry directory doesn't exist |
| `retention_would_prune` | Dry-run: partition would be pruned |
| `retention_pruned` | Partition was successfully removed |
| `retention_prune_error` | Error removing a partition (logged, not fatal) |
| `retention_complete` | Summary with scanned/pruned/skipped/error counts |

## Inspecting Data

Browse partitions:
```bash
ls data/telemetry/
ls data/telemetry/2026-03-03/
```

Read recent heartbeats for a service:
```bash
tail -5 data/telemetry/2026-03-03/web-app/production/heartbeat.jsonl | jq .
```

Count events for a date:
```bash
wc -l data/telemetry/2026-03-03/*/production/events.jsonl
```

## Query Endpoints

The telemetry reader serves five query endpoints for Grafana consumption:

| Endpoint | Description |
|----------|-------------|
| `GET /query/fleet-health` | Aggregated service health with instance counts |
| `GET /query/heartbeat-freshness` | Per-instance heartbeat recency and staleness |
| `GET /query/event-throughput` | Time-bucketed event counts for time series |
| `GET /query/go-dark-status` | GO_DARK directive state per instance |
| `GET /query/recent-events` | Paginated recent events (payload redacted) |

All query endpoints require `X-API-Key` authentication and accept optional
`start`/`end` time range parameters (defaults to last 1 hour).
