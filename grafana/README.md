# Arecibo Grafana

Provisioned Grafana instance for fleet observability dashboards.

## Quick Start

```bash
docker compose up -d arecibo-grafana
```

Grafana will be available at `http://localhost:3033` (bound to 127.0.0.1 only).

Default credentials: `admin` / value of `GRAFANA_ADMIN_PASSWORD` env var
(defaults to `arecibo-admin` for local development).

## Architecture

```
contrived-site ──proxy──> arecibo-grafana:3000
                              │
                              ▼ (Infinity datasource)
                          arecibo-api:8080
                              │
                              ▼
                          data/telemetry/
```

Grafana uses the [Infinity datasource](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/)
plugin to query the Arecibo API REST endpoints directly.

## Provisioning

### Datasources

`grafana/provisioning/datasources/arecibo.yml` configures the Infinity
datasource pointing to `http://arecibo-api:8080` on the internal Docker network.

### Dashboards

`grafana/provisioning/dashboards/dashboards.yml` configures the file-based
dashboard provider reading from `/var/lib/grafana/dashboards` (mounted from
`grafana/dashboards/`).

Starter dashboards:
- **Fleet Overview** (`fleet-overview.json`): Fleet health table, heartbeat
  freshness, GO_DARK status
- **Event Activity** (`event-activity.json`): Event throughput time series,
  recent events table

## Updating Dashboards

1. Edit the dashboard in the Grafana UI
2. Save it (admin access required)
3. Export the dashboard JSON: Dashboard Settings > JSON Model > Copy
4. Save to `grafana/dashboards/<name>.json`
5. Commit to the repo

On next `docker compose up`, Grafana will pick up the updated JSON.

## Upgrading Grafana

Change the image tag in `docker-compose.yml`:

```yaml
arecibo-grafana:
  image: grafana/grafana:11.5.2  # ← change this version
```

Then restart:
```bash
docker compose up -d arecibo-grafana
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `GRAFANA_ADMIN_PASSWORD` | `arecibo-admin` | Grafana admin password |
| `GRAFANA_ROOT_URL` | `http://localhost:3033` | External URL for Grafana |

## Troubleshooting

### Datasource connection errors

Verify the API is healthy and reachable from the Grafana container:
```bash
docker compose exec arecibo-grafana wget -qO- http://arecibo-api:8080/health
```

### Dashboards not loading

Check that the provisioning volume is mounted correctly:
```bash
docker compose exec arecibo-grafana ls /etc/grafana/provisioning/dashboards/
docker compose exec arecibo-grafana ls /var/lib/grafana/dashboards/
```

### Plugin not installed

The Infinity datasource plugin is installed via `GF_INSTALL_PLUGINS` on first boot.
If it fails, check container logs:
```bash
docker compose logs arecibo-grafana | grep -i plugin
```
