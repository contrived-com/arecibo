from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PolicyStore:
    policy_ttl_sec: int
    policies: dict[str, dict]

    def lookup_policy(self, service_name: str, environment: str) -> dict | None:
        key = f"{service_name}:{environment}"
        if key in self.policies:
            return self.policies[key]
        wildcard_key = f"{service_name}:*"
        return self.policies.get(wildcard_key)

    def get_session_id(self, service_name: str, environment: str) -> str:
        raw = f"arecibo:{service_name}:{environment}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))

    def build_policy_response(self, service_name: str, environment: str, policy: dict) -> dict:
        return {
            "schemaVersion": "1.0.0",
            "transponderSessionId": self.get_session_id(service_name, environment),
            "fetchedAt": utc_now(),
            "ttlSec": self.policy_ttl_sec,
            "policy": policy,
        }


def default_policies() -> dict[str, dict]:
    baseline = {
        "policyVersion": "1.0.0",
        "serviceName": "demo-service",
        "environment": "local",
        "enabled": True,
        "defaultSampleRate": 1.0,
        "heartbeatIntervalSec": 30,
        "maxEventQueueDepth": 10000,
        "maxBatchSize": 1000,
        "eventOverrides": {},
        "redactionRules": [],
    }
    return {"demo-service:local": baseline}
