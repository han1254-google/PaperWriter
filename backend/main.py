"""FastAPI 应用入口（产品文档 §1.5 本地网页应用）。

启动：python -m backend.main（端口默认 8000，环境变量 PORT 可覆盖；
启动脚本负责 8000–8010 端口顺延）。
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, APP_VERSION
from .api import deps, designs as api_designs, drafts as api_drafts, files as api_files
from .api import outlines as api_outlines, settings as api_settings, system as api_system
from .config import get_config
from .db import init_db
from .errors import AppError
from .logging_setup import cleanup_old_logs, setup_logging
from .paths import ensure_dirs, get_data_dir, PROJECT_ROOT

_log = logging.getLogger("backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动自检（§11.3）：失败不阻断进程，写入日志并在诊断页展示
    from .logging_setup import log_backend
    data_dir = ensure_dirs()
    setup_logging()
    log_backend(trace=None, module="startup",
                msg=f"startup app_version={APP_VERSION} "
                    f"python={os.sys.version.split()[0]} os={os.sys.platform} "
                    f"data_dir={data_dir}")
    try:
        init_db()
    except AppError as e:
        log_backend(trace=None, module="startup", level="ERROR",
                    msg=f"db init failed code={e.code}", detail=e.message)
    from .jobs import get_runner
    recovered = get_runner().recover_interrupted()
    if recovered:
        log_backend(trace=None, module="startup", level="WARN",
                    msg=f"recovered {recovered} interrupted jobs")
    cfg = get_config()
    keep_days = cfg.get("retention.logs_days", 14)
    total_mb = cfg.get("retention.logs_total_mb", 500)
    removed = cleanup_old_logs(data_dir / "logs", keep_days, total_mb)
    if removed:
        log_backend(trace=None, module="startup", msg=f"cleaned {removed} old logs")
    yield
    log_backend(trace=None, module="startup", msg="shutdown")


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
deps.register(app)

for r in (api_system.router, api_files.router, api_designs.router,
          api_outlines.router, api_drafts.router, api_settings.router):
    app.include_router(r)


@app.get("/api/config-wizard-status")
def wizard_status():
    from .config import get_config as _gc
    cfg = _gc()
    return {"ok": True, "data": {
        "setup_done": cfg.get("setup_done", False),
        "privacy_consent": cfg.get("privacy_consent", False),
        "key_status": {"deepseek": cfg.key_status("deepseek"),
                       "qwen": cfg.key_status("qwen")},
        "data_dir": str(get_data_dir()),
        "version": APP_VERSION,
    }}


# ---------------------------------------------------------------------------
# 前端静态文件（预构建 React，SPA 回退）
# ---------------------------------------------------------------------------
_DIST = PROJECT_ROOT / "frontend" / "dist"


@app.get("/")
def index():
    index_file = _DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"ok": False, "error": {
        "code": "SYS_API_FAILED", "message": "frontend not built",
        "userMessage": "前端文件未构建",
        "suggestion": "开发模式：在 frontend/ 目录执行 npm run build"}})


if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    # SPA 回退：非 API 路径一律回 index.html
    if full_path.startswith("api/"):
        raise AppError("NOT_FOUND", http_status=404)
    target = _DIST / full_path
    if target.is_file():
        return FileResponse(target)
    index_file = _DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    raise AppError("NOT_FOUND", http_status=404)


def main() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
