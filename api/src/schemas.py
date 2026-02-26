from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, RefResolver


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"


def _load_schema(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return _strip_ids(json.load(handle))


def _strip_ids(value: Any) -> Any:
    # The repository schemas use non-URI $id values for naming/versioning.
    # jsonschema uses $id for reference scope, which breaks local relative $ref
    # resolution, so we remove $id before validation and resolve from file paths.
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "$id":
                continue
            result[key] = _strip_ids(item)
        return result
    if isinstance(value, list):
        return [_strip_ids(item) for item in value]
    return value


class SchemaRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, Draft202012Validator] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._store = self._build_store()
        self._register_defaults()

    def _build_store(self) -> dict[str, dict[str, Any]]:
        store: dict[str, dict[str, Any]] = {}
        for path in SCHEMA_DIR.rglob("*.json"):
            store[path.resolve().as_uri()] = _load_schema(path)
        return store

    def _register_defaults(self) -> None:
        self.register("result", SCHEMA_DIR / "api" / "result.1.0.0.json")
        self.register("policy_response", SCHEMA_DIR / "policy" / "policy-response.1.0.0.json")
        self.register("announce", SCHEMA_DIR / "ingest" / "announce.1.0.0.json")
        self.register("heartbeat", SCHEMA_DIR / "ingest" / "heartbeat.1.0.0.json")
        self.register("events_batch", SCHEMA_DIR / "ingest" / "events-batch.1.0.0.json")

    def register(self, name: str, path: Path) -> None:
        schema_uri = path.resolve().as_uri()
        schema = self._store[schema_uri]
        resolver = RefResolver(base_uri=schema_uri, referrer=schema, store=self._store)
        validator = Draft202012Validator(schema, resolver=resolver)
        self._validators[name] = validator
        self._schemas[name] = schema

    def validate(self, name: str, payload: Any) -> list[str]:
        validator = self._validators[name]
        return [error.message for error in validator.iter_errors(payload)]

    def schema(self, name: str) -> dict[str, Any]:
        return self._schemas[name]


schema_registry = SchemaRegistry()
