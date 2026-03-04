"""Telemetry reader module.

Partition-aware reads from the telemetry file storage for query endpoints.
Leverages the date/service/environment partition structure to skip irrelevant
directories and avoid full-scan reads.

Layout (matches telemetry_store.py):
  data/telemetry/{YYYY-MM-DD}/{serviceName}/{environment}/{type}.jsonl
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .telemetry_store import _safe_name

logger = logging.getLogger("arecibo.telemetry_reader")


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse an RFC 3339 UTC timestamp (trailing Z)."""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _format_ts(dt: datetime) -> str:
    """Format a datetime as RFC 3339 UTC with trailing Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor))
        return max(0, int(data.get("o", 0)))
    except Exception:
        return 0


class TelemetryReader:
    """Partition-aware reader for telemetry JSONL files."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    def _date_dirs_in_range(
        self, start: datetime, end: datetime
    ) -> list[tuple[str, Path]]:
        """Return sorted (date_str, path) tuples for date dirs within [start, end]."""
        if not self._base.is_dir():
            return []
        start_date = start.date()
        end_date = end.date()
        results = []
        for entry in sorted(self._base.iterdir()):
            if not entry.is_dir():
                continue
            try:
                d = datetime.strptime(entry.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if start_date <= d <= end_date:
                results.append((entry.name, entry))
        return results

    def _all_date_dirs(self) -> list[tuple[str, Path]]:
        """Return all date directories, sorted."""
        if not self._base.is_dir():
            return []
        results = []
        for entry in sorted(self._base.iterdir()):
            if not entry.is_dir():
                continue
            try:
                datetime.strptime(entry.name, "%Y-%m-%d")
            except ValueError:
                continue
            results.append((entry.name, entry))
        return results

    def _service_env_dirs(
        self,
        date_dir: Path,
        service_filter: str | None,
        env_filter: str | None,
    ) -> list[tuple[str, str, Path]]:
        """Return (serviceName, environment, path) for matching partitions."""
        results = []
        if not date_dir.is_dir():
            return results
        service_dirs = sorted(date_dir.iterdir())
        for svc_dir in service_dirs:
            if not svc_dir.is_dir():
                continue
            if service_filter and svc_dir.name != _safe_name(service_filter):
                continue
            for env_dir in sorted(svc_dir.iterdir()):
                if not env_dir.is_dir():
                    continue
                if env_filter and env_dir.name != _safe_name(env_filter):
                    continue
                results.append((svc_dir.name, env_dir.name, env_dir))
        return results

    def _read_jsonl(self, filepath: Path) -> list[dict]:
        """Read all records from a JSONL file."""
        records = []
        if not filepath.is_file():
            return records
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            logger.exception("read_jsonl_error", extra={"fields": {"path": str(filepath)}})
        return records

    def query_fleet_health(
        self,
        start: datetime,
        end: datetime,
        service_name: str | None = None,
        environment: str | None = None,
        max_rows: int = 1000,
    ) -> dict:
        """Aggregate fleet health from announce and heartbeat data."""
        # Key: (serviceName, environment)
        aggregates: dict[tuple[str, str], dict] = {}

        date_dirs = self._date_dirs_in_range(start, end)
        for _date_str, date_dir in date_dirs:
            for svc_name, env_name, partition_dir in self._service_env_dirs(
                date_dir, service_name, environment
            ):
                key = (svc_name, env_name)
                if key not in aggregates:
                    aggregates[key] = {
                        "instances": set(),
                        "lastAnnouncedAt": None,
                        "lastHeartbeatAt": None,
                    }
                agg = aggregates[key]

                # Scan announce records
                for rec in self._read_jsonl(partition_dir / "announce.jsonl"):
                    payload = rec.get("payload", {})
                    identity = payload.get("identity", {})
                    inst_id = identity.get("instanceId")
                    if inst_id:
                        agg["instances"].add(inst_id)
                    sent_at = payload.get("sentAt") or rec.get("receivedAt")
                    if sent_at:
                        ts = _parse_ts(sent_at)
                        if ts and start <= ts <= end:
                            if agg["lastAnnouncedAt"] is None or ts > agg["lastAnnouncedAt"]:
                                agg["lastAnnouncedAt"] = ts

                # Scan heartbeat records
                for rec in self._read_jsonl(partition_dir / "heartbeat.jsonl"):
                    payload = rec.get("payload", {})
                    identity = payload.get("identity", {})
                    inst_id = identity.get("instanceId")
                    if inst_id:
                        agg["instances"].add(inst_id)
                    sent_at = payload.get("sentAt") or rec.get("receivedAt")
                    if sent_at:
                        ts = _parse_ts(sent_at)
                        if ts and start <= ts <= end:
                            if agg["lastHeartbeatAt"] is None or ts > agg["lastHeartbeatAt"]:
                                agg["lastHeartbeatAt"] = ts

        # Build response
        now = datetime.now(timezone.utc)
        data = []
        for (svc, env), agg in sorted(aggregates.items()):
            last_hb = agg["lastHeartbeatAt"]
            last_announced = agg["lastAnnouncedAt"] or last_hb
            if last_hb is None:
                status = "offline"
            elif (now - last_hb).total_seconds() > 900:  # 15 min
                status = "offline"
            elif (now - last_hb).total_seconds() > 300:  # 5 min
                status = "stale"
            else:
                status = "healthy"

            data.append({
                "serviceName": svc,
                "environment": env,
                "instanceCount": len(agg["instances"]),
                "lastAnnouncedAt": _format_ts(last_announced) if last_announced else None,
                "lastHeartbeatAt": _format_ts(last_hb) if last_hb else None,
                "status": status,
            })

        data = data[:max_rows]
        return {
            "data": data,
            "meta": {
                "totalRows": len(data),
                "start": _format_ts(start),
                "end": _format_ts(end),
            },
        }

    def query_heartbeat_freshness(
        self,
        start: datetime,
        end: datetime,
        staleness_threshold_sec: int = 300,
        service_name: str | None = None,
        environment: str | None = None,
        max_rows: int = 1000,
        cursor: str | None = None,
    ) -> dict:
        """Per-instance heartbeat freshness with staleness calculation."""
        offset = _decode_cursor(cursor)
        # Key: (serviceName, environment, instanceId) -> latest heartbeat info
        instances: dict[tuple[str, str, str], dict] = {}

        date_dirs = self._date_dirs_in_range(start, end)
        for _date_str, date_dir in date_dirs:
            for svc_name, env_name, partition_dir in self._service_env_dirs(
                date_dir, service_name, environment
            ):
                for rec in self._read_jsonl(partition_dir / "heartbeat.jsonl"):
                    payload = rec.get("payload", {})
                    identity = payload.get("identity", {})
                    svc = identity.get("serviceName", svc_name)
                    env = identity.get("environment", env_name)
                    inst_id = identity.get("instanceId", "")
                    if not inst_id:
                        continue
                    sent_at = payload.get("sentAt") or rec.get("receivedAt")
                    ts = _parse_ts(sent_at) if sent_at else None
                    if not ts or ts < start or ts > end:
                        continue

                    go_dark = payload.get("status", {}).get("goDark")
                    key = (_safe_name(svc), _safe_name(env), inst_id)
                    existing = instances.get(key)
                    if existing is None or ts > existing["ts"]:
                        instances[key] = {"ts": ts, "goDark": go_dark}

        now = datetime.now(timezone.utc)
        all_rows = []
        for (svc, env, inst_id), info in sorted(instances.items()):
            stale_sec = (now - info["ts"]).total_seconds()
            stale_sec = max(0.0, stale_sec)
            if stale_sec <= staleness_threshold_sec:
                status = "fresh"
            elif stale_sec <= staleness_threshold_sec * 3:
                status = "stale"
            else:
                status = "offline"

            row: dict = {
                "serviceName": svc,
                "environment": env,
                "instanceId": inst_id,
                "lastHeartbeatAt": _format_ts(info["ts"]),
                "staleSec": round(stale_sec, 1),
                "status": status,
            }
            if info["goDark"] is not None:
                row["goDark"] = info["goDark"]
            all_rows.append(row)

        total = len(all_rows)
        page = all_rows[offset : offset + max_rows]
        next_offset = offset + max_rows
        next_cursor = _encode_cursor(next_offset) if next_offset < total else None

        return {
            "data": page,
            "meta": {
                "totalRows": total,
                "start": _format_ts(start),
                "end": _format_ts(end),
                "cursor": next_cursor,
            },
        }

    def query_event_throughput(
        self,
        start: datetime,
        end: datetime,
        bucket_width_sec: int = 60,
        service_name: str | None = None,
        environment: str | None = None,
        max_rows: int = 1000,
    ) -> dict:
        """Time-bucketed event counts."""
        bucket_width = timedelta(seconds=bucket_width_sec)
        # Collect all event timestamps
        event_times: list[datetime] = []

        date_dirs = self._date_dirs_in_range(start, end)
        for _date_str, date_dir in date_dirs:
            for svc_name, env_name, partition_dir in self._service_env_dirs(
                date_dir, service_name, environment
            ):
                for rec in self._read_jsonl(partition_dir / "events.jsonl"):
                    payload = rec.get("payload", {})
                    events = payload.get("events", [])
                    for event in events:
                        ts_str = event.get("ts")
                        ts = _parse_ts(ts_str) if ts_str else None
                        if ts and start <= ts <= end:
                            event_times.append(ts)

        # Build buckets
        buckets: dict[datetime, int] = {}
        for ts in event_times:
            # Floor to bucket boundary
            seconds_since_start = (ts - start).total_seconds()
            bucket_index = int(seconds_since_start // bucket_width_sec)
            bucket_start = start + timedelta(seconds=bucket_index * bucket_width_sec)
            buckets[bucket_start] = buckets.get(bucket_start, 0) + 1

        # Generate all buckets in range (including empty ones)
        all_buckets = []
        current = start
        while current < end:
            count = buckets.get(current, 0)
            all_buckets.append({
                "bucket": _format_ts(current),
                "count": count,
            })
            current += bucket_width

        all_buckets = all_buckets[:max_rows]
        return {
            "data": all_buckets,
            "meta": {
                "totalRows": len(all_buckets),
                "bucketWidthSec": bucket_width_sec,
                "start": _format_ts(start),
                "end": _format_ts(end),
            },
        }

    def query_container_metrics(
        self,
        start: datetime,
        end: datetime,
        bucket_width_sec: int = 30,
        service_name: str | None = None,
        environment: str | None = None,
        instance_id: str | None = None,
        rollup: str = "container",
        max_rows: int = 10000,
    ) -> dict:
        """Bucketed heartbeat resource/network metrics for containers or services."""

        def _to_int(value: object) -> int | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            return None

        def _to_float(value: object) -> float | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value)
            return None

        # container key -> points ordered later by timestamp
        container_points: dict[tuple[str, str, str], list[dict]] = {}
        for _date_str, date_dir in self._date_dirs_in_range(start, end):
            for svc_name, env_name, partition_dir in self._service_env_dirs(
                date_dir, service_name, environment
            ):
                for rec in self._read_jsonl(partition_dir / "heartbeat.jsonl"):
                    payload = rec.get("payload", {})
                    identity = payload.get("identity", {})
                    svc = _safe_name(identity.get("serviceName", svc_name))
                    env = _safe_name(identity.get("environment", env_name))
                    inst_id = str(identity.get("instanceId", "")).strip()
                    if not inst_id:
                        continue
                    if instance_id and inst_id != instance_id:
                        continue

                    sent_at = payload.get("sentAt") or rec.get("receivedAt")
                    ts = _parse_ts(sent_at) if sent_at else None
                    if not ts or ts < start or ts > end:
                        continue

                    status = payload.get("status", {})
                    container_points.setdefault((svc, env, inst_id), []).append({
                        "ts": ts,
                        "rx": max(0, _to_int(status.get("containerRxBytesSinceLastHeartbeat")) or 0),
                        "tx": max(0, _to_int(status.get("containerTxBytesSinceLastHeartbeat")) or 0),
                        "containerMemoryCurrentBytes": _to_int(status.get("containerMemoryCurrentBytes")),
                        "containerMemoryMaxBytes": _to_int(status.get("containerMemoryMaxBytes")),
                        "transponderRssBytes": _to_int(status.get("transponderRssBytes")),
                        "primaryAppRssBytes": _to_int(status.get("primaryAppRssBytes")),
                        "transponderCpuUserSec": _to_float(status.get("transponderCpuUserSec")),
                        "transponderCpuSystemSec": _to_float(status.get("transponderCpuSystemSec")),
                    })

        # aggregate by bucket and grouping key (container or service+environment)
        aggregates: dict[tuple, dict] = {}
        for (svc, env, inst), points in container_points.items():
            previous: dict | None = None
            for point in sorted(points, key=lambda p: p["ts"]):
                seconds_since_start = (point["ts"] - start).total_seconds()
                bucket_index = int(seconds_since_start // bucket_width_sec)
                bucket_start = start + timedelta(seconds=bucket_index * bucket_width_sec)

                group_key = (svc, env, bucket_start, inst)
                if rollup == "service":
                    group_key = (svc, env, bucket_start)

                agg = aggregates.setdefault(group_key, {
                    "serviceName": svc,
                    "environment": env,
                    "bucket": bucket_start,
                    "instanceId": inst if rollup == "container" else None,
                    "containerCount": 0,
                    "_containerSet": set(),
                    "networkRxBytes": 0,
                    "networkTxBytes": 0,
                    "containerMemoryCurrentBytes": 0,
                    "containerMemoryMaxBytes": 0,
                    "transponderRssBytes": 0,
                    "primaryAppRssBytes": 0,
                    "cpuPct": 0.0,
                    "_cpuSamples": 0,
                    "_memorySamples": 0,
                    "_maxSamples": 0,
                    "_rssSamples": 0,
                    "_appRssSamples": 0,
                })

                # Network in heartbeat is already deltas for "since last heartbeat".
                agg["networkRxBytes"] += point["rx"]
                agg["networkTxBytes"] += point["tx"]

                if point["containerMemoryCurrentBytes"] is not None:
                    agg["containerMemoryCurrentBytes"] += point["containerMemoryCurrentBytes"]
                    agg["_memorySamples"] += 1
                if point["containerMemoryMaxBytes"] is not None:
                    agg["containerMemoryMaxBytes"] += point["containerMemoryMaxBytes"]
                    agg["_maxSamples"] += 1
                if point["transponderRssBytes"] is not None:
                    agg["transponderRssBytes"] += point["transponderRssBytes"]
                    agg["_rssSamples"] += 1
                if point["primaryAppRssBytes"] is not None:
                    agg["primaryAppRssBytes"] += point["primaryAppRssBytes"]
                    agg["_appRssSamples"] += 1

                if previous is not None:
                    dt_sec = max(1e-6, (point["ts"] - previous["ts"]).total_seconds())
                    user_now = point["transponderCpuUserSec"]
                    user_prev = previous["transponderCpuUserSec"]
                    sys_now = point["transponderCpuSystemSec"]
                    sys_prev = previous["transponderCpuSystemSec"]
                    if (
                        user_now is not None
                        and user_prev is not None
                        and sys_now is not None
                        and sys_prev is not None
                    ):
                        cpu_delta = (user_now - user_prev) + (sys_now - sys_prev)
                        if cpu_delta >= 0:
                            agg["cpuPct"] += (cpu_delta / dt_sec) * 100.0
                            agg["_cpuSamples"] += 1

                if rollup == "service":
                    agg["_containerSet"].add(inst)
                previous = point

        rows = []
        for agg in sorted(aggregates.values(), key=lambda r: (r["bucket"], r["serviceName"], r["environment"], r.get("instanceId") or "")):
            row = {
                "bucket": _format_ts(agg["bucket"]),
                "serviceName": agg["serviceName"],
                "environment": agg["environment"],
                "networkRxBytes": agg["networkRxBytes"],
                "networkTxBytes": agg["networkTxBytes"],
                "containerMemoryCurrentBytes": (
                    agg["containerMemoryCurrentBytes"] if agg["_memorySamples"] > 0 else None
                ),
                "containerMemoryMaxBytes": (
                    agg["containerMemoryMaxBytes"] if agg["_maxSamples"] > 0 else None
                ),
                "transponderRssBytes": (
                    agg["transponderRssBytes"] if agg["_rssSamples"] > 0 else None
                ),
                "primaryAppRssBytes": (
                    agg["primaryAppRssBytes"] if agg["_appRssSamples"] > 0 else None
                ),
                "cpuPct": round(agg["cpuPct"] / agg["_cpuSamples"], 3) if agg["_cpuSamples"] > 0 else None,
            }
            if rollup == "container":
                row["instanceId"] = agg["instanceId"]
            else:
                row["containerCount"] = len(agg["_containerSet"])
            rows.append(row)

        rows = rows[:max_rows]
        return {
            "data": rows,
            "meta": {
                "totalRows": len(rows),
                "bucketWidthSec": bucket_width_sec,
                "rollup": rollup,
                "start": _format_ts(start),
                "end": _format_ts(end),
            },
        }

    def query_go_dark_status(
        self,
        service_name: str | None = None,
        environment: str | None = None,
        max_rows: int = 1000,
    ) -> dict:
        """Latest GO_DARK state per instance from heartbeat data."""
        # Scan all date directories (no time range filter for go-dark status)
        instances: dict[tuple[str, str, str], dict] = {}

        for _date_str, date_dir in self._all_date_dirs():
            for svc_name, env_name, partition_dir in self._service_env_dirs(
                date_dir, service_name, environment
            ):
                for rec in self._read_jsonl(partition_dir / "heartbeat.jsonl"):
                    payload = rec.get("payload", {})
                    identity = payload.get("identity", {})
                    svc = identity.get("serviceName", svc_name)
                    env = identity.get("environment", env_name)
                    inst_id = identity.get("instanceId", "")
                    if not inst_id:
                        continue
                    sent_at = payload.get("sentAt") or rec.get("receivedAt")
                    ts = _parse_ts(sent_at) if sent_at else None
                    if not ts:
                        continue

                    go_dark = payload.get("status", {}).get("goDark", False)
                    key = (_safe_name(svc), _safe_name(env), inst_id)
                    existing = instances.get(key)
                    if existing is None or ts > existing["ts"]:
                        instances[key] = {
                            "ts": ts,
                            "goDark": bool(go_dark),
                            "lastHeartbeatAt": ts,
                        }

        data = []
        for (svc, env, inst_id), info in sorted(instances.items()):
            data.append({
                "serviceName": svc,
                "environment": env,
                "instanceId": inst_id,
                "goDark": info["goDark"],
                "lastHeartbeatAt": _format_ts(info["lastHeartbeatAt"]),
                "reportedAt": _format_ts(info["ts"]),
            })

        data = data[:max_rows]
        return {
            "data": data,
            "meta": {
                "totalRows": len(data),
            },
        }

    def query_recent_events(
        self,
        start: datetime,
        end: datetime,
        service_name: str | None = None,
        environment: str | None = None,
        max_rows: int = 100,
        cursor: str | None = None,
        severity: str | None = None,
        event_type: str | None = None,
    ) -> dict:
        """Paginated recent events with redaction-safe projection (no payload)."""
        offset = _decode_cursor(cursor)
        all_events: list[dict] = []

        date_dirs = self._date_dirs_in_range(start, end)
        # Reverse for recent-first ordering
        for _date_str, date_dir in reversed(date_dirs):
            for svc_name, env_name, partition_dir in self._service_env_dirs(
                date_dir, service_name, environment
            ):
                for rec in self._read_jsonl(partition_dir / "events.jsonl"):
                    payload = rec.get("payload", {})
                    batch_id = payload.get("batchId")
                    session_id = payload.get("transponderSessionId")
                    events = payload.get("events", [])

                    # Extract service context from first event tags
                    first_tags = events[0].get("tags", {}) if events else {}
                    rec_svc = first_tags.get("serviceName", svc_name)
                    rec_env = first_tags.get("environment", env_name)

                    for event in events:
                        ts_str = event.get("ts")
                        ts = _parse_ts(ts_str) if ts_str else None
                        if not ts or ts < start or ts > end:
                            continue

                        evt_severity = event.get("severity", "info")
                        evt_type = event.get("type", "")

                        if severity and evt_severity != severity:
                            continue
                        if event_type and evt_type != event_type:
                            continue

                        row: dict = {
                            "ts": ts_str,
                            "type": evt_type,
                            "severity": evt_severity,
                            "serviceName": rec_svc,
                            "environment": rec_env,
                        }
                        tags = event.get("tags")
                        if tags:
                            row["tags"] = {str(k): str(v) for k, v in tags.items()}
                        if batch_id:
                            row["batchId"] = batch_id
                        if session_id:
                            row["transponderSessionId"] = session_id
                        all_events.append((ts, row))

        # Sort by timestamp descending (most recent first)
        all_events.sort(key=lambda x: x[0], reverse=True)
        total = len(all_events)
        page = [row for _, row in all_events[offset : offset + max_rows]]
        next_offset = offset + max_rows
        next_cursor = _encode_cursor(next_offset) if next_offset < total else None

        return {
            "data": page,
            "meta": {
                "totalRows": total,
                "start": _format_ts(start),
                "end": _format_ts(end),
                "cursor": next_cursor,
            },
        }
