# Policy Blob Format

Policy files are stored on disk at:

- `./data/policies/<service-name>/<container-name>.json`

At runtime in the container this maps to:

- `/data/policies/<service-name>/<container-name>.json`

Lookup mapping:

- `serviceName` query param -> `<service-name>`
- `environment` query param -> `<container-name>`

## Field Notes

- `defaultSampleRate`
  - Ratio from `0.0` to `1.0`
  - Not a duration, not a time interval
  - `1.0` means keep all matching events, `0.5` means sample ~50%
- `eventOverrides.<eventType>.sampleRate`
  - Same ratio semantics as `defaultSampleRate`
- `heartbeatIntervalSec`
  - Seconds between transponder heartbeat requests
- `maxBatchSize`
  - Max number of events per outbound batch
- `maxTransponderSilenceSec`
  - Seconds before forcing a partial batch flush when queue is not full
  - Use `300` for five minutes
  - Use `0` to disable silence-based forcing

## Earshot Baseline

Current earshot policy blobs:

- `data/policies/earshot/earshot-api.json`
- `data/policies/earshot/earshot-worker.json`
- `data/policies/earshot/earshot-web.json`

All three currently set `maxTransponderSilenceSec` to `300` (5 minutes).
