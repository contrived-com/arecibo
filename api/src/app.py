from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse

from .config import Settings
from .logging_json import configure_logging
from .policy_store import PolicyStore
from .query_routes import create_query_router
from .schemas import schema_registry
from .telemetry_reader import TelemetryReader
from .telemetry_retention import get_retention_days, run_retention
from .telemetry_store import TelemetryStore


logger = logging.getLogger("arecibo.api")
SRC_DIR = os.path.dirname(__file__)
API_DIR = os.path.dirname(SRC_DIR)
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)


def _result(
    request_id: str,
    *,
    status_value: str,
    error: dict | None = None,
    directives: list | None = None,
):
    payload = {"result": {"status": status_value, "requestId": request_id}}
    if error:
        payload["result"]["error"] = error
    if directives:
        payload["result"]["directives"] = directives
    return payload


def _validated_or_400(request: Request, schema_name: str, payload: dict) -> None:
    errors = schema_registry.validate(schema_name, payload)
    if errors:
        identity = payload.get("identity") if isinstance(payload, dict) else None
        logger.warning(
            "payload_validation_failed",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "schemaName": schema_name,
                    "path": str(request.url.path),
                    "eventType": (
                        payload.get("eventType")
                        if isinstance(payload, dict)
                        else None
                    ),
                    "serviceName": (
                        identity.get("serviceName")
                        if isinstance(identity, dict)
                        else None
                    ),
                    "environment": (
                        identity.get("environment")
                        if isinstance(identity, dict)
                        else None
                    ),
                    "errors": errors,
                }
            },
        )
        message = "; ".join(errors)
        raise HTTPException(
            status_code=400,
            detail=_result(
                request.state.request_id,
                status_value="rejected",
                error={"code": "validation_error", "message": message},
            ),
        )


def _validated_response_or_500(schema_name: str, payload: dict) -> None:
    errors = schema_registry.validate(schema_name, payload)
    if errors:
        raise RuntimeError(f"Schema invalid response for {schema_name}: {'; '.join(errors)}")


def _go_dark_directives_if_enabled(
    settings: Settings,
    endpoint_name: str,
) -> list[dict]:
    if settings.force_go_dark:
        return [{"type": "GO_DARK"}]
    if endpoint_name in settings.force_go_dark_on:
        return [{"type": "GO_DARK"}]
    return []


def _validate_policy_key_part(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} is required.")
    if "/" in clean or "\\" in clean or ".." in clean:
        raise ValueError(f"{field_name} contains invalid path characters.")
    return clean


