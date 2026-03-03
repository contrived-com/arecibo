"""Telemetry retention module.

Prunes telemetry partitions older than a configurable number of days
(default 180) from the date-partitioned directory tree under data/telemetry/.

The date dimension is the top-level directory (YYYY-MM-DD format).
Retention walks these directories, compares against the cutoff date,
and removes stale partitions entirely.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("arecibo.telemetry_retention")

DEFAULT_RETENTION_DAYS = 180


def run_retention(
    base_dir: str | Path,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Prune telemetry partitions older than retention_days.

    Args:
        base_dir: Root telemetry directory (e.g. data/telemetry/).
        retention_days: Number of days to retain. Partitions older than this are pruned.
        dry_run: If True, log what would be pruned without deleting.
        now: Override current time for deterministic testing.

    Returns:
        Summary dict with scanned, pruned, skipped, and error counts.
    """
    base = Path(base_dir)
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).date()

    summary = {"scanned": 0, "pruned": 0, "skipped": 0, "errors": 0}

    if not base.is_dir():
        logger.info(
            "retention_skip_no_dir",
            extra={"fields": {"base_dir": str(base)}},
        )
        return summary

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        summary["scanned"] += 1

        # Parse date from directory name (YYYY-MM-DD)
        try:
            partition_date = datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            # Skip non-date directories (e.g. .gitkeep)
            summary["skipped"] += 1
            continue

        if partition_date < cutoff:
            if dry_run:
                logger.info(
                    "retention_would_prune",
                    extra={"fields": {"partition": name, "cutoff": str(cutoff)}},
                )
            else:
                try:
                    shutil.rmtree(entry)
                    logger.info(
                        "retention_pruned",
                        extra={"fields": {"partition": name}},
                    )
                except Exception:
                    logger.exception(
                        "retention_prune_error",
                        extra={"fields": {"partition": name}},
                    )
                    summary["errors"] += 1
                    continue
            summary["pruned"] += 1
        else:
            summary["skipped"] += 1

    logger.info(
        "retention_complete",
        extra={"fields": {
            "retention_days": retention_days,
            "cutoff": str(cutoff),
            "dry_run": dry_run,
            **summary,
        }},
    )
    return summary


def get_retention_days() -> int:
    """Read retention period from environment, defaulting to 180 days."""
    raw = os.getenv("ARECIBO_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        days = int(raw)
        return max(1, days)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
