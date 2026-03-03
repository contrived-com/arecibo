"""Tests for telemetry storage writes and partition directory structure."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_store(tmp_path: Path):
    """Create a TelemetryStore pointing at a temp directory."""
    import sys, os
    api_root = os.path.dirname(os.path.dirname(__file__))
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    from src.telemetry_store import TelemetryStore
    return TelemetryStore(tmp_path / "telemetry")


def _read_jsonl(filepath: Path) -> list[dict]:
    lines = filepath.read_text().strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


class TestStoreAnnounce:
    def test_creates_partition_and_writes_record(self, tmp_path):
        store = _make_store(tmp_path)
        payload = {
            "schemaVersion": "1.0.0",
            "eventType": "announce",
            "eventId": "a-001",
            "sentAt": "2026-03-01T12:00:00Z",
            "identity": {
                "serviceName": "web-app",
                "environment": "production",
                "instanceId": "i-001",
                "repository": "github.com/test",
                "commitSha": "abc123",
                "startupTs": "2026-03-01T12:00:00Z",
            },
            "runtime": {"transponderPid": 1, "transponderVersion": "0.1.0"},
        }
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            store.store_announce(payload)

        # Verify directory structure
        announce_file = tmp_path / "telemetry" / "2026-03-01" / "web-app" / "production" / "announce.jsonl"
        assert announce_file.exists()
        records = _read_jsonl(announce_file)
        assert len(records) == 1
        assert records[0]["payload"] == payload
        assert "receivedAt" in records[0]
        assert records[0]["receivedAt"].endswith("Z")

    def test_appends_multiple_records(self, tmp_path):
        store = _make_store(tmp_path)
        identity = {
            "serviceName": "svc",
            "environment": "dev",
            "instanceId": "i-1",
            "repository": "r",
            "commitSha": "c",
            "startupTs": "2026-03-01T12:00:00Z",
        }
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            for i in range(3):
                store.store_announce({
                    "identity": identity,
                    "eventId": f"a-{i}",
                    "sentAt": "2026-03-01T12:00:00Z",
                })

        announce_file = tmp_path / "telemetry" / "2026-03-01" / "svc" / "dev" / "announce.jsonl"
        records = _read_jsonl(announce_file)
        assert len(records) == 3

    def test_sanitizes_service_name(self, tmp_path):
        store = _make_store(tmp_path)
        payload = {
            "identity": {
                "serviceName": "../../../etc/passwd",
                "environment": "prod",
                "instanceId": "i-1",
            },
        }
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            store.store_announce(payload)

        # Verify path traversal was sanitized
        base = tmp_path / "telemetry" / "2026-03-01"
        assert base.exists()
        # Should NOT have created directories outside the base
        assert not (tmp_path / "etc").exists()
        # Sanitized name should exist and not contain path separators
        dirs = list(base.iterdir())
        assert len(dirs) == 1
        assert "/" not in dirs[0].name
        assert "\\" not in dirs[0].name


class TestStoreHeartbeat:
    def test_creates_heartbeat_partition(self, tmp_path):
        store = _make_store(tmp_path)
        payload = {
            "schemaVersion": "1.0.0",
            "eventType": "heartbeat",
            "eventId": "hb-001",
            "sentAt": "2026-03-01T12:01:00Z",
            "identity": {
                "serviceName": "api-svc",
                "environment": "staging",
                "instanceId": "i-001",
            },
            "status": {
                "transponderUptimeSec": 60,
                "goDark": False,
            },
        }
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            store.store_heartbeat(payload)

        hb_file = tmp_path / "telemetry" / "2026-03-01" / "api-svc" / "staging" / "heartbeat.jsonl"
        assert hb_file.exists()
        records = _read_jsonl(hb_file)
        assert len(records) == 1
        assert records[0]["payload"]["status"]["goDark"] is False


class TestStoreEventsBatch:
    def test_creates_events_partition_from_tags(self, tmp_path):
        store = _make_store(tmp_path)
        payload = {
            "schemaVersion": "1.0.0",
            "batchId": "b-001",
            "transponderSessionId": "s-001",
            "sentAt": "2026-03-01T12:02:00Z",
            "events": [
                {
                    "ts": "2026-03-01T12:01:50Z",
                    "type": "http.request",
                    "severity": "info",
                    "payload": {"path": "/health"},
                    "tags": {"serviceName": "web-app", "environment": "prod"},
                }
            ],
        }
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            store.store_events_batch(payload)

        events_file = tmp_path / "telemetry" / "2026-03-01" / "web-app" / "prod" / "events.jsonl"
        assert events_file.exists()
        records = _read_jsonl(events_file)
        assert len(records) == 1
        assert records[0]["payload"]["batchId"] == "b-001"

    def test_falls_back_to_unknown_without_tags(self, tmp_path):
        store = _make_store(tmp_path)
        payload = {
            "batchId": "b-002",
            "transponderSessionId": "s-002",
            "events": [
                {"ts": "2026-03-01T12:01:50Z", "type": "test", "severity": "info", "payload": {}},
            ],
        }
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            store.store_events_batch(payload)

        events_file = tmp_path / "telemetry" / "2026-03-01" / "unknown" / "unknown" / "events.jsonl"
        assert events_file.exists()

    def test_graceful_on_write_failure(self, tmp_path):
        store = _make_store(tmp_path)
        payload = {"identity": {"serviceName": "svc", "environment": "env"}}
        # Make base dir read-only to trigger write failure
        store._base.mkdir(parents=True, exist_ok=True)
        ro_dir = store._base / "2026-03-01"
        ro_dir.mkdir()
        # Create a file where directory is expected to force an error
        blocker = ro_dir / "svc"
        blocker.write_text("blocker")
        with patch("src.telemetry_store._today_str", return_value="2026-03-01"):
            # Should not raise
            store.store_announce(payload)
