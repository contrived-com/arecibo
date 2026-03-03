from __future__ import annotations

from arecibo_transponder.config import TransponderConfig


def test_from_env_uses_explicit_transponder_api_key(monkeypatch):
    monkeypatch.setenv("TRANSPONDER_API_KEY", "explicit-key")
    monkeypatch.setattr(
        "arecibo_transponder.config._read_vault_transponder_api_key",
        lambda: "vault-key",
    )

    cfg = TransponderConfig.from_env(startup_ts="2026-03-03T00:00:00Z")
    assert cfg.api_key == "explicit-key"


def test_from_env_falls_back_to_vault_api_key(monkeypatch):
    monkeypatch.delenv("TRANSPONDER_API_KEY", raising=False)
    monkeypatch.setattr(
        "arecibo_transponder.config._read_vault_transponder_api_key",
        lambda: "vault-key",
    )

    cfg = TransponderConfig.from_env(startup_ts="2026-03-03T00:00:00Z")
    assert cfg.api_key == "vault-key"


def test_from_env_uses_empty_api_key_when_unset(monkeypatch):
    monkeypatch.delenv("TRANSPONDER_API_KEY", raising=False)
    monkeypatch.setattr(
        "arecibo_transponder.config._read_vault_transponder_api_key",
        lambda: "",
    )

    cfg = TransponderConfig.from_env(startup_ts="2026-03-03T00:00:00Z")
    assert cfg.api_key == ""
