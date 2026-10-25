"""系统 API：检索、任务、诊断、前端日志（产品文档 §11）。"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from .. import APP_NAME, APP_VERSION, db
from ..config import get_config
from ..errors import AppError, ok
from ..ids import trace_id
from ..logging_setup import LOG_WRITE_BROKEN, cleanup_old_logs, redact, trace_var
from ..paths import get_data_dir

router = APIRouter(prefix="/api", tags=["system"])


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
@router.get("/search")
async def search(q: str, category_id: int | None = None,
                 document_id: int | None = None, limit: int = 20):
    from ..search.hybrid import hybrid_search
    if not q.strip():
        return ok([])
    scope: dict[str, Any] = {"all": True}
    if category_id:
        scope = {"category_id": category_id}
    if document_id:
        scope = {"document_ids": [document_id]}
    trace = trace_var.get() or trace_id()
    results = await hybrid_search([q], scope=scope, trace=trace, top_n=limit)
    return ok(results)


@router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: int):
    row = db.fetch_one(
        "SELECT c.*, d.title, d.authors, d.publisher, d.published_at, d.doc_type, "
        "d.id AS document_id FROM chunk c JOIN document d ON d.id=c.document_id "
        "WHERE c.id=?", (chunk_id,))
    if not row:
        raise AppError("NOT_FOUND")
    return ok(row)


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}")
def get_job(job_id: int):
    row = db.fetch_one("SELECT * FROM job WHERE id=?", (job_id,))
    if not row:
        raise AppError("NOT_FOUND")
    return ok(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    from ..jobs import get_runner
    cancelled = get_runner().cancel(job_id)
    return ok({"cancelled": cancelled})


@router.get("/stream/{job_id}")
async def stream_job(job_id: int):
    """生成任务事件流（NDJSON）。"""
    import asyncio
    from fastapi.responses import StreamingResponse
    from ..api.stream import stream_events

    async def gen():
        async for event in stream_events(job_id):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# 健康与自检
# ---------------------------------------------------------------------------
@router.get("/health")
def health():
    return ok({"status": "ok", "app": APP_NAME, "version": APP_VERSION,
               "data_dir": str(get_data_dir())})


@router.post("/selfcheck")
def selfcheck():
    results: dict[str, Any] = {"passed": True, "items": []}

    def add(name: str, ok_: bool, detail: str = ""):
        if not ok_:
            results["passed"] = False
        results["items"].append({"name": name, "ok": ok_, "detail": detail})

    data_dir = get_data_dir()
    add("数据目录可写", os.access(data_dir, os.W_OK), str(data_dir))
    try:
        db.init_db()
        add("数据库可打开", True, str(db.db_path()))
    except Exception as e:
        add("数据库可打开", False, str(e))
    try:
        cfg = get_config()
        add("配置可解析", True, "deepseek=" + cfg.key_status("deepseek") +
            " qwen=" + cfg.key_status("qwen"))
    except Exception as e:
        add("配置可解析", False, str(e))
    logs = data_dir / "logs"
    logs.mkdir(exist_ok=True)
    add("日志可写", os.access(logs, os.W_OK) and not LOG_WRITE_BROKEN.is_set(),
        str(logs))
    # 磁盘空间
    try:
        usage = shutil.disk_usage(data_dir)
        free_gb = usage.free / (1 << 30)
        add("磁盘空间", free_gb > 0.5, f"剩余 {free_gb:.1f} GB")
    except OSError as e:
        add("磁盘空间", False, str(e))
    # FTS5 / sqlite-vec
    try:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        add("FTS5 全文索引", True)
        conn.close()
    except Exception as e:
        add("FTS5 全文索引", False, str(e))
    try:
        import sqlite_vec  # noqa: F401
        add("sqlite-vec", True)
    except ImportError:
        add("sqlite-vec", False, "将使用 NumPy 降级检索")
    # 网络连通性：直接探测两个模型服务地址（ping 8.8.8.8 在国内常被墙，无参考价值）
    def check_host(url: str) -> tuple[bool, str]:
        import httpx
        try:
            with httpx.Client(timeout=5, follow_redirects=True) as c:
                r = c.get(url)
            return True, f"HTTP {r.status_code}"
        except httpx.ConnectTimeout:
            return False, "连接超时"
        except httpx.ConnectError:
            return False, "无法连接（检查网络/代理/DNS）"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:40]

    cfg0 = get_config()
    ds_ok, ds_msg = check_host(cfg0.get("deepseek.base_url", "https://api.deepseek.com/v1"))
    qw_ok, qw_msg = check_host(cfg0.get("qwen.base_url", "https://dashscope.aliyuncs.com/api/v1"))
    add("网络连通性（DeepSeek 服务）", ds_ok, ds_msg)
    add("网络连通性（Qwen 服务）", qw_ok, qw_msg)
    return ok(results)


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------
@router.get("/diagnostics/info")
def diagnostics_info(request: Request):
    cfg = get_config()
    data_dir = get_data_dir()
    db_size = db.db_path().stat().st_size if db.db_path().exists() else 0
    disk = shutil.disk_usage(data_dir)
    last_selfcheck = db.fetch_one(
        "SELECT * FROM job ORDER BY id DESC LIMIT 1") or {}
    return ok({
        "app_version": APP_VERSION,
        "python_version": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "browser": request.headers.get("user-agent", ""),
        "port": request.url.port or 8000,
        "data_dir": str(data_dir),
        "logs_dir": str(data_dir / "logs"),
        "db_size": db_size,
        "disk_free_gb": round(disk.free / (1 << 30), 1),
        "key_status": {"deepseek": cfg.key_status("deepseek"),
                       "qwen": cfg.key_status("qwen")},
        "key_masked": {"deepseek": cfg.mask_key(cfg.get("deepseek.api_key", "")),
                       "qwen": cfg.mask_key(cfg.get("qwen.api_key", ""))},
        "log_write_broken": LOG_WRITE_BROKEN.is_set(),
    })


@router.post("/diagnostics/open-logs")
def open_logs_dir():
    logs = get_data_dir() / "logs"
    logs.mkdir(exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(logs))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(logs)])
        else:
            subprocess.Popen(["xdg-open", str(logs)])
        return ok({"opened": True})
    except OSError:
        return ok({"opened": False, "path": str(logs)})


@router.post("/diagnostics/clear-logs")
def clear_logs():
    logs = get_data_dir() / "logs"
    removed = 0
    if logs.exists():
        for p in logs.glob("*.log*"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return ok({"removed": removed})


@router.get("/diagnostics/export")
def export_diagnostics():
    data_dir = get_data_dir()
    logs = data_dir / "logs"
    cfg = get_config()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = data_dir / f"diagnostics-{stamp}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 最近 3 天日志
        for p in sorted(logs.glob("*.log"), key=lambda p: p.stat().st_mtime)[-12:]:
            zf.write(p, f"logs/{p.name}")
        crash_dir = logs / "crash"
        if crash_dir.exists():
            for p in sorted(crash_dir.glob("*.log"),
                            key=lambda p: p.stat().st_mtime)[-5:]:
                zf.write(p, f"crash/{p.name}")
        # system-info.json
        cfg0 = get_config()
        db_size0 = db.db_path().stat().st_size if db.db_path().exists() else 0
        disk0 = shutil.disk_usage(data_dir)
        info = {
            "app_version": APP_VERSION,
            "python_version": sys.version.split()[0],
            "os": f"{platform.system()} {platform.release()}",
            "arch": platform.machine(),
            "data_dir": str(data_dir),
            "logs_dir": str(logs),
            "db_size": db_size0,
            "disk_free_gb": round(disk0.free / (1 << 30), 1),
            "key_status": {"deepseek": cfg0.key_status("deepseek"),
                           "qwen": cfg0.key_status("qwen")},
        }
        zf.writestr("system-info.json",
                    json.dumps(info, ensure_ascii=False, indent=2))
        # config.redacted.json
        zf.writestr("config.redacted.json",
                    json.dumps(cfg.redacted(), ensure_ascii=False, indent=2))
        # db-summary.json
        summary = {t: db.fetch_one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
                   for t in ("document", "chunk", "design", "outline", "draft",
                             "citation", "notice", "job", "version")}
        zf.writestr("db-summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        # recent-errors.json：最近 50 条错误 job
        errors = db.fetch_all(
            "SELECT id, job_type, status, error_code, error_detail, trace_id, "
            "created_at, finished_at FROM job WHERE status='failed' "
            "ORDER BY id DESC LIMIT 50")
        zf.writestr("recent-errors.json",
                    json.dumps(errors, ensure_ascii=False, indent=2))
    return FileResponse(out_path, filename=out_path.name,
                        media_type="application/zip")


# ---------------------------------------------------------------------------
# 前端日志上报
# ---------------------------------------------------------------------------
@router.post("/clear-data")
def clear_all_data():
    """清空全部数据（§10.3）：文献、片段、设计、大纲、创作稿、任务、版本、
    原文件、向量索引、导出与日志。保留配置（API Key）与向导状态。"""
    from ..jobs import get_runner
    from ..search.vector import reset_indexes
    # 1. 取消运行中的任务
    running = db.fetch_all("SELECT id FROM job WHERE status IN ('pending','running')")
    for r in running:
        get_runner().cancel(r["id"])
    # 2. 清空业务表（先删下游，再删上游；子表随外键级联）
    with db.connect() as conn:
        for t in ("draft", "outline", "design", "document", "category", "job", "version"):
            conn.execute(f"DELETE FROM {t}")
        # 重置自增计数，避免新数据 id 与残留向量错位
        conn.execute("DELETE FROM sqlite_sequence")
        # 删除向量表（按嵌入模型分表）
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vec_%'").fetchall()
        for r in rows:
            try:
                conn.execute(f"DROP TABLE {r['name']}")
            except Exception:
                pass
        conn.execute("INSERT INTO meta(key, value) VALUES('fts_dirty', '1') "
                     "ON CONFLICT(key) DO UPDATE SET value='1'")
    reset_indexes()
    # 3. 清空文件目录（保留 data/config.json）
    data_dir = get_data_dir()
    for sub in ("files", "vectors", "exports", "logs"):
        shutil.rmtree(data_dir / sub, ignore_errors=True)
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    from ..logging_setup import log_backend
    log_backend(trace=trace_var.get(), module="system",
                msg="clear all data", docs=len(running))
    return ok({"cleared": True, "cancelled_jobs": len(running)})


@router.post("/client-logs")
async def client_logs(request: Request, body: dict[str, Any]):
    """前端日志（§11.2）：写入 frontend-*.log，凭据过滤。"""
    import logging as _logging
    logger = _logging.getLogger("frontend")
    entries = body.get("entries") or []
    if isinstance(entries, dict):
        entries = [entries]
    trace = trace_var.get() or trace_id()
    for e in entries[:100]:
        msg = redact(str(e.get("message", "")))[:500]
        logger.info(msg, extra={"structured": {
            "trace": e.get("trace_id") or trace,
            "module": "frontend",
            "route": e.get("route", ""),
            "action": e.get("action", ""),
            "level": e.get("level", "info"),
            "browser": (request.headers.get("user-agent", "") or "")[:100],
            "app_version": e.get("app_version", ""),
        }})
    return ok({"received": len(entries)})
