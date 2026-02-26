from __future__ import annotations

from arecibo_agent.config import AgentConfig
from arecibo_agent.runtime import CEARuntime


class FakeClient:
    def __init__(self):
        self.calls = []
        self.policy_payload = {
            "agentSessionId": "session-123",
            "ttlSec": 60,
            "policy": {
                "policyVersion": "1.0.0",
                "enabled": True,
                "heartbeatIntervalSec": 30,
                "maxBatchSize": 1000,
            },
        }

    def health(self):
        self.calls.append(("health",))
        return 200, {"ok": True, "version": "0.1.0"}

    def announce(self, payload):
        self.calls.append(("announce", payload))
        return 202, {"result": {"status": "ok", "requestId": "r-1"}}

    def policy(self, service_name, environment):
        self.calls.append(("policy", service_name, environment))
        return 200, self.policy_payload

    def heartbeat(self, payload):
        self.calls.append(("heartbeat", payload))
        return 202, {"result": {"status": "ok", "requestId": "r-2"}}

    def events_batch(self, payload):
        self.calls.append(("events_batch", payload))
        return 202, {"result": {"status": "ok", "requestId": "r-3"}}


def _config() -> AgentConfig:
    return AgentConfig(
        api_key="test-key",
        collector_candidates=["http://collector:8080"],
        probe_timeout_sec=0.1,
        http_timeout_sec=0.2,
        service_name="demo-service",
        environment="local",
        repository="github.com/contrived-com/arecibo",
        commit_sha="abcdef1",
        instance_id="instance-1",
        startup_ts="2026-02-26T12:00:00Z",
        hostname="host-1",
        heartbeat_interval_sec=30,
        heartbeat_min_interval_sec=5,
        policy_refresh_jitter_sec=2,
        events_flush_interval_sec=1,
        queue_max_depth=1000,
        max_batch_size=1000,
        ingest_socket_enabled=False,
        ingest_socket_path="/tmp/cea.sock",
        ingest_socket_buffer_bytes=65535,
    )


def test_bootstrap_sets_selected_collector_and_policy(monkeypatch):
    runtime = CEARuntime(_config())
    fake = FakeClient()
    monkeypatch.setattr(runtime, "_client", lambda: fake)

    def _bootstrap_client(_base, _api, _timeout):
        return fake

    monkeypatch.setattr("arecibo_agent.runtime.CollectorClient", _bootstrap_client)

    runtime._bootstrap()

    assert runtime.state.selected_collector == "http://collector:8080"
    assert runtime.state.policy.session_id == "session-123"
    assert runtime.state.policy.heartbeat_interval_sec == 30


def test_directives_toggle_go_dark():
    runtime = CEARuntime(_config())
    runtime._apply_directives(
        {
            "result": {
                "status": "directive",
                "requestId": "r-1",
                "directives": [{"type": "GO_DARK"}],
            }
        }
    )
    assert runtime.state.go_dark is True

    runtime._apply_directives(
        {
            "result": {
                "status": "directive",
                "requestId": "r-2",
                "directives": [{"type": "RESUME"}],
            }
        }
    )
    assert runtime.state.go_dark is False


def test_flush_events_sends_batch(monkeypatch):
    runtime = CEARuntime(_config())
    fake = FakeClient()
    runtime.state.selected_collector = "http://collector:8080"
    runtime.state.policy.session_id = "session-123"
    runtime.state.policy.max_batch_size = 1000
    runtime.ingest_json_line('{"type":"demo.event","payload":{"x":1}}')

    monkeypatch.setattr(runtime, "_client", lambda: fake)
    runtime._flush_events()

    sent = [call for call in fake.calls if call[0] == "events_batch"]
    assert len(sent) == 1
    assert sent[0][1]["agentSessionId"] == "session-123"
    assert len(sent[0][1]["events"]) == 1
