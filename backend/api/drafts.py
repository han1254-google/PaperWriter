"""章节创作 API（产品文档 §8 / §9）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .. import db
from ..errors import AppError, ok
from ..ids import trace_id
from ..logging_setup import trace_var
from ..services import drafts, export

router = APIRouter(prefix="/api", tags=["drafts"])


@router.get("/drafts")
def list_drafts():
    return ok(drafts.list_drafts())


@router.post("/drafts")
def create_draft(body: dict[str, Any]):
    return ok(drafts.create_draft(int(body["outline_id"]), title=body.get("title")))


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int):
    return ok(drafts.get_draft(draft_id))


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: int, body: dict[str, Any]):
    snapshot = not body.get("autosave", False)
    return ok(drafts.update_draft_info(draft_id, body, snapshot=snapshot))


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int):
    drafts.delete_draft(draft_id)
    return ok()


@router.put("/drafts/chapters/{chapter_id}")
def update_draft_chapter(chapter_id: int, body: dict[str, Any]):
    snapshot = not body.get("autosave", False)
    return ok(drafts.save_draft_chapter(chapter_id, body.get("content", ""),
                                        snapshot=snapshot))


@router.post("/drafts/chapters/{chapter_id}/complete")
def complete_chapter(chapter_id: int, body: dict[str, Any] | None = None):
    return ok(drafts.mark_chapter_done(chapter_id,
                                       force=bool((body or {}).get("force", False))))


@router.post("/drafts/chapters/{chapter_id}/generate")
async def generate_draft_chapter(chapter_id: int, body: dict[str, Any] | None = None):
    from ..api.stream import on_event_factory
    from ..jobs import get_runner
    body = body or {}
    row = db.fetch_one("SELECT * FROM draft_chapter WHERE id=?", (chapter_id,))
    if not row:
        raise AppError("NOT_FOUND")
    if get_runner().has_running("draft_chapter", chapter_id):
        raise AppError("JOB_ALREADY_RUNNING", stage="generate")
    trace = trace_var.get() or trace_id()

    async def run(job_id: int):
        on_event = await on_event_factory(job_id)
        try:
            result = await drafts.generate_draft_chapter(
                row["draft_id"], chapter_id, trace=trace, job_id=job_id,
                on_event=on_event,
                target_words=body.get("target_words"),
                cite_density=body.get("cite_density", "中"),
                tone=body.get("tone", "学术严谨"))
            await on_event({"type": "result", "chapter_id": chapter_id,
                            "validation": result.get("validation")})
            await on_event({"type": "done", "message": "正文已生成"})
            return result
        except AppError as e:
            await on_event({"type": "error", **e.to_dict()})
            raise

    job_id, started = get_runner().start(
        "draft_generate", "draft_chapter", chapter_id, trace, run)
    return ok({"job_id": job_id, "started": started})


# ---- 引用与提示块 ----
@router.patch("/notices/{notice_id}")
def update_notice(notice_id: int, body: dict[str, Any]):
    return ok(drafts.update_notice(notice_id, status=body.get("status"),
                                   content=body.get("content")))


@router.post("/citations/{citation_id}/fix")
def fix_citation(citation_id: int, body: dict[str, Any]):
    return ok(drafts.fix_citation(citation_id, chunk_id=body.get("chunk_id")))


# ---- 导出 ----
@router.get("/drafts/{draft_id}/export-checks")
def export_checks(draft_id: int):
    return ok(export.export_checks(draft_id))


@router.get("/drafts/{draft_id}/export")
def export_draft(draft_id: int, mode: str = "final",
                 citation_format: str = "gb7714",
                 include_todo: bool = False):
    from ..logging_setup import log_backend
    if mode not in ("final", "work"):
        raise AppError("VALIDATION_ERROR", message="导出模式仅支持 final/work",
                       http_status=422)
    if mode == "final":
        checks = export.export_checks(draft_id)
        if checks["invalid_citations"] > 0:
            raise AppError("EXPORT_INVALID_CITATION", stage="export",
                           message=f"{checks['invalid_citations']} 条无效引用")
    path = export.export_to_file(draft_id, mode=mode, citation_format=citation_format,
                                 include_todo=include_todo)
    trace = trace_var.get() or trace_id()
    log_backend(trace=trace, module="export", msg="export ok",
                format=mode, path=path.name)
    return FileResponse(path, filename=path.name,
                        media_type="text/markdown; charset=utf-8")
