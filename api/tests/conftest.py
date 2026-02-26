from __future__ import annotations

import os
import sys
from typing import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def env_defaults(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("ARECIBO_API_KEYS", "test-key")
    monkeypatch.delenv("ARECIBO_FORCE_GO_DARK", raising=False)
    monkeypatch.delenv("ARECIBO_FORCE_GO_DARK_ON", raising=False)
    yield


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    api_root = os.path.dirname(os.path.dirname(__file__))
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    # Import after env setup so app lifespan picks up test values.
    from src.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": os.getenv("ARECIBO_TEST_KEY", "test-key")}


def _sample_identity() -> dict:
    return {
        "serviceName": "demo-service",
        "environment": "local",
        "repository": "github.com/contrived/arecibo",
        "commitSha": "1234567",
        "instanceId": "instance-1",
        "startupTs": "2026-02-26T12:00:00Z",
    }


@pytest.fixture
def sample_announce() -> dict:
    return {
        "schemaVersion": "1.0.0",
        "eventType": "announce",
        "eventId": "announce-0001",
        "sentAt": "2026-02-26T12:00:01Z",
        "identity": _sample_identity(),
        "runtime": {"ceaPid": 42, "ceaVersion": "0.1.0", "pythonVersion": "3.12.0"},
    }


@pytest.fixture
def sample_heartbeat() -> dict:
    return {
        "schemaVersion": "1.0.0",
        "eventType": "heartbeat",
        "eventId": "heartbeat-0001",
        "sentAt": "2026-02-26T12:01:00Z",
        "identity": _sample_identity(),
        "status": {
            "agentUptimeSec": 60,
            "maxEventQueueDepthSinceLastHeartbeat": 4,
            "eventsReceivedTotal": 10,
            "eventsSentTotal": 9,
            "eventsDroppedTotal": 1,
            "eventsDroppedByQueueSizeSinceLastHeartbeat": 1,
            "eventsDroppedByPolicySinceLastHeartbeat": 0,
            "ceaRssBytes": 2048,
            "goDark": False,
        },
    }


@pytest.fixture
def sample_events_batch() -> dict:
    return {
        "schemaVersion": "1.0.0",
        "batchId": "batch-0001",
        "agentSessionId": "session-1",
        "sentAt": "2026-02-26T12:01:30Z",
        "events": [
            {
                "ts": "2026-02-26T12:01:20Z",
                "type": "http.request",
                "severity": "info",
                "payload": {"path": "/health", "status": 200},
            }
        ],
    }
