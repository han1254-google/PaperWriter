"""ID 生成：trace_id（8 位十六进制）与各类业务 ID。"""
from __future__ import annotations

import secrets

_HEX = "0123456789abcdef"


def trace_id() -> str:
    """每次用户发起的操作生成一个 8 位 trace_id（§11.2）。"""
    return "".join(secrets.choice(_HEX) for _ in range(8))


def new_id(prefix: str) -> str:
    """业务 ID，如 doc_/chk_/dsn_/otl_/drf_。"""
    return f"{prefix}_{secrets.token_hex(8)}"
