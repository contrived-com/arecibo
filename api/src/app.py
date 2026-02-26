from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse

from .config import Settings, load_policy_overrides
from .logging_json import configure_logging
from .policy_store import PolicyStore, default_policies
from .schemas import schema_registry


logger = logging.getLogger("arecibo.api")
SRC_DIR = os.path.dirname(__file__)
API_DIR = os.path.dirname(SRC_DIR)
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)


def _result(request_id: str, *, status_value: str, error: dict | None = None, directives: list | None = None):
    payload = {"result": {"status": status_value, "requestId": request_id}}
    if error:
        payload["result"]["error"] = error
    if directives:
        payload["result"]["directives"] = directives
    return payload


def _validated_or_400(request: Request, schema_name: str, payload: dict) -> None:
    errors = schema_registry.validate(schema_name, payload)
    if errors:
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


def _go_dark_directives_if_enabled(settings: Settings, endpoint_name: str) -> list[dict]:
    if settings.force_go_dark:
        return [{"type": "GO_DARK"}]
    if endpoint_name in settings.force_go_dark_on:
        return [{"type": "GO_DARK"}]
    return []


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = Settings.from_env()
        policies = default_policies()
        if settings.default_policy_file:
            policies.update(load_policy_overrides(settings.default_policy_file))
        app.state.settings = settings
        app.state.policy_store = PolicyStore(settings.policy_ttl_sec, policies)
        yield

    app = FastAPI(title="Arecibo API", version="0.1.0", lifespan=lifespan)

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

    async def _auth_dependency(
        request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")
    ):
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

    @app.get("/health")
    async def get_health():
        return {"ok": True, "version": app.version}

    @app.post("/announce", status_code=status.HTTP_202_ACCEPTED)
    async def post_announce(payload: dict, request: Request, _: str = Depends(_auth_dependency)):
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
        response_payload = _result(request.state.request_id, status_value="ok")
        _validated_response_or_500("result", response_payload)
        return response_payload

    @app.get("/policy")
    async def get_policy(serviceName: str, environment: str, request: Request, _: str = Depends(_auth_dependency)):
        policy_store: PolicyStore = app.state.policy_store
        policy = policy_store.lookup_policy(serviceName, environment)
        if not policy:
            payload = _result(
                request.state.request_id,
                status_value="rejected",
                error={
                    "code": "policy_not_found",
                    "message": f"No policy configured for service '{serviceName}' in environment '{environment}'.",
                },
            )
            _validated_response_or_500("result", payload)
            return JSONResponse(status_code=404, content=payload)

        if policy["serviceName"] != serviceName or policy["environment"] != environment:
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

        response_payload = policy_store.build_policy_response(serviceName, environment, policy)
        _validated_response_or_500("policy_response", response_payload)
        logger.info(
            "policy_fetched",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "serviceName": serviceName,
                    "environment": environment,
                    "agentSessionId": response_payload["agentSessionId"],
                }
            },
        )
        return response_payload

    @app.post("/heartbeat", status_code=status.HTTP_202_ACCEPTED)
    async def post_heartbeat(payload: dict, request: Request, _: str = Depends(_auth_dependency)):
        _validated_or_400(request, "heartbeat", payload)
        status_payload = payload["status"]
        logger.info(
            "heartbeat_received",
            extra={
                "fields": {
                    "requestId": request.state.request_id,
                    "serviceName": payload["identity"]["serviceName"],
                    "environment": payload["identity"]["environment"],
                    "agentUptimeSec": status_payload["agentUptimeSec"],
                    "eventsReceivedTotal": status_payload["eventsReceivedTotal"],
                    "eventsSentTotal": status_payload["eventsSentTotal"],
                }
            },
        )

        directives = _go_dark_directives_if_enabled(app.state.settings, "heartbeat")
        response_payload = _result(
            request.state.request_id,
            status_value="directive" if directives else "ok",
            directives=directives or None,
        )
        _validated_response_or_500("result", response_payload)
        return response_payload

    @app.post("/events:batch")
    async def post_events_batch(payload: dict, request: Request, _: str = Depends(_auth_dependency)):
        if isinstance(payload, dict) and isinstance(payload.get("events"), list) and len(payload["events"]) > 1000:
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
                    "agentSessionId": payload["agentSessionId"],
                    "batchId": payload["batchId"],
                    "eventCount": event_count,
                }
            },
        )

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
