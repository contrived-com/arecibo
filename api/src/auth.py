from __future__ import annotations

from fastapi import Header, HTTPException, Request


def _result_error(request: Request, code: str, message: str) -> dict:
    return {
        "result": {
            "status": "rejected",
            "requestId": request.state.request_id,
            "error": {"code": code, "message": message},
        }
    }


def api_key_guard(allowed_keys: set[str]):
    async def require_api_key(
        request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")
    ) -> str:
        if not x_api_key:
            raise HTTPException(
                status_code=401,
                detail=_result_error(request, "unauthorized", "Missing X-API-Key."),
            )
        if x_api_key not in allowed_keys:
            raise HTTPException(
                status_code=401,
                detail=_result_error(request, "unauthorized", "Invalid X-API-Key."),
            )
        return x_api_key

    return require_api_key
