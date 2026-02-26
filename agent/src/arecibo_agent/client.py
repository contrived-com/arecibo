from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class CollectorClient:
    def __init__(self, base_url: str, api_key: str, timeout_sec: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, method=method, headers=headers, data=data)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return resp.status, None
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body.strip() else None
            except json.JSONDecodeError:
                parsed = None
            return exc.code, parsed
        except Exception:
            return 0, None

    def health(self) -> tuple[int, dict[str, Any] | None]:
        return self._request("GET", "/health")

    def announce(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        return self._request("POST", "/announce", payload=payload)

    def policy(self, service_name: str, environment: str) -> tuple[int, dict[str, Any] | None]:
        return self._request(
            "GET",
            "/policy",
            query={"serviceName": service_name, "environment": environment},
        )

    def heartbeat(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        return self._request("POST", "/heartbeat", payload=payload)

    def events_batch(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        return self._request("POST", "/events:batch", payload=payload)
