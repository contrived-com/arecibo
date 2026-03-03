"""Telemetry file storage module.

Writes accepted ingest payloads to ./data/telemetry/ in structured,
append-friendly JSONL files partitioned by date and service/environment.

Layout:
  data/telemetry/{YYYY-MM-DD}/{serviceName}/{environment}/announce.jsonl
  data/telemetry/{YYYY-MM-DD}/{serviceName}/{environment}/heartbeat.jsonl
  data/telemetry/{YYYY-MM-DD}/{serviceName}/{environment}/events.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("arecibo.telemetry_store")

# Sanitize directory component to prevent path traversal
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]")


def _safe_name(value: str) -> str:
    """Sanitize a string for use as a directory name."""
    return _SAFE_NAME_RE.sub("_", value)[:128] or "_"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TelemetryStore:
    """Append-only JSONL telemetry storage with date/service/env partitions."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _partition_dir(self, date_str: str, service_name: str, environment: str) -> Path:
        return self._base / date_str / _safe_name(service_name) / _safe_name(environment)

    def _append(self, partition: Path, filename: str, record: dict) -> None:
        """Append a single JSON line to a JSONL file. Failures are logged, not raised."""
        try:
            partition.mkdir(parents=True, exist_ok=True)
            filepath = partition / filename
            line = json.dumps(record, separators=(",", ":")) + "\n"
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            logger.exception(
                "telemetry_write_failed",
                extra={"fields": {"partition": str(partition), "filename": filename}},
            )

    def store_announce(self, payload: dict) -> None:
        identity = payload.get("identity", {})
        service_name = identity.get("serviceName", "unknown")
        environment = identity.get("environment", "unknown")
        date_str = _today_str()
        partition = self._partition_dir(date_str, service_name, environment)
        record = {
            "receivedAt": _utc_now_iso(),
            "payload": payload,
        }
        self._append(partition, "announce.jsonl", record)

    def store_heartbeat(self, payload: dict) -> None:
        identity = payload.get("identity", {})
        service_name = identity.get("serviceName", "unknown")
        environment = identity.get("environment", "unknown")
        date_str = _today_str()
        partition = self._partition_dir(date_str, service_name, environment)
        record = {
            "receivedAt": _utc_now_iso(),
            "payload": payload,
        }
        self._append(partition, "heartbeat.jsonl", record)

    def store_events_batch(self, payload: dict) -> None:
        """Store events batch. Extracts service context from the session or batch metadata."""
        # events:batch doesn't carry identity directly; we store with session context
        # The batch includes transponderSessionId which links to an announced identity.
        # For partition purposes, we extract from the first event tags or fall back to "unknown".
        service_name = "unknown"
        environment = "unknown"
        events = payload.get("events", [])
        if events:
            tags = events[0].get("tags", {})
            service_name = tags.get("serviceName", service_name)
            environment = tags.get("environment", environment)
        date_str = _today_str()
        partition = self._partition_dir(date_str, service_name, environment)
        record = {
            "receivedAt": _utc_now_iso(),
            "payload": payload,
        }
        self._append(partition, "events.jsonl", record)

    @property
    def base_dir(self) -> Path:
        return self._base
