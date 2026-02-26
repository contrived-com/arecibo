from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Directive:
    type: str
    value: Any = None
    ttl_sec: int | None = None


@dataclass
class PolicyState:
    session_id: str = ""
    policy_version: str = ""
    enabled: bool = True
    heartbeat_interval_sec: int = 30
    max_batch_size: int = 1000
    ttl_sec: int = 60


@dataclass
class AgentCounters:
    events_received_total: int = 0
    events_sent_total: int = 0
    events_dropped_total: int = 0
    events_dropped_by_queue_size_since_last_heartbeat: int = 0
    events_dropped_by_policy_since_last_heartbeat: int = 0
    max_event_queue_depth_since_last_heartbeat: int = 0

    def reset_heartbeat_window(self) -> None:
        self.events_dropped_by_queue_size_since_last_heartbeat = 0
        self.events_dropped_by_policy_since_last_heartbeat = 0
        self.max_event_queue_depth_since_last_heartbeat = 0


@dataclass
class AgentRuntimeState:
    go_dark: bool = False
    selected_collector: str = ""
    request_seq: int = 0
    policy: PolicyState = field(default_factory=PolicyState)
    counters: AgentCounters = field(default_factory=AgentCounters)
