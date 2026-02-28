from __future__ import annotations

import json
import os
import socket
import threading
from collections import deque
from typing import Any

from .model import TransponderCounters
from .utils import utc_now


class IngestQueue:
    def __init__(self, max_depth: int) -> None:
        self.max_depth = max_depth
        self._items: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()

    def push(self, item: dict[str, Any], counters: TransponderCounters) -> None:
        with self._lock:
            if len(self._items) >= self.max_depth:
                self._items.popleft()
                counters.events_dropped_total += 1
                counters.events_dropped_by_queue_size_since_last_heartbeat += 1
            self._items.append(item)
            counters.events_received_total += 1
            counters.max_event_queue_depth_since_last_heartbeat = max(
                counters.max_event_queue_depth_since_last_heartbeat, len(self._items)
            )

    def pop_batch(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            n = min(limit, len(self._items))
            return [self._items.popleft() for _ in range(n)]

    def size(self) -> int:
        with self._lock:
            return len(self._items)


class IngestDatagramServer:
    def __init__(
        self,
        socket_path: str,
        buffer_bytes: int,
        queue: IngestQueue,
        counters: TransponderCounters,
    ):
        self.socket_path = socket_path
        self.buffer_bytes = buffer_bytes
        self.queue = queue
        self.counters = counters
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.bind(self.socket_path)
        os.chmod(self.socket_path, 0o666)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                raw, _addr = self._sock.recvfrom(self.buffer_bytes)
            except OSError:
                break
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            event = {
                "ts": payload.get("ts", utc_now()),
                "type": str(payload.get("type", "app.event")),
                "severity": payload.get("severity", "info"),
                "payload": payload.get("payload", payload),
            }
            tags = payload.get("tags")
            if isinstance(tags, dict):
                event["tags"] = {str(k): str(v) for k, v in tags.items()}
            self.queue.push(event, self.counters)
