"""统一错误对象 AppError 与错误码（产品文档 §11.3）。

文案单一来源：shared/error-messages.zh-CN.json。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from fastapi import HTTPException

from .ids import trace_id as _new_trace_id

_MESSAGES_PATH = Path(__file__).resolve().parent.parent / "shared" / "error-messages.zh-CN.json"

_MESSAGES: dict[str, Any] = {}
_FALLBACK: dict[str, str] = {"userMessage": "发生了未预期的问题，已记录日志",
                             "suggestion": "请重试；仍失败可导出诊断包反馈"}


def load_messages() -> None:
    """后端启动时加载一次。"""
    global _MESSAGES, _FALLBACK
    try:
        data = json.loads(_MESSAGES_PATH.read_text(encoding="utf-8"))
        _MESSAGES = {k: v for k, v in data.items() if not k.startswith("$")}
        _FALLBACK = data.get("$meta", {}).get("fallback", _FALLBACK)
    except (OSError, json.JSONDecodeError):
        _MESSAGES = {}
        _FALLBACK = {"userMessage": "发生了未预期的问题，已记录日志", "suggestion": ""}


_STAGE_ORDER: ClassVar[list[str]] = [
    "config", "upload", "parse", "embed", "retrieve", "generate", "save", "export", "unknown",
]

_DEFAULT_HTTP_STATUS = {
    "CFG_KEY_MISSING": 400, "CFG_KEY_INVALID": 400, "CFG_CONFIG_CORRUPT": 500,
    "CFG_MODEL_DENIED": 400,
    "NET_TIMEOUT": 504, "NET_DNS": 502, "NET_PROXY": 502, "NET_OFFLINE": 503,
    "AI_RATE_LIMIT": 429, "AI_BALANCE": 402, "AI_CONTEXT_LENGTH": 400, "AI_BAD_JSON": 502,
    "AI_TIMEOUT": 504, "AI_CONTENT_FILTER": 400, "AI_SERVER": 502, "AI_EMPTY_OUTPUT": 502,
    "FILE_TOO_LARGE": 413, "FILE_TYPE_UNSUPPORTED": 415, "FILE_ENCRYPTED": 422,
    "FILE_CORRUPT": 422, "FILE_PARSE_FAILED": 422, "FILE_EMPTY_PARSE": 422,
    "FILE_DUPLICATE": 409, "FILE_DOC_CONVERT_FAILED": 422,
    "DB_OPEN_FAILED": 500, "DB_MIGRATION_FAILED": 500, "DB_DISK_FULL": 507,
    "DB_WRITE_FAILED": 500,
    "JOB_CANCELLED": 409, "JOB_TIMEOUT": 504, "JOB_RETRY_EXHAUSTED": 502,
    "JOB_INTERRUPTED": 502, "JOB_ALREADY_RUNNING": 409,
    "EXPORT_WRITE_FAILED": 500, "EXPORT_NO_CONTENT": 400, "EXPORT_INVALID_CITATION": 409,
    "SYS_UNCAUGHT": 500, "SYS_API_FAILED": 503, "SYS_LOG_WRITE_FAILED": 500,
    "SYS_CRASH": 500, "UI_RENDER_ERROR": 500, "UI_UNHANDLED_REJECTION": 500,
    "VALIDATION_ERROR": 422, "NOT_FOUND": 404,
    "UPSTREAM_IN_USE": 409, "UPSTREAM_NOT_READY": 409,
}


class AppError(Exception):
    """业务错误。http_status 可被 FastAPI 异常处理器转为 HTTP 状态码。"""

    def __init__(self, code: str, *, message: str | None = None, detail: str | None = None,
                 stage: str = "unknown", retryable: bool = False, http_status: int | None = None,
                 trace: str | None = None, suggestion: str | None = None) -> None:
        self.code = code
        entry = _MESSAGES.get(code, {})
        self.message = message or entry.get("message", code)
        self.userMessage = entry.get("userMessage", _FALLBACK["userMessage"])
        self.suggestion = suggestion if suggestion is not None else entry.get("suggestion", _FALLBACK["suggestion"])
        self.detail = detail
        self.traceId = trace or _new_trace_id()
        self.stage = stage if stage in _STAGE_ORDER else "unknown"
        self.retryable = retryable
        self.http_status = http_status or _DEFAULT_HTTP_STATUS.get(code, 500)
        super().__init__(self.code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "userMessage": self.userMessage,
            "suggestion": self.suggestion,
            "detail": self.detail,
            "traceId": self.traceId,
            "stage": self.stage,
            "retryable": self.retryable,
        }

    def to_http(self) -> HTTPException:
        return HTTPException(status_code=self.http_status,
                             detail={"ok": False, "error": self.to_dict()})


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, **extra}


def fail(err: AppError) -> dict[str, Any]:
    return {"ok": False, "error": err.to_dict()}
