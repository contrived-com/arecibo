"""Tests for query endpoints: fleet-health, heartbeat-freshness, event-throughput,
go-dark-status, and recent-events."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _write_jsonl(filepath: Path, records: list[dict]):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")


@pytest.fixture
def telemetry_dir(tmp_path):
    """Create telemetry base dir and set up app to use it."""
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()
    return tel_dir


@pytest.fixture
def query_client(monkeypatch, telemetry_dir):
    """Create a test client with telemetry_reader pointed at our temp dir."""
    monkeypatch.setenv("ARECIBO_API_KEYS", "test-key")
    monkeypatch.delenv("ARECIBO_FORCE_GO_DARK", raising=False)
    monkeypatch.delenv("ARECIBO_FORCE_GO_DARK_ON", raising=False)
    policy_root = telemetry_dir.parent / "policies"
    policy_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARECIBO_POLICY_ROOT", str(policy_root))

    api_root = os.path.dirname(os.path.dirname(__file__))
    if api_root not in sys.path:
        sys.path.insert(0, api_root)

    from src.app import create_app
    from src.telemetry_reader import TelemetryReader

    app = create_app()
    # Override the telemetry reader and store to use our temp dir
    with TestClient(app) as client:
        app.state.telemetry_reader = TelemetryReader(telemetry_dir)
        yield client, telemetry_dir


@pytest.fixture
def auth():
    return {"X-API-Key": "test-key"}


def _seed_announce(tel_dir: Path, date: str, svc: str, env: str, instance_id: str, sent_at: str):
    _write_jsonl(tel_dir / date / svc / env / "announce.jsonl", [{
        "receivedAt": sent_at,
        "payload": {
            "identity": {
                "serviceName": svc,
                "environment": env,
                "instanceId": instance_id,
                "repository": "test",
                "commitSha": "abc",
                "startupTs": sent_at,
            },
            "sentAt": sent_at,
        },
    }])


def _seed_heartbeat(
    tel_dir: Path, date: str, svc: str, env: str, instance_id: str,
    sent_at: str, go_dark: bool = False, status_overrides: dict | None = None,
):
    status = {
        "transponderUptimeSec": 60,
        "goDark": go_dark,
    }
    if status_overrides:
        status.update(status_overrides)
    _write_jsonl(tel_dir / date / svc / env / "heartbeat.jsonl", [{
        "receivedAt": sent_at,
        "payload": {
            "identity": {
                "serviceName": svc,
                "environment": env,
                "instanceId": instance_id,
            },
            "sentAt": sent_at,
            "status": status,
        },
    }])


def _seed_events(
    tel_dir: Path, date: str, svc: str, env: str,
    events: list[dict], batch_id: str = "b-001", session_id: str = "s-001",
):
    _write_jsonl(tel_dir / date / svc / env / "events.jsonl", [{
        "receivedAt": f"{date}T12:00:00Z",
        "payload": {
            "transponderSessionId": session_id,
            "batchId": batch_id,
            "events": events,
        },
    }])


# ---- Authentication ----

class TestQueryAuth:
    def test_query_requires_api_key(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/fleet-health")
        assert resp.status_code == 401

    def test_query_rejects_invalid_key(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/fleet-health", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_query_accepts_valid_key(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/fleet-health", headers=auth)
        assert resp.status_code == 200


# ---- Fleet Health ----

class TestFleetHealth:
    def test_empty_data(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/fleet-health", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["totalRows"] == 0
        assert "start" in body["meta"]
        assert "end" in body["meta"]

    def test_returns_fleet_health_data(self, query_client, auth):
        client, tel_dir = query_client
        now_str = "2026-03-03T12:00:00Z"
        _seed_announce(tel_dir, "2026-03-03", "web-app", "prod", "i-1", now_str)
        _seed_heartbeat(tel_dir, "2026-03-03", "web-app", "prod", "i-1", now_str)
        _seed_heartbeat(tel_dir, "2026-03-03", "web-app", "prod", "i-2", now_str)

        resp = client.get(
            "/query/fleet-health",
            headers=auth,
            params={"start": "2026-03-03T00:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["serviceName"] == "web-app"
        assert row["environment"] == "prod"
        assert row["instanceCount"] == 2
        assert row["lastAnnouncedAt"] is not None
        assert row["lastHeartbeatAt"] is not None
        assert row["status"] in ("healthy", "stale", "offline")

    def test_service_name_filter(self, query_client, auth):
        client, tel_dir = query_client
        now_str = "2026-03-03T12:00:00Z"
        _seed_heartbeat(tel_dir, "2026-03-03", "svc-a", "prod", "i-1", now_str)
        _seed_heartbeat(tel_dir, "2026-03-03", "svc-b", "prod", "i-1", now_str)

        resp = client.get(
            "/query/fleet-health",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "serviceName": "svc-a",
            },
        )
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["serviceName"] == "svc-a"

    def test_max_rows_limit(self, query_client, auth):
        client, tel_dir = query_client
        now_str = "2026-03-03T12:00:00Z"
        for i in range(5):
            _seed_heartbeat(tel_dir, "2026-03-03", f"svc-{i}", "prod", "i-1", now_str)

        resp = client.get(
            "/query/fleet-health",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "maxRows": 2,
            },
        )
        body = resp.json()
        assert len(body["data"]) <= 2


# ---- Heartbeat Freshness ----

class TestHeartbeatFreshness:
    def test_empty_data(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/heartbeat-freshness", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["cursor"] is None

    def test_returns_freshness_data(self, query_client, auth):
        client, tel_dir = query_client
        _seed_heartbeat(tel_dir, "2026-03-03", "web-app", "prod", "i-1", "2026-03-03T12:00:00Z")

        resp = client.get(
            "/query/heartbeat-freshness",
            headers=auth,
            params={"start": "2026-03-03T00:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        body = resp.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["serviceName"] == "web-app"
        assert row["instanceId"] == "i-1"
        assert row["lastHeartbeatAt"] == "2026-03-03T12:00:00Z"
        assert row["staleSec"] >= 0
        assert row["status"] in ("fresh", "stale", "offline")

    def test_pagination(self, query_client, auth):
        client, tel_dir = query_client
        for i in range(5):
            _seed_heartbeat(
                tel_dir, "2026-03-03", "web-app", "prod", f"i-{i}",
                "2026-03-03T12:00:00Z",
            )

        # Page 1
        resp = client.get(
            "/query/heartbeat-freshness",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "maxRows": 2,
            },
        )
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["meta"]["cursor"] is not None
        assert body["meta"]["totalRows"] == 5

        # Page 2
        resp2 = client.get(
            "/query/heartbeat-freshness",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "maxRows": 2,
                "cursor": body["meta"]["cursor"],
            },
        )
        body2 = resp2.json()
        assert len(body2["data"]) == 2

    def test_staleness_threshold(self, query_client, auth):
        client, tel_dir = query_client
        # Old heartbeat - should be stale/offline
        _seed_heartbeat(tel_dir, "2026-03-03", "web-app", "prod", "i-1", "2026-03-03T00:00:00Z")

        resp = client.get(
            "/query/heartbeat-freshness",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "stalenessThresholdSec": 60,
            },
        )
        body = resp.json()
        assert len(body["data"]) == 1
        # Given the heartbeat is hours/days old, it should be stale or offline
        assert body["data"][0]["status"] in ("stale", "offline")


# ---- Event Throughput ----

class TestEventThroughput:
    def test_empty_data(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/event-throughput", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)
        assert body["meta"]["bucketWidthSec"] == 60

    def test_returns_bucketed_counts(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T12:00:10Z", "type": "http.req", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T12:00:20Z", "type": "http.req", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T12:01:10Z", "type": "http.req", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/event-throughput",
            headers=auth,
            params={
                "start": "2026-03-03T12:00:00Z",
                "end": "2026-03-03T12:05:00Z",
                "bucketWidthSec": 60,
            },
        )
        body = resp.json()
        assert len(body["data"]) == 5  # 5 minutes = 5 buckets
        # First bucket should have 2 events
        assert body["data"][0]["count"] == 2
        # Second bucket should have 1 event
        assert body["data"][1]["count"] == 1
        assert body["meta"]["bucketWidthSec"] == 60

    def test_custom_bucket_width(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T12:00:10Z", "type": "test", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/event-throughput",
            headers=auth,
            params={
                "start": "2026-03-03T12:00:00Z",
                "end": "2026-03-03T12:10:00Z",
                "bucketWidthSec": 300,
            },
        )
        body = resp.json()
        assert body["meta"]["bucketWidthSec"] == 300
        assert len(body["data"]) == 2  # 10 min / 5 min = 2 buckets

    def test_bucket_width_validation(self, query_client, auth):
        client, _ = query_client
        # Below minimum (10)
        resp = client.get(
            "/query/event-throughput",
            headers=auth,
            params={"bucketWidthSec": 5},
        )
        assert resp.status_code == 422  # FastAPI validation error


# ---- GO_DARK Status ----

class TestGoDarkStatus:
    def test_empty_data(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/go-dark-status", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["totalRows"] == 0

    def test_returns_go_dark_status(self, query_client, auth):
        client, tel_dir = query_client
        _seed_heartbeat(tel_dir, "2026-03-03", "web-app", "prod", "i-1", "2026-03-03T12:00:00Z", go_dark=True)
        _seed_heartbeat(tel_dir, "2026-03-03", "api-svc", "prod", "i-2", "2026-03-03T12:00:00Z", go_dark=False)

        resp = client.get("/query/go-dark-status", headers=auth)
        body = resp.json()
        assert body["meta"]["totalRows"] == 2
        # Find the go_dark=True entry
        dark_rows = [r for r in body["data"] if r["goDark"] is True]
        assert len(dark_rows) == 1
        assert dark_rows[0]["serviceName"] == "web-app"

    def test_service_filter(self, query_client, auth):
        client, tel_dir = query_client
        _seed_heartbeat(tel_dir, "2026-03-03", "svc-a", "prod", "i-1", "2026-03-03T12:00:00Z")
        _seed_heartbeat(tel_dir, "2026-03-03", "svc-b", "prod", "i-1", "2026-03-03T12:00:00Z")

        resp = client.get(
            "/query/go-dark-status",
            headers=auth,
            params={"serviceName": "svc-a"},
        )
        body = resp.json()
        assert body["meta"]["totalRows"] == 1


# ---- Container Metrics ----

class TestContainerMetrics:
    def test_returns_per_container_network_and_memory_metrics(
        self,
        query_client,
        auth,
    ):
        client, tel_dir = query_client
        _seed_heartbeat(
            tel_dir,
            "2026-03-03",
            "svc",
            "prod",
            "i-1",
            "2026-03-03T12:00:10Z",
            status_overrides={
                "containerRxBytesSinceLastHeartbeat": 1200,
                "containerTxBytesSinceLastHeartbeat": 600,
                "containerMemoryCurrentBytes": 1000,
                "containerMemoryMaxBytes": 2000,
            },
        )
        _seed_heartbeat(
            tel_dir,
            "2026-03-03",
            "svc",
            "prod",
            "i-1",
            "2026-03-03T12:00:20Z",
            status_overrides={
                "containerRxBytesSinceLastHeartbeat": 1300,
                "containerTxBytesSinceLastHeartbeat": 700,
                "containerMemoryCurrentBytes": 1100,
                "containerMemoryMaxBytes": 2100,
            },
        )

        resp = client.get(
            "/query/container-metrics",
            headers=auth,
            params={
                "start": "2026-03-03T12:00:00Z",
                "end": "2026-03-03T12:01:00Z",
                "bucketWidthSec": 30,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["rollup"] == "container"
        assert body["meta"]["bucketWidthSec"] == 30
        rows = [r for r in body["data"] if r["instanceId"] == "i-1"]
        assert len(rows) == 1
        row = rows[0]
        assert row["networkRxBytes"] == 2500
        assert row["networkTxBytes"] == 1300
        assert row["containerMemoryCurrentBytes"] == 2100
        assert row["containerMemoryMaxBytes"] == 4100

    def test_service_rollup_counts_containers(self, query_client, auth):
        client, tel_dir = query_client
        _seed_heartbeat(
            tel_dir,
            "2026-03-03",
            "svc",
            "prod",
            "i-1",
            "2026-03-03T12:00:10Z",
            status_overrides={
                "containerRxBytesSinceLastHeartbeat": 100,
                "containerTxBytesSinceLastHeartbeat": 50,
            },
        )
        _seed_heartbeat(
            tel_dir,
            "2026-03-03",
            "svc",
            "prod",
            "i-2",
            "2026-03-03T12:00:15Z",
            status_overrides={
                "containerRxBytesSinceLastHeartbeat": 200,
                "containerTxBytesSinceLastHeartbeat": 100,
            },
        )

        resp = client.get(
            "/query/container-metrics",
            headers=auth,
            params={
                "start": "2026-03-03T12:00:00Z",
                "end": "2026-03-03T12:01:00Z",
                "bucketWidthSec": 30,
                "rollup": "service",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        rows = [
            r
            for r in body["data"]
            if r["serviceName"] == "svc" and r["environment"] == "prod"
        ]
        assert len(rows) == 1
        assert rows[0]["containerCount"] == 2
        assert rows[0]["networkRxBytes"] == 300
        assert rows[0]["networkTxBytes"] == 150

    def test_rollup_validation(self, query_client, auth):
        client, _ = query_client
        resp = client.get(
            "/query/container-metrics",
            headers=auth,
            params={"rollup": "invalid"},
        )
        assert resp.status_code == 422


# ---- Recent Events ----

class TestRecentEvents:
    def test_empty_data(self, query_client, auth):
        client, _ = query_client
        resp = client.get("/query/recent-events", headers=auth)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["cursor"] is None

    def test_returns_events_without_payload(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {
                "ts": "2026-03-03T12:00:10Z",
                "type": "http.request",
                "severity": "info",
                "payload": {"secret": "should-not-appear"},
                "tags": {"serviceName": "web-app", "environment": "prod"},
            },
        ]
        _seed_events(tel_dir, "2026-03-03", "web-app", "prod", events)

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={"start": "2026-03-03T00:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        body = resp.json()
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["type"] == "http.request"
        assert row["severity"] == "info"
        assert "payload" not in row  # redacted
        assert row["serviceName"] == "web-app"

    def test_severity_filter(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T12:00:10Z", "type": "a", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T12:00:20Z", "type": "b", "severity": "error", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "severity": "error",
            },
        )
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["type"] == "b"

    def test_type_filter(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T12:00:10Z", "type": "http.request", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T12:00:20Z", "type": "db.query", "severity": "info", "payload": {},
             "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "type": "db.query",
            },
        )
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["type"] == "db.query"

    def test_pagination(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": f"2026-03-03T12:00:{10+i:02d}Z", "type": "test", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}}
            for i in range(5)
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "maxRows": 2,
            },
        )
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["meta"]["totalRows"] == 5
        assert body["meta"]["cursor"] is not None

        # Page 2
        resp2 = client.get(
            "/query/recent-events",
            headers=auth,
            params={
                "start": "2026-03-03T00:00:00Z",
                "end": "2026-03-03T23:59:59Z",
                "maxRows": 2,
                "cursor": body["meta"]["cursor"],
            },
        )
        body2 = resp2.json()
        assert len(body2["data"]) == 2

    def test_events_sorted_recent_first(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T12:00:10Z", "type": "first", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T12:00:30Z", "type": "third", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T12:00:20Z", "type": "second", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={"start": "2026-03-03T00:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        body = resp.json()
        types = [e["type"] for e in body["data"]]
        assert types == ["third", "second", "first"]

    def test_includes_batch_and_session_ids(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T12:00:10Z", "type": "test", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events,
                      batch_id="batch-42", session_id="session-99")

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={"start": "2026-03-03T00:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        body = resp.json()
        assert body["data"][0]["batchId"] == "batch-42"
        assert body["data"][0]["transponderSessionId"] == "session-99"


# ---- Cross-partition queries ----

class TestCrossPartition:
    def test_query_spans_multiple_dates(self, query_client, auth):
        client, tel_dir = query_client
        _seed_heartbeat(tel_dir, "2026-03-02", "web-app", "prod", "i-1", "2026-03-02T23:00:00Z")
        _seed_heartbeat(tel_dir, "2026-03-03", "web-app", "prod", "i-1", "2026-03-03T01:00:00Z")

        resp = client.get(
            "/query/heartbeat-freshness",
            headers=auth,
            params={"start": "2026-03-02T00:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        body = resp.json()
        # Should find the latest heartbeat across both dates
        assert len(body["data"]) == 1
        assert body["data"][0]["lastHeartbeatAt"] == "2026-03-03T01:00:00Z"

    def test_time_range_excludes_out_of_range(self, query_client, auth):
        client, tel_dir = query_client
        events = [
            {"ts": "2026-03-03T06:00:00Z", "type": "early", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}},
            {"ts": "2026-03-03T18:00:00Z", "type": "late", "severity": "info",
             "payload": {}, "tags": {"serviceName": "svc", "environment": "prod"}},
        ]
        _seed_events(tel_dir, "2026-03-03", "svc", "prod", events)

        resp = client.get(
            "/query/recent-events",
            headers=auth,
            params={"start": "2026-03-03T12:00:00Z", "end": "2026-03-03T23:59:59Z"},
        )
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["type"] == "late"
