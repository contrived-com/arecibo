from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import hvac


logger = logging.getLogger(__name__)


class VaultClient:
    def __init__(self) -> None:
        self.client = None
        self._cache: dict[str, Any] = {}
        self._vault_configured = False
        self._vault_addr: str | None = None
        self._role_id: str | None = None
        self._secret_id: str | None = None
        self._init_client()

    def _init_client(self) -> None:
        self._vault_addr = os.getenv("VAULT_ADDR")
        self._role_id = os.getenv("VAULT_ROLE_ID")
        self._secret_id = os.getenv("VAULT_SECRET_ID")
        if not all([self._vault_addr, self._role_id, self._secret_id]):
            logger.warning("Vault credentials not configured; runtime secret fetch disabled.")
            return
        self._vault_configured = True
        self._authenticate()

    def _authenticate(self) -> bool:
        try:
            self.client = hvac.Client(url=self._vault_addr)
            self.client.auth.approle.login(role_id=self._role_id, secret_id=self._secret_id)
            logger.info("Authenticated to Vault using AppRole.")
            return True
        except Exception as exc:
            logger.error("Vault AppRole login failed: %s", exc)
            self.client = None
            return False

    def _ensure_authenticated(self) -> bool:
        if not self._vault_configured:
            return False
        if self.client and self.client.is_authenticated():
            return True
        self._cache.clear()
        return self._authenticate()

    @property
    def configured(self) -> bool:
        return self._vault_configured

    def get_secret(self, path: str, key: str) -> str | None:
        cache_key = f"{path}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self._ensure_authenticated():
            return None

        try:
            response = self.client.secrets.kv.v2.read_secret_version(path=path, mount_point="secret")
            data = response["data"]["data"]
            result = data.get(key)
            if isinstance(result, str):
                self._cache[cache_key] = result
                return result
        except Exception as exc:
            logger.warning("Vault read failed for secret/%s key %s: %s", path, key, exc)
        return None


@lru_cache(maxsize=1)
def get_vault_client() -> VaultClient:
    return VaultClient()
