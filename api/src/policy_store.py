from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PolicyStore:
    policy_ttl_sec: int
    policy_root_dir: str

    def _policy_path(self, service_name: str, container_name: str) -> Path:
        return Path(self.policy_root_dir) / service_name / f"{container_name}.json"

    def lookup_policy(self, service_name: str, container_name: str) -> dict | None:
        path = self._policy_path(service_name, container_name)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError(
                f"Policy file {path} must contain a JSON object."
            )
        return raw

    def put_policy(self, service_name: str, container_name: str, policy: dict) -> None:
        path = self._policy_path(service_name, container_name)
        os.makedirs(path.parent, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(policy, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(path)

    def delete_policy(self, service_name: str, container_name: str) -> bool:
        path = self._policy_path(service_name, container_name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def get_session_id(
        self,
        service_name: str,
        container_name: str,
    ) -> str:
        raw = f"arecibo:{service_name}:{container_name}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))

    def build_policy_response(
        self,
        service_name: str,
        container_name: str,
        policy: dict,
    ) -> dict:
        return {
            "schemaVersion": "1.0.0",
            "transponderSessionId": self.get_session_id(service_name, container_name),
            "fetchedAt": utc_now(),
            "ttlSec": self.policy_ttl_sec,
            "policy": policy,
        }
