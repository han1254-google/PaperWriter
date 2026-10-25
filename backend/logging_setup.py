"""前后端日志（产品文档 §11.2）。

- 文件：data/logs/{backend,ai,frontend,error}-YYYY-MM-DD.log，崩溃写 data/logs/crash/。
- 格式：一行一条事件：时间戳 级别 [模块] trace=… key=value msg="…"
- 按天 + 单文件 20MB 滚动；保留 14 天、总量 500MB。
- 凭据过滤：api_key / Authorization / access_token 等统一替换为 ****。
- 日志写入失败不影响主流程：降级控制台 + 内存最近 500 条。
"""
from __future__ import annotations

import contextvars
import datetime as dt
import faulthandler
import logging
import os
import re
import sys
import threading
from collections import deque
from pathlib import Path

from .paths import ensure_dirs, get_data_dir

trace_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

_REDACT_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[=:]\s*)([^\s\"',;&]+)", re.IGNORECASE),
    re.compile(r"(authorization\s*[=:]\s*(?:bearer\s+)?)([^\s\"',;&]+)", re.IGNORECASE),
    re.compile(r"(access[_-]?token\s*[=:]\s*)([^\s\"',;&]+)", re.IGNORECASE),
    re.compile(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+"),
    re.compile(r"(DASHSCOPE_API_KEY\s*=\s*)[^\s]+", re.IGNORECASE),
]
_QUOTE_RE = re.compile(r'"')


def redact(text: str) -> str:
    for pat in _REDACT_PATTERNS:
        text = pat.sub(lambda m: f"{m.group(1)}****", text)
    return text


def _fmt_value(v: object) -> str:
    if isinstance(v, str):
        return '"' + _QUOTE_RE.sub("'", v) + '"'
    return str(v)


class _DailySizeHandler(logging.Handler):
    """按天 + 按大小（20MB）滚动的文件 handler；写入失败降级，不抛异常。"""

    def __init__(self, log_dir: Path, name: str, max_mb: int = 20) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.name = name
        self.max_bytes = max_mb * 1024 * 1024
        self._stream = None
        self._current_date = None

    def _open(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{self.name}-{dt.date.today().isoformat()}.log"
        self._stream = open(path, "a", encoding="utf-8")
        self._current_date = dt.date.today()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            now = dt.datetime.now().astimezone()
            if self._stream is None or self._current_date != now.date():
                if self._stream is not None:
                    self._stream.close()
                self._open()
            if self._stream.tell() > self.max_bytes:
                self._stream.close()
                path = self.log_dir / f"{self.name}-{now.date().isoformat()}.log"
                rolled = path.with_suffix(f".{now.strftime('%H%M%S')}.log")
                try:
                    path.replace(rolled)
                except OSError:
                    pass
                self._open()
            ts = now.isoformat(timespec="milliseconds")
            parts = [f"{ts}  {record.levelname:<5}  [{self.name:<8}]"]
            extras = getattr(record, "structured", {})
            trace = extras.get("trace") or trace_var.get()
            if trace:
                parts.append(f"trace={trace}")
            for k in ("module",):
                if extras.get(k):
                    parts.append(f"{k}={extras[k]}")
            parts.append(f"msg={_fmt_value(redact(record.getMessage()))}")
            for k, v in extras.items():
                if k in ("trace", "module"):
                    continue
                parts.append(f"{k}={_fmt_value(v)}")
            if record.exc_info:
                parts.append(f"detail={_fmt_value(redact(self.format(record)))}")
            self._stream.write(" ".join(parts) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)


class _MemoryBuffer(logging.Handler):
    """日志写入失败时的内存兜底：最近 500 条。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: deque[str] = deque(maxlen=500)

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


_buffers: dict[str, _MemoryBuffer] = {}
LOG_WRITE_BROKEN = threading.Event()


def _make_handler(log_dir: Path, name: str) -> logging.Handler:
    return _DailySizeHandler(log_dir, name)


class _Structured(logging.Filter):
    """把 extra 里的结构化字段挂到 record 上；extra 中的 key 不应与 LogRecord 属性冲突。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.structured = {}
        return True


_FORMATTER = logging.Formatter("%(message)s")


def _setup_logger(name: str, log_dir: Path, console: bool) -> logging.Logger:
    logger = logging.getLogger(name)
    if getattr(logger, "_law_paper_ready", False):
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    file_handler = _make_handler(log_dir, name)
    file_handler.setLevel(logging.INFO)
    file_handler.addFilter(_Structured())
    file_handler.setFormatter(_FORMATTER)
    logger.addHandler(file_handler)

    err_handler = _make_handler(log_dir, "error")
    err_handler.setLevel(logging.WARNING)
    err_handler.addFilter(_Structured())
    err_handler.setFormatter(_FORMATTER)
    logger.addHandler(err_handler)

    mem = _MemoryBuffer()
    mem.setLevel(logging.DEBUG)
    logger.addHandler(mem)
    _buffers[name] = mem

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(_FORMATTER)
        logger.addHandler(ch)
    logger._law_paper_ready = True  # type: ignore[attr-defined]
    return logger


def setup_logging(console: bool | None = None) -> dict[str, logging.Logger]:
    """初始化全部日志。返回 {backend, ai, frontend}。console 默认：开发模式（环境变量）打开。"""
    log_dir = ensure_dirs() / "logs"
    if console is None:
        console = bool(os.environ.get("LAW_PAPER_DEV"))
    loggers = {}
    for name in ("backend", "ai", "frontend"):
        loggers[name] = _setup_logger(name, log_dir, console)
    # 崩溃转储
    crash_dir = log_dir / "crash"
    crash_dir.mkdir(parents=True, exist_ok=True)
    crash_file = crash_dir / f"crash-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    try:
        faulthandler.enable(file=open(crash_file, "a", encoding="utf-8"), all_threads=True)
    except OSError:
        pass
    return loggers


def setup_logging_failure() -> None:
    """模拟 / 检测到日志目录不可写时：停用文件 handler，仅保留内存与控制台。"""
    LOG_WRITE_BROKEN.set()
    for name in ("backend", "ai", "frontend"):
        logger = logging.getLogger(name)
        for h in list(logger.handlers):
            if isinstance(h, _DailySizeHandler):
                h.setLevel(logging.CRITICAL + 1)  # 实际停用
                logger.removeHandler(h)


def cleanup_old_logs(log_dir: Path, keep_days: int = 14, total_mb: int = 500) -> int:
    """启动时与运行中定期调用：清理过期日志；总量超限从最旧开始删。返回删除文件数。"""
    removed = 0
    if not log_dir.exists():
        return removed
    cutoff = dt.datetime.now() - dt.timedelta(days=keep_days)
    files = sorted(log_dir.glob("*.log*"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        try:
            mtime = dt.datetime.fromtimestamp(p.stat().st_mtime)
            too_old = mtime < cutoff
            if too_old or total > total_mb * 1024 * 1024:
                p.unlink()
                removed += 1
                total -= p.stat().st_size if p.exists() else 0
        except OSError:
            continue
    return removed


def log_ai(*, trace: str | None, module: str, msg: str, **extra) -> None:
    """AI 调用摘要日志（ai-*.log）。"""
    logging.getLogger("ai").info(msg, extra={"structured": {**extra, "module": module, "trace": trace}})


def log_backend(*, trace: str | None, module: str, msg: str, level: str = "INFO", **extra) -> None:
    logger = logging.getLogger("backend")
    getattr(logger, level.lower())(msg, extra={"structured": {**extra, "module": module, "trace": trace}})
