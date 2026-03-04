"""Query route handlers for Grafana datasource consumption.

Implements the observability query endpoints defined in openapi.yml.
All endpoints are GET with query parameters, authenticated via X-API-Key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request

from .telemetry_reader import TelemetryReader


def _parse_time_range(
    start: str | None,
    end: str | None,
) -> tuple[datetime, datetime]:
    """Parse start/end query params with defaults (last 1 hour)."""
    now = datetime.now(timezone.utc)
    if end:
        try:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            end_dt = now
    else:
        end_dt = now

    if start:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            start_dt = end_dt - timedelta(hours=1)
    else:
        start_dt = end_dt - timedelta(hours=1)

    return start_dt, end_dt


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def create_query_router(auth_dependency) -> APIRouter:
    """Create the query router with the given auth dependency."""
    router = APIRouter(prefix="/query", tags=["observability"])

    def _get_reader(request: Request) -> TelemetryReader:
        return request.app.state.telemetry_reader

    @router.get("/fleet-health")
    async def get_fleet_health(
        request: Request,
        _: str = Depends(auth_dependency),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        serviceName: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        maxRows: int = Query(default=1000, ge=1, le=10000),
    ):
        start_dt, end_dt = _parse_time_range(start, end)
        reader = _get_reader(request)
        return reader.query_fleet_health(
            start=start_dt,
            end=end_dt,
            service_name=serviceName,
            environment=environment,
            max_rows=maxRows,
        )

    @router.get("/heartbeat-freshness")
    async def get_heartbeat_freshness(
        request: Request,
        _: str = Depends(auth_dependency),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        serviceName: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        maxRows: int = Query(default=1000, ge=1, le=10000),
        cursor: str | None = Query(default=None),
        stalenessThresholdSec: int = Query(default=300, ge=1),
    ):
        start_dt, end_dt = _parse_time_range(start, end)
        reader = _get_reader(request)
        return reader.query_heartbeat_freshness(
            start=start_dt,
            end=end_dt,
            staleness_threshold_sec=stalenessThresholdSec,
            service_name=serviceName,
            environment=environment,
            max_rows=maxRows,
            cursor=cursor,
        )

    @router.get("/event-throughput")
    async def get_event_throughput(
        request: Request,
        _: str = Depends(auth_dependency),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        serviceName: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        maxRows: int = Query(default=1000, ge=1, le=10000),
        bucketWidthSec: int = Query(default=60, ge=10, le=86400),
    ):
        start_dt, end_dt = _parse_time_range(start, end)
        reader = _get_reader(request)
        return reader.query_event_throughput(
            start=start_dt,
            end=end_dt,
            bucket_width_sec=bucketWidthSec,
            service_name=serviceName,
            environment=environment,
            max_rows=maxRows,
        )

    @router.get("/go-dark-status")
    async def get_go_dark_status(
        request: Request,
        _: str = Depends(auth_dependency),
        serviceName: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        maxRows: int = Query(default=1000, ge=1, le=10000),
    ):
        reader = _get_reader(request)
        return reader.query_go_dark_status(
            service_name=serviceName,
            environment=environment,
            max_rows=maxRows,
        )

    @router.get("/container-metrics")
    async def get_container_metrics(
        request: Request,
        _: str = Depends(auth_dependency),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        serviceName: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        instanceId: str | None = Query(default=None),
        rollup: str = Query(
            default="container",
            pattern="^(container|service)$",
        ),
        bucketWidthSec: int = Query(default=30, ge=10, le=86400),
        maxRows: int = Query(default=10000, ge=1, le=10000),
    ):
        start_dt, end_dt = _parse_time_range(start, end)
        reader = _get_reader(request)
        return reader.query_container_metrics(
            start=start_dt,
            end=end_dt,
            bucket_width_sec=bucketWidthSec,
            service_name=serviceName,
            environment=environment,
            instance_id=instanceId,
            rollup=rollup,
            max_rows=maxRows,
        )

    @router.get("/recent-events")
    async def get_recent_events(
        request: Request,
        _: str = Depends(auth_dependency),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        serviceName: str | None = Query(default=None),
        environment: str | None = Query(default=None),
        maxRows: int = Query(default=100, ge=1, le=10000),
        cursor: str | None = Query(default=None),
        severity: str | None = Query(default=None),
        type: str | None = Query(default=None),
    ):
        start_dt, end_dt = _parse_time_range(start, end)
        reader = _get_reader(request)
        return reader.query_recent_events(
            start=start_dt,
            end=end_dt,
            service_name=serviceName,
            environment=environment,
            max_rows=maxRows,
            cursor=cursor,
            severity=severity,
            event_type=type,
        )

    return router
