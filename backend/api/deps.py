"""API 公共依赖：trace_id 中间件、统一异常处理、请求日志。"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..errors import AppError, load_messages
from ..ids import trace_id as new_trace_id
from ..logging_setup import log_backend, redact, trace_var

_log = logging.getLogger("backend")


class TraceMiddleware(BaseHTTPMiddleware):
    """X-Trace-Id 请求头透传；响应头回传 trace_id（§11.2）。"""

    async def dispatch(self, request: Request, call_next):
        trace = request.headers.get("X-Trace-Id") or new_trace_id()
        if len(trace) > 32:
            trace = trace[:32]
        token = trace_var.set(trace)
        start = time.time()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            trace_var.reset(token)
            duration_ms = int((time.time() - start) * 1000)
            if request.url.path.startswith("/api/") and \
               request.url.path != "/api/client-logs":
                log_backend(trace=trace, module="http",
                            msg=f"http {request.method} {request.url.path}",
                            method=request.method, path=request.url.path,
                            status=status, duration_ms=duration_ms)


def register(app: FastAPI) -> None:
    load_messages()

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.http_status,
                            content={"ok": False, "error": exc.to_dict()},
                            headers={"X-Trace-Id": exc.traceId})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        trace = trace_var.get() or new_trace_id()
        err = AppError("VALIDATION_ERROR", message=str(exc.errors())[:500],
                       trace=trace, http_status=422)
        return JSONResponse(status_code=422,
                            content={"ok": False, "error": err.to_dict()},
                            headers={"X-Trace-Id": trace})

    @app.exception_handler(Exception)
    async def uncaught_handler(request: Request, exc: Exception):
        trace = trace_var.get() or new_trace_id()
        _log.error("uncaught exception trace=%s path=%s error=%s",
                   trace, request.url.path, redact(str(exc)), exc_info=True)
        err = AppError("SYS_UNCAUGHT", message=str(exc), detail=redact(str(exc)),
                       trace=trace, http_status=500)
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": err.to_dict()},
                            headers={"X-Trace-Id": trace})

    app.add_middleware(TraceMiddleware)
