from __future__ import annotations

import os
import socket
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    collector_candidates: list[str]
    probe_timeout_sec: float
    http_timeout_sec: float

    service_name: str
    environment: str
    repository: str
    commit_sha: str
    instance_id: str
    startup_ts: str
    hostname: str

    heartbeat_interval_sec: int
    heartbeat_min_interval_sec: int
    policy_refresh_jitter_sec: int
    events_flush_interval_sec: int
    queue_max_depth: int
    max_batch_size: int

    ingest_socket_enabled: bool
    ingest_socket_path: str
    ingest_socket_buffer_bytes: int

    @classmethod
    def from_env(cls, startup_ts: str) -> "AgentConfig":
        collector_candidates_raw = os.getenv(
            "CEA_COLLECTOR_CANDIDATES",
            "http://arecibo-api:8080,https://arecibo.contrived.com",
        )
        collector_candidates = [
            value.strip().rstrip("/")
            for value in collector_candidates_raw.split(",")
            if value.strip()
        ]
        collector_override = os.getenv("CEA_COLLECTOR_URL", "").strip().rstrip("/")
        if collector_override:
            collector_candidates = [collector_override] + collector_candidates
        deduped_candidates: list[str] = []
        for candidate in collector_candidates:
            if candidate and candidate not in deduped_candidates:
                deduped_candidates.append(candidate)

        service_name = os.getenv("CEA_SERVICE_NAME", os.getenv("SERVICE_NAME", "unknown-service"))
        environment = os.getenv("CEA_ENVIRONMENT", os.getenv("ENVIRONMENT", "unknown"))
        repository = os.getenv("CEA_REPOSITORY", os.getenv("GITHUB_REPOSITORY", "unknown-repository"))
        commit_sha = os.getenv("CEA_COMMIT_SHA", os.getenv("GIT_COMMIT", "unknown"))
        instance_id = os.getenv("CEA_INSTANCE_ID", socket.gethostname())
        hostname = os.getenv("HOSTNAME", socket.gethostname())

        api_key = os.getenv("CEA_API_KEY", "").strip()

        return cls(
            api_key=api_key,
            collector_candidates=deduped_candidates,
            probe_timeout_sec=float(os.getenv("CEA_PROBE_TIMEOUT_SEC", "0.8")),
            http_timeout_sec=float(os.getenv("CEA_HTTP_TIMEOUT_SEC", "2.0")),
            service_name=service_name,
            environment=environment,
            repository=repository,
            commit_sha=commit_sha,
            instance_id=instance_id,
            startup_ts=startup_ts,
            hostname=hostname,
            heartbeat_interval_sec=_int("CEA_HEARTBEAT_INTERVAL_SEC", 30, minimum=5),
            heartbeat_min_interval_sec=5,
            policy_refresh_jitter_sec=_int("CEA_POLICY_REFRESH_JITTER_SEC", 2, minimum=0),
            events_flush_interval_sec=_int("CEA_EVENTS_FLUSH_INTERVAL_SEC", 5, minimum=1),
            queue_max_depth=_int("CEA_MAX_EVENT_QUEUE_DEPTH", 10000, minimum=1),
            max_batch_size=_int("CEA_MAX_BATCH_SIZE", 1000, minimum=1),
            ingest_socket_enabled=_bool("CEA_INGEST_SOCKET_ENABLED", True),
            ingest_socket_path=os.getenv("CEA_INGEST_SOCKET_PATH", "/tmp/cea-ingest.sock"),
            ingest_socket_buffer_bytes=_int("CEA_INGEST_SOCKET_BUFFER_BYTES", 65535, minimum=1024),
        )
