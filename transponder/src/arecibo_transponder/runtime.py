from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from .client import CollectorClient
from .config import TransponderConfig
from .ingest import IngestDatagramServer, IngestQueue
from .model import Directive, TransponderRuntimeState
from .utils import new_event_id, parse_json_line, setup_logging, utc_now


logger = logging.getLogger(__name__)


class TransponderRuntime:
    def __init__(self, config: TransponderConfig) -> None:
        self.config = config
        self.state = TransponderRuntimeState()
        self.started_monotonic = time.monotonic()
        self.queue = IngestQueue(max_depth=config.queue_max_depth)
        self.ingest_server: IngestDatagramServer | None = None
        self._stop = False

    def install_signal_handlers(self) -> None:
        def _handler(_sig, _frame):
            self._stop = True

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def run(self) -> None:
        setup_logging()
        self.install_signal_handlers()
        self._bootstrap()

        if self.config.ingest_socket_enabled:
            self.ingest_server = IngestDatagramServer(
                socket_path=self.config.ingest_socket_path,
                buffer_bytes=self.config.ingest_socket_buffer_bytes,
                queue=self.queue,
                counters=self.state.counters,
            )
            self.ingest_server.start()
            logger.info("local ingest socket listening at %s", self.config.ingest_socket_path)

        next_heartbeat_at = time.monotonic()
        next_flush_at = time.monotonic() + self.config.events_flush_interval_sec
        next_policy_refresh_at = time.monotonic() + max(
            self.config.heartbeat_min_interval_sec,
            self.state.policy.ttl_sec - self.config.policy_refresh_jitter_sec,
        )

        while not self._stop:
            now = time.monotonic()

            if now >= next_heartbeat_at:
                self._send_heartbeat()
                interval = max(self.config.heartbeat_min_interval_sec, self.state.policy.heartbeat_interval_sec)
                next_heartbeat_at = now + interval

            if now >= next_policy_refresh_at:
                self._refresh_policy()
                next_policy_refresh_at = now + max(
                    self.config.heartbeat_min_interval_sec,
                    self.state.policy.ttl_sec - self.config.policy_refresh_jitter_sec,
                )

            if now >= next_flush_at:
                self._flush_events()
                next_flush_at = now + self.config.events_flush_interval_sec

            time.sleep(0.2)

        if self.ingest_server is not None:
            self.ingest_server.stop()

    def _bootstrap(self) -> None:
        if not self.config.collector_candidates:
            logger.warning("no collector candidates configured; transponder remains local-only")
            return
        if not self.config.api_key:
            logger.warning("TRANSPONDER_API_KEY missing; outbound API calls likely rejected")

        for candidate in self.config.collector_candidates:
            client = CollectorClient(candidate, self.config.api_key, self.config.probe_timeout_sec)
            status, body = client.health()
            if status != 200 or not isinstance(body, dict) or not body.get("ok"):
                continue
            self.state.selected_collector = candidate
            logger.info("selected collector=%s", candidate)
            break

        if not self.state.selected_collector:
            logger.warning("collector probe failed; transponder will retry opportunistically")
            return

        self._announce()
        self._refresh_policy()

    def _client(self) -> CollectorClient | None:
        if not self.state.selected_collector:
            return None
        return CollectorClient(self.state.selected_collector, self.config.api_key, self.config.http_timeout_sec)

    def _identity(self) -> dict[str, Any]:
        return {
            "serviceName": self.config.service_name,
            "environment": self.config.environment,
            "repository": self.config.repository,
            "commitSha": self.config.commit_sha,
            "instanceId": self.config.instance_id,
            "startupTs": self.config.startup_ts,
            "hostname": self.config.hostname,
        }

    def _announce(self) -> None:
        client = self._client()
        if client is None or self.state.go_dark:
            return
        payload = {
            "schemaVersion": "1.0.0",
            "eventType": "announce",
            "eventId": new_event_id("announce"),
            "sentAt": utc_now(),
            "identity": self._identity(),
            "runtime": {
                "transponderPid": os.getpid(),
                "transponderVersion": "0.1.0",
                "pythonVersion": ".".join(str(v) for v in os.sys.version_info[:3]),
            },
        }
        status, body = client.announce(payload)
        if status == 202 and isinstance(body, dict):
            self._apply_directives(body)
            logger.info("announce accepted")
        else:
            logger.warning("announce failed status=%s", status)

    def _refresh_policy(self) -> None:
        client = self._client()
        if client is None or self.state.go_dark:
            return
        status, body = client.policy(self.config.service_name, self.config.environment)
        if status == 200 and isinstance(body, dict):
            policy = body.get("policy", {})
            self.state.policy.session_id = str(body.get("transponderSessionId", ""))
            self.state.policy.ttl_sec = int(body.get("ttlSec", self.state.policy.ttl_sec))
            self.state.policy.policy_version = str(policy.get("policyVersion", ""))
            self.state.policy.enabled = bool(policy.get("enabled", True))
            self.state.policy.heartbeat_interval_sec = int(
                policy.get("heartbeatIntervalSec", self.state.policy.heartbeat_interval_sec)
            )
            self.state.policy.max_batch_size = int(policy.get("maxBatchSize", self.config.max_batch_size))
            logger.info(
                "policy loaded version=%s heartbeat=%ss session=%s",
                self.state.policy.policy_version,
                self.state.policy.heartbeat_interval_sec,
                self.state.policy.session_id,
            )
            return
        if status == 404:
            logger.warning("policy not found for %s/%s", self.config.service_name, self.config.environment)
            return
        logger.warning("policy fetch failed status=%s", status)

    def _send_heartbeat(self) -> None:
        client = self._client()
        if client is None or self.state.go_dark:
            return
        uptime = int(time.monotonic() - self.started_monotonic)
        payload = {
            "schemaVersion": "1.0.0",
            "eventType": "heartbeat",
            "eventId": new_event_id("heartbeat"),
            "sentAt": utc_now(),
            "identity": self._identity(),
            "status": {
                "transponderUptimeSec": uptime,
                "maxEventQueueDepthSinceLastHeartbeat": self.state.counters.max_event_queue_depth_since_last_heartbeat,
                "eventsReceivedTotal": self.state.counters.events_received_total,
                "eventsSentTotal": self.state.counters.events_sent_total,
                "eventsDroppedTotal": self.state.counters.events_dropped_total,
                "eventsDroppedByQueueSizeSinceLastHeartbeat": self.state.counters.events_dropped_by_queue_size_since_last_heartbeat,
                "eventsDroppedByPolicySinceLastHeartbeat": self.state.counters.events_dropped_by_policy_since_last_heartbeat,
                "transponderRssBytes": 0,
                "goDark": self.state.go_dark,
                "policyVersion": self.state.policy.policy_version,
            },
        }
        status, body = client.heartbeat(payload)
        self.state.counters.reset_heartbeat_window()
        if status == 202 and isinstance(body, dict):
            self._apply_directives(body)
            return
        logger.warning("heartbeat failed status=%s", status)

    def _flush_events(self) -> None:
        if self.state.go_dark:
            return
        if not self.state.policy.enabled:
            size = self.queue.size()
            if size > 0:
                self.state.counters.events_dropped_total += size
                self.state.counters.events_dropped_by_policy_since_last_heartbeat += size
                _ = self.queue.pop_batch(size)
            return

        client = self._client()
        if client is None:
            return

        limit = max(1, min(self.state.policy.max_batch_size, self.config.max_batch_size))
        batch = self.queue.pop_batch(limit)
        if not batch:
            return
        if not self.state.policy.session_id:
            logger.warning("no session id; dropping %d events", len(batch))
            self.state.counters.events_dropped_total += len(batch)
            self.state.counters.events_dropped_by_policy_since_last_heartbeat += len(batch)
            return

        payload = {
            "schemaVersion": "1.0.0",
            "batchId": new_event_id("batch"),
            "transponderSessionId": self.state.policy.session_id,
            "sentAt": utc_now(),
            "events": batch,
        }
        status, body = client.events_batch(payload)
        if status == 202:
            self.state.counters.events_sent_total += len(batch)
            if isinstance(body, dict):
                self._apply_directives(body)
            return
        logger.warning("events batch failed status=%s count=%d", status, len(batch))
        for event in batch:
            self.queue.push(event, self.state.counters)

    def _parse_directives(self, body: dict[str, Any]) -> list[Directive]:
        result = body.get("result", {})
        directives_raw = result.get("directives", [])
        parsed: list[Directive] = []
        if not isinstance(directives_raw, list):
            return parsed
        for item in directives_raw:
            if not isinstance(item, dict):
                continue
            directive_type = str(item.get("type", "")).strip()
            if not directive_type:
                continue
            parsed.append(
                Directive(
                    type=directive_type,
                    value=item.get("value"),
                    ttl_sec=item.get("ttlSec"),
                )
            )
        return parsed

    def _apply_directives(self, body: dict[str, Any]) -> None:
        directives = self._parse_directives(body)
        for directive in directives:
            if directive.type == "GO_DARK":
                logger.warning("received GO_DARK directive; suppressing outbound sends")
                self.state.go_dark = True
            elif directive.type == "RESUME":
                logger.info("received RESUME directive")
                self.state.go_dark = False
            elif directive.type == "REFRESH_POLICY":
                logger.info("received REFRESH_POLICY directive")
                self._refresh_policy()
            elif directive.type == "SET_HEARTBEAT_INTERVAL":
                try:
                    interval = int(directive.value)
                    self.state.policy.heartbeat_interval_sec = max(self.config.heartbeat_min_interval_sec, interval)
                    logger.info("heartbeat interval set to %ss", self.state.policy.heartbeat_interval_sec)
                except Exception:
                    logger.warning("invalid SET_HEARTBEAT_INTERVAL value: %r", directive.value)
            elif directive.type == "FLUSH_STATS":
                logger.info(
                    "FLUSH_STATS requested received=%d sent=%d dropped=%d queue=%d",
                    self.state.counters.events_received_total,
                    self.state.counters.events_sent_total,
                    self.state.counters.events_dropped_total,
                    self.queue.size(),
                )
            else:
                logger.info("ignoring unsupported directive type=%s", directive.type)

    def ingest_json_line(self, raw: str) -> None:
        payload = parse_json_line(raw)
        if not payload:
            return
        event = {
            "ts": payload.get("ts", utc_now()),
            "type": str(payload.get("type", "app.event")),
            "severity": payload.get("severity", "info"),
            "payload": payload.get("payload", payload),
        }
        self.queue.push(event, self.state.counters)
