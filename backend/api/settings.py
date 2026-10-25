"""设置与模型配置 API（产品文档 §10.2）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..config import get_config
from ..errors import AppError, ok
from ..ids import trace_id
from ..logging_setup import trace_var

router = APIRouter(prefix="/api", tags=["settings"])

_UPDATE_KEYS = {
    "deepseek": ("api_key", "base_url", "model"),
    "qwen": ("api_key", "base_url", "embedding_model"),
}


@router.get("/settings")
def get_settings():
    cfg = get_config()
    data = cfg.redacted()
    return ok({
        "config": data,
        "key_status": {"deepseek": cfg.key_status("deepseek"),
                       "qwen": cfg.key_status("qwen")},
        "setup_done": cfg.get("setup_done", False),
        "privacy_consent": cfg.get("privacy_consent", False),
    })


@router.put("/settings")
def update_settings(body: dict[str, Any]):
    """保存密钥与模型配置。密钥显示 ****末4位 时保持原值不变。"""
    cfg = get_config()
    for provider, keys in _UPDATE_KEYS.items():
        patch = body.get(provider)
        if not isinstance(patch, dict):
            continue
        for k in keys:
            if k not in patch:
                continue
            v = str(patch[k] or "").strip()
            if k == "api_key" and v.startswith("****") and len(v) <= 8:
                continue  # 未修改的掩码值
            cfg.set(f"{provider}.{k}", v)
    if "generation" in body and isinstance(body["generation"], dict):
        for k in ("timeout_s", "max_retries"):
            if k in body["generation"]:
                cfg.set(f"generation.{k}", body["generation"][k])
    cfg.save()
    return get_settings()


@router.post("/settings/test-connection")
async def test_connection(body: dict[str, Any]):
    """测试连接：区分密钥无效 / 余额不足 / 网络不通 / 模型无权限。"""
    provider = body.get("provider")
    if provider not in ("deepseek", "qwen"):
        raise AppError("VALIDATION_ERROR", message="provider 仅支持 deepseek/qwen",
                       http_status=422)
    trace = trace_var.get() or trace_id()
    cfg = get_config()
    if provider == "deepseek":
        from ..ai.clients import chat
        # 临时覆盖用于测试的密钥（不落盘）
        key = body.get("api_key") or cfg.get("deepseek.api_key")
        base = body.get("base_url") or cfg.get("deepseek.base_url")
        model = body.get("model") or cfg.get("deepseek.model")
        if not key:
            raise AppError("CFG_KEY_MISSING", stage="config")
        orig = (cfg.get("deepseek.api_key"), cfg.get("deepseek.base_url"),
                cfg.get("deepseek.model"))
        cfg.set("deepseek.api_key", key)
        cfg.set("deepseek.base_url", base)
        cfg.set("deepseek.model", model)
        try:
            # 注意：deepseek-v4-flash 带思维链，输出预算要同时覆盖思考与正文，
            # 给 512 保证测试响应不会被思考吃光
            await chat([{"role": "user", "content": "请只回复：连接成功"}],
                       trace=trace, module="test_connection", temperature=0.0,
                       max_tokens=512)
            return ok({"ok": True, "message": "连接成功"})
        finally:
            cfg.set("deepseek.api_key", orig[0])
            cfg.set("deepseek.base_url", orig[1])
            cfg.set("deepseek.model", orig[2])
    else:
        from ..ai.clients import embed
        key = body.get("api_key") or cfg.get("qwen.api_key")
        base = body.get("base_url") or cfg.get("qwen.base_url")
        model = body.get("embedding_model") or cfg.get("qwen.embedding_model")
        if not key:
            raise AppError("CFG_KEY_MISSING", stage="config")
        orig = (cfg.get("qwen.api_key"), cfg.get("qwen.base_url"),
                cfg.get("qwen.embedding_model"))
        cfg.set("qwen.api_key", key)
        cfg.set("qwen.base_url", base)
        cfg.set("qwen.embedding_model", model)
        try:
            vecs = await embed(["测试连接"], trace=trace)
            return ok({"ok": True, "message": "连接成功",
                       "dim": len(vecs[0]) if vecs else 0})
        finally:
            cfg.set("qwen.api_key", orig[0])
            cfg.set("qwen.base_url", orig[1])
            cfg.set("qwen.embedding_model", orig[2])


@router.post("/settings/complete-setup")
def complete_setup(body: dict[str, Any] | None = None):
    cfg = get_config()
    cfg.set("setup_done", True)
    cfg.set("privacy_consent", bool((body or {}).get("privacy_consent", False)))
    cfg.save()
    return ok({"setup_done": True})