def _is_trusted_internal_grafana_query(request: Request) -> bool:
    """Allow Grafana-internal query reads without X-API-Key.

    This is intentionally narrow:
    - `/query` endpoints only
    - Client source IP must be private
    - Optional host allowlist for known internal/proxied hostnames
    """
    path = request.url.path or ""
    if not (path == "/query" or path.startswith("/query/")):
        return False

    client_host = request.client.host if request.client else ""
    try:
        if not ipaddress.ip_address(client_host).is_private:
            return False
    except ValueError:
        return False

    host_header = (request.headers.get("host") or "").lower()
    if not host_header:
        return True

    trusted_hosts = {
        "arecibo-api",
        "arecibo-api:8080",
        "127.0.0.1",
        "127.0.0.1:8080",
        "localhost",
        "localhost:8080",
        "arecibo.contrived.com",
        "arecibo.contrived.com:443",
        "contrived.com",
        "contrived.com:443",
        "www.contrived.com",
        "www.contrived.com:443",
    }
    return host_header in trusted_hosts


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = Settings.from_env()
        app.state.settings = settings
        app.state.policy_store = PolicyStore(
            settings.policy_ttl_sec,
            settings.policy_root_dir,
        )
        telemetry_dir = settings.telemetry_root_dir
        app.state.telemetry_store = TelemetryStore(telemetry_dir)
        app.state.telemetry_reader = TelemetryReader(telemetry_dir)
        # Run retention in background so it doesn't block startup
        retention_days = get_retention_days()
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            None, lambda: run_retention(telemetry_dir, retention_days=retention_days),
        )
        yield

    app = FastAPI(title="Arecibo API", version="0.1.0", lifespan=lifespan)

    # Auth dependency needs to be defined before the router
    async def _auth_dependency(
        request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")
    ):
        # Internal Grafana queries from the private service network are allowed
        # regardless of placeholder/empty X-API-Key header values.
        if _is_trusted_internal_grafana_query(request):
            return "internal-grafana-query"
        if not x_api_key:
            raise HTTPException(
                status_code=401,
                detail=_result(
                    request.state.request_id,
                    status_value="rejected",
                    error={"code": "unauthorized", "message": "Missing X-API-Key."},
                ),
            )
        if x_api_key not in app.state.settings.api_keys:
            raise HTTPException(
                status_code=401,
                detail=_result(
                    request.state.request_id,
                    status_value="rejected",
                    error={"code": "unauthorized", "message": "Invalid X-API-Key."},
                ),
            )
        return x_api_key

    app.include_router(create_query_router(_auth_dependency))

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "result" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)

        payload = _result(
            request.state.request_id,
            status_value="rejected",
            error={"code": "http_error", "message": str(exc.detail)},
        )
        _validated_response_or_500("result", payload)
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            "unhandled_exception",
            extra={"fields": {"requestId": request.state.request_id, "error": repr(exc)}},
        )
        payload = _result(
            request.state.request_id,
            status_value="retryable",
            error={"code": "internal_error", "message": "Unhandled server error."},
        )
        _validated_response_or_500("result", payload)
        return JSONResponse(status_code=500, content=payload)

    @app.get("/health")
    async def get_health():
        return {"ok": True, "version": app.version}

    @app.post("/announce", status_code=status.HTTP_202_ACCEPTED)
    async def post_announce(
        payload: dict,
        request: Request,
        _: str = Depends(_auth_dependency),
    ):
        _validated_or_400(request, "announce", payload)
        identity = payload["identity"]
        logger.info(
            "announce_received",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "serviceName": identity["serviceName"],
                    "environment": identity["environment"],
                    "instanceId": identity["instanceId"],
                }
            },
        )
        app.state.telemetry_store.store_announce(payload)
        response_payload = _result(request.state.request_id, status_value="ok")
        _validated_response_or_500("result", response_payload)
        return response_payload

    @app.get("/policy")
    async def get_policy(
        serviceName: str,
        environment: str,
        request: Request,
        _: str = Depends(_auth_dependency),
    ):
        policy_store: PolicyStore = app.state.policy_store
        try:
            service_name = _validate_policy_key_part(serviceName, "serviceName")
            container_name = _validate_policy_key_part(environment, "environment")
        except ValueError as exc:
            payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={"code": "invalid_policy_key", "message": str(exc)},
            )
            _validated_response_or_500("result", payload)
            return JSONResponse(status_code=400, content=payload)

        policy = policy_store.lookup_policy(service_name, container_name)
        if not policy:
            payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={
                    "code": "policy_not_found",
                    "message": (
                        f"No policy configured for service '{service_name}' "
                        f"in container '{container_name}'."
                    ),
                },
            )
            _validated_response_or_500("result", payload)
            return JSONResponse(status_code=404, content=payload)

        if (
            policy["serviceName"] != service_name
            or policy["environment"] != container_name
        ):
            payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={
                    "code": "policy_mismatch",
                    "message": "Policy serviceName/environment mismatch.",
                },
            )
            _validated_response_or_500("result", payload)
            return JSONResponse(status_code=403, content=payload)

        response_payload = policy_store.build_policy_response(
            service_name,
            container_name,
            policy,
        )
        _validated_response_or_500("policy_response", response_payload)
        logger.info(
            "policy_fetched",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "serviceName": service_name,
                    "environment": container_name,
                    "transponderSessionId": response_payload["transponderSessionId"],
                }
            },
        )
        return response_payload

    @app.put("/policy")
    async def put_policy(
        serviceName: str,
        environment: str,
        payload: dict,
        request: Request,
        _: str = Depends(_auth_dependency),
    ):
        try:
            service_name = _validate_policy_key_part(serviceName, "serviceName")
            container_name = _validate_policy_key_part(environment, "environment")
        except ValueError as exc:
            error_payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={"code": "invalid_policy_key", "message": str(exc)},
            )
            _validated_response_or_500("result", error_payload)
            return JSONResponse(status_code=400, content=error_payload)

        _validated_or_400(request, "policy", payload)
        if (
            payload.get("serviceName") != service_name
            or payload.get("environment") != container_name
        ):
            error_payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={
                    "code": "policy_mismatch",
                    "message": "Policy serviceName/environment must match query parameters.",
                },
            )
            _validated_response_or_500("result", error_payload)
            return JSONResponse(status_code=400, content=error_payload)

        policy_store: PolicyStore = app.state.policy_store
        policy_store.put_policy(service_name, container_name, payload)
        ok_payload = _result(request.state.request_id, status_value="ok")
        _validated_response_or_500("result", ok_payload)
        return ok_payload

    @app.delete("/policy")
    async def delete_policy(
        serviceName: str,
        environment: str,
        request: Request,
        _: str = Depends(_auth_dependency),
    ):
        try:
            service_name = _validate_policy_key_part(serviceName, "serviceName")
            container_name = _validate_policy_key_part(environment, "environment")
        except ValueError as exc:
            error_payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={"code": "invalid_policy_key", "message": str(exc)},
            )
            _validated_response_or_500("result", error_payload)
            return JSONResponse(status_code=400, content=error_payload)

        policy_store: PolicyStore = app.state.policy_store
        deleted = policy_store.delete_policy(service_name, container_name)
        if not deleted:
            error_payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={
                    "code": "policy_not_found",
                    "message": (
                        f"No policy configured for service '{service_name}' "
                        f"in container '{container_name}'."
                    ),
                },
            )
            _validated_response_or_500("result", error_payload)
            return JSONResponse(status_code=404, content=error_payload)

        ok_payload = _result(request.state.request_id, status_value="ok")
        _validated_response_or_500("result", ok_payload)
        return ok_payload

    @app.post("/heartbeat", status_code=status.HTTP_202_ACCEPTED)
    async def post_heartbeat(
        payload: dict,
        request: Request,
        _: str = Depends(_auth_dependency),
    ):
        _validated_or_400(request, "heartbeat", payload)
        status_payload = payload["status"]
        logger.info(
            "heartbeat_received",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "serviceName": payload["identity"]["serviceName"],
                    "environment": payload["identity"]["environment"],
                    "transponderUptimeSec": status_payload["transponderUptimeSec"],
                    "eventsReceivedTotal": status_payload["eventsReceivedTotal"],
                    "eventsSentTotal": status_payload["eventsSentTotal"],
                }
            },
        )

        app.state.telemetry_store.store_heartbeat(payload)
        directives = _go_dark_directives_if_enabled(app.state.settings, "heartbeat")
        response_payload = _result(
            request.state.request_id,
            status_value="directive" if directives else "ok",
            directives=directives or None,
        )
        _validated_response_or_500("result", response_payload)
        return response_payload

    @app.post("/events:batch")
    async def post_events_batch(
        payload: dict,
        request: Request,
        _: str = Depends(_auth_dependency),
    ):
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("events"), list)
            and len(payload["events"]) > 1000
        ):
            error_payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={"code": "batch_too_large", "message": "events exceeds maxItems 1000"},
            )
            _validated_response_or_500("result", error_payload)
            return JSONResponse(status_code=413, content=error_payload)

        _validated_or_400(request, "events_batch", payload)

        event_count = len(payload["events"])
        logger.info(
            "events_batch_received",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "transponderSessionId": payload["transponderSessionId"],
                    "batchId": payload["batchId"],
                    "eventCount": event_count,
                }
            },
        )

        app.state.telemetry_store.store_events_batch(payload)
        directives = _go_dark_directives_if_enabled(app.state.settings, "events")
        response_payload = _result(
            request.state.request_id,
            status_value="directive" if directives else "ok",
            directives=directives or None,
        )
        _validated_response_or_500("result", response_payload)
        return JSONResponse(status_code=202, content=response_payload)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("src.app:app", host=host, port=port, reload=False)
