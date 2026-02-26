from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .vault_client import get_vault_client


@dataclass(frozen=True)
class Settings:
    api_keys: set[str]
    force_go_dark: bool
    force_go_dark_on: set[str]
    policy_ttl_sec: int
    default_policy_file: str

    @classmethod
    def from_env(cls) -> "Settings":
        vault_path = os.getenv("ARECIBO_VAULT_PATH", "arecibo/config")
        vault_key = os.getenv("ARECIBO_API_KEYS_FIELD", "arecibo_api_keys")
        keys_raw: str | None = None

        vault_client = get_vault_client()
        if vault_client.configured:
            keys_raw = vault_client.get_secret(vault_path, vault_key)
            if not keys_raw:
                raise RuntimeError(
                    f"Vault is configured but no API key material found at secret/{vault_path} field {vault_key}."
                )

        if not keys_raw:
            # Local dev/test fallback only when Vault is not configured.
            keys_raw = os.getenv("ARECIBO_API_KEYS", "local-dev-key")

        keys = {item.strip() for item in keys_raw.split(",") if item.strip()}
        if not keys:
            raise RuntimeError("Arecibo API key set is empty.")

        force_raw = os.getenv("ARECIBO_FORCE_GO_DARK", "false").lower()
        force_go_dark = force_raw in {"1", "true", "yes", "on"}

        force_on_raw = os.getenv("ARECIBO_FORCE_GO_DARK_ON", "")
        force_on = {item.strip() for item in force_on_raw.split(",") if item.strip()}

        ttl_raw = os.getenv("ARECIBO_POLICY_TTL_SEC", "60")
        policy_ttl_sec = max(5, int(ttl_raw))

        default_policy_file = os.getenv("ARECIBO_POLICY_FILE", "")

        return cls(
            api_keys=keys,
            force_go_dark=force_go_dark,
            force_go_dark_on=force_on,
            policy_ttl_sec=policy_ttl_sec,
            default_policy_file=default_policy_file,
        )


def load_policy_overrides(path: str) -> dict[str, dict]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Policy file must be a JSON object keyed by service/environment.")
    return raw
