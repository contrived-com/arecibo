"""Tests for telemetry retention pruning."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _import_retention():
    import sys, os
    api_root = os.path.dirname(os.path.dirname(__file__))
    if api_root not in sys.path:
        sys.path.insert(0, api_root)
    from src.telemetry_retention import run_retention, get_retention_days
    return run_retention, get_retention_days


def _create_partition(base: Path, date_str: str, svc: str = "svc", env: str = "prod"):
    """Create a fake telemetry partition with a heartbeat record."""
    partition = base / date_str / svc / env
    partition.mkdir(parents=True, exist_ok=True)
    (partition / "heartbeat.jsonl").write_text(
        json.dumps({"receivedAt": f"{date_str}T12:00:00Z", "payload": {}}) + "\n"
    )
    return partition


class TestRunRetention:
    def test_prunes_old_partitions(self, tmp_path):
        run_retention, _ = _import_retention()
        base = tmp_path / "telemetry"
        # Create partitions at different dates
        _create_partition(base, "2025-01-01")  # old
        _create_partition(base, "2025-06-01")  # old
        _create_partition(base, "2026-03-01")  # recent

        now = datetime(2026, 3, 3, tzinfo=timezone.utc)
        result = run_retention(base, retention_days=180, now=now)

        assert result["scanned"] == 3
        assert result["pruned"] == 2
        assert result["errors"] == 0
        # Old partitions should be gone
        assert not (base / "2025-01-01").exists()
        assert not (base / "2025-06-01").exists()
        # Recent partition should remain
        assert (base / "2026-03-01").exists()

    def test_preserves_recent_partitions(self, tmp_path):
        run_retention, _ = _import_retention()
        base = tmp_path / "telemetry"
        _create_partition(base, "2026-03-01")
        _create_partition(base, "2026-03-02")
        _create_partition(base, "2026-03-03")

        now = datetime(2026, 3, 3, tzinfo=timezone.utc)
        result = run_retention(base, retention_days=180, now=now)

        assert result["pruned"] == 0
        assert result["skipped"] == 3
        assert (base / "2026-03-01").exists()
        assert (base / "2026-03-02").exists()
        assert (base / "2026-03-03").exists()

    def test_dry_run_does_not_delete(self, tmp_path):
        run_retention, _ = _import_retention()
        base = tmp_path / "telemetry"
        _create_partition(base, "2020-01-01")

        now = datetime(2026, 3, 3, tzinfo=timezone.utc)
        result = run_retention(base, retention_days=180, dry_run=True, now=now)

        assert result["pruned"] == 1
        # Directory should still exist in dry-run
        assert (base / "2020-01-01").exists()

    def test_skips_non_date_directories(self, tmp_path):
        run_retention, _ = _import_retention()
        base = tmp_path / "telemetry"
        (base / ".gitkeep").mkdir(parents=True, exist_ok=True)
        (base / "not-a-date").mkdir(parents=True, exist_ok=True)
        _create_partition(base, "2026-03-01")

        now = datetime(2026, 3, 3, tzinfo=timezone.utc)
        result = run_retention(base, retention_days=180, now=now)

        assert result["scanned"] == 3
        assert result["skipped"] == 3  # 2 non-date + 1 recent
        assert result["pruned"] == 0

    def test_handles_missing_base_dir(self, tmp_path):
        run_retention, _ = _import_retention()
        result = run_retention(tmp_path / "nonexistent")
        assert result == {"scanned": 0, "pruned": 0, "skipped": 0, "errors": 0}

    def test_configurable_retention_days(self, tmp_path):
        run_retention, _ = _import_retention()
        base = tmp_path / "telemetry"
        _create_partition(base, "2026-03-01")

        # With 1-day retention, 2-day-old partition should be pruned
        now = datetime(2026, 3, 3, tzinfo=timezone.utc)
        result = run_retention(base, retention_days=1, now=now)

        assert result["pruned"] == 1
        assert not (base / "2026-03-01").exists()


class TestGetRetentionDays:
    def test_default_value(self, monkeypatch):
        _, get_retention_days = _import_retention()
        monkeypatch.delenv("ARECIBO_RETENTION_DAYS", raising=False)
        assert get_retention_days() == 180

    def test_custom_value(self, monkeypatch):
        _, get_retention_days = _import_retention()
        monkeypatch.setenv("ARECIBO_RETENTION_DAYS", "30")
        assert get_retention_days() == 30

    def test_minimum_enforcement(self, monkeypatch):
        _, get_retention_days = _import_retention()
        monkeypatch.setenv("ARECIBO_RETENTION_DAYS", "0")
        assert get_retention_days() == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        _, get_retention_days = _import_retention()
        monkeypatch.setenv("ARECIBO_RETENTION_DAYS", "not-a-number")
        assert get_retention_days() == 180
