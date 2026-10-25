"""异步任务管理（产品文档 §4.2 job 表 / §11.3）。

- 任务持久化到 job 表；重启后 running → failed(JOB_INTERRUPTED)，可查看与重试。
- 同一 (entity_type, entity_id) 同一时间只允许一个任务。
- 生成类任务支持取消；取消后旧内容保留。
"""
from __future__ import annotations

import asyncio
import contextlib
import traceback
from typing import Any, Awaitable, Callable

from . import db
from .errors import AppError
from .ids import trace_id as new_trace_id
from .logging_setup import log_backend

CoroutineFactory = Callable[[int], Awaitable[Any]]


class JobError(Exception):
    def __init__(self, code: str, message: str = "", retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(code)


class JobCancelled(Exception):
    pass


class JobRunner:
    """单进程 asyncio 任务运行器。"""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._entity_locks: dict[tuple[str, int], asyncio.Task] = {}
        self._cancel_flags: dict[int, asyncio.Event] = {}

    # ---- 启动恢复 ----
    def recover_interrupted(self) -> int:
        """重启后把 running 任务标记为失败。返回恢复数量。"""
        with db.connect() as conn:
            rows = conn.execute("SELECT id FROM job WHERE status IN ('pending','running')").fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE job SET status='failed', error_code='JOB_INTERRUPTED', "
                    "finished_at=? WHERE id=?", (db.now(), r["id"]))
        return len(rows)

    # ---- 任务生命周期 ----
    def start(self, job_type: str, entity_type: str | None, entity_id: int | None,
              trace: str | None, factory: CoroutineFactory) -> tuple[int, bool]:
        """创建并启动任务。返回 (job_id, started)。

        若同一实体已有任务在跑，不启动新任务并返回 (0, False)。
        """
        key = (entity_type or job_type, entity_id or 0)
        if key in self._entity_locks and not self._entity_locks[key].done():
            return 0, False
        job_id = db.insert("job", {
            "job_type": job_type, "entity_type": entity_type, "entity_id": entity_id,
            "status": "running", "progress": 0, "trace_id": trace or new_trace_id(),
            "attempt": 0, "started_at": db.now(), "created_at": db.now(), "updated_at": db.now(),
        })
        cancel_evt = asyncio.Event()
        self._cancel_flags[job_id] = cancel_evt
        task = asyncio.create_task(self._run(job_id, key, factory, cancel_evt))
        # 等待任务注册完成，确保同一实体并发保护生效
        self._tasks[job_id] = task
        self._entity_locks[key] = task
        log_backend(trace=trace, module="jobs",
                    msg=f"job started job_id={job_id} job_type={job_type}")
        return job_id, True

    async def _run(self, job_id: int, key: tuple[str, int], factory: CoroutineFactory,
                   cancel_evt: asyncio.Event) -> None:
        trace = db.fetch_one("SELECT trace_id FROM job WHERE id=?", (job_id,))
        trace = trace["trace_id"] if trace else None
        try:
            result = await factory(job_id)
            self._finish(job_id, "succeeded", result=result)
        except JobCancelled:
            self._finish(job_id, "cancelled", code="JOB_CANCELLED")
        except JobError as e:
            self._finish(job_id, "failed", code=e.code, detail=e.message)
        except AppError as e:
            self._finish(job_id, "failed", code=e.code, detail=str(e.detail or e.message))
        except asyncio.CancelledError:
            self._finish(job_id, "cancelled", code="JOB_CANCELLED")
        except Exception as e:  # noqa: BLE001 — 任务兜底
            tb = traceback.format_exc(limit=10)
            log_backend(trace=trace, module="jobs", level="ERROR",
                        msg=f"job crashed job_id={job_id}", detail=tb[:2000])
            self._finish(job_id, "failed", code="SYS_UNCAUGHT", detail=tb[:2000])
        finally:
            self._tasks.pop(job_id, None)
            self._cancel_flags.pop(job_id, None)
            if self._entity_locks.get(key) is not None and self._entity_locks[key].done():
                self._entity_locks.pop(key, None)

    def _finish(self, job_id: int, status: str, *, code: str | None = None,
                detail: str | None = None, result: Any = None) -> None:
        data = {"status": status, "progress": 100 if status == "succeeded" else None,
                "finished_at": db.now(), "updated_at": db.now()}
        if code:
            data["error_code"] = code
        if detail:
            data["error_detail"] = (detail or "")[:2000]
        if result is not None:
            try:
                import json as _json
                data["error_detail"] = _json.dumps(result, ensure_ascii=False)[:2000]
            except Exception:
                pass
        try:
            db.update("job", job_id, {k: v for k, v in data.items() if v is not None})
        except Exception:
            pass

    # ---- 进度 / 取消 ----
    def set_progress(self, job_id: int, progress: float, note: str | None = None) -> None:
        data: dict[str, Any] = {"progress": round(min(progress, 99), 1), "updated_at": db.now()}
        if note:
            data["error_detail"] = note[:2000]
        db.update("job", job_id, data)

    def cancel(self, job_id: int) -> bool:
        evt = self._cancel_flags.get(job_id)
        task = self._tasks.get(job_id)
        if evt is not None:
            evt.set()
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def is_cancelled(self, job_id: int) -> bool:
        return self._cancel_flags.get(job_id, asyncio.Event()).is_set()

    def check_cancel(self, job_id: int) -> None:
        if self.is_cancelled(job_id):
            raise JobCancelled()

    def has_running(self, entity_type: str | None, entity_id: int | None) -> bool:
        key = (entity_type or "", entity_id or 0)
        task = self._entity_locks.get(key)
        return task is not None and not task.done()

    def running_job_for(self, entity_type: str | None, entity_id: int | None) -> dict | None:
        rows = db.fetch_all(
            "SELECT * FROM job WHERE entity_type=? AND entity_id=? AND status IN ('pending','running') "
            "ORDER BY id DESC LIMIT 1", (entity_type, entity_id))
        return rows[0] if rows else None


_runner: JobRunner | None = None


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner


@contextlib.contextmanager
def job_guard(job_id: int):
    """任务内部使用：自动检查取消并更新进度。"""
    runner = get_runner()
    yield
