"""章节设计 API（产品文档 §6）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import db
from ..errors import AppError, ok
from ..ids import trace_id
from ..logging_setup import trace_var
from ..services import designs

router = APIRouter(prefix="/api", tags=["designs"])


@router.get("/designs")
def list_designs():
    return ok(designs.list_designs())


@router.post("/designs")
def create_design(body: dict[str, Any]):
    return ok(designs.create_design(
        body.get("intent", ""),
        total_word_count=int(body.get("total_word_count") or 0),
        title=body.get("title"), description=body.get("description"),
        discipline=body.get("discipline", "法学")))


@router.get("/designs/{design_id}")
def get_design(design_id: int):
    return ok(designs.get_design(design_id))


@router.put("/designs/{design_id}")
def update_design(design_id: int, body: dict[str, Any]):
    """保存（autosave=true 不建版本快照）。tree/chunk_links 全量替换。"""
    fields = {k: v for k, v in body.items()
              if k in ("title", "intent", "description", "overall_idea",
                       "total_word_count", "discipline", "tree", "chunk_links")}
    snapshot = not body.get("autosave", False)
    return ok(designs.update_design(design_id, fields, snapshot=snapshot))


@router.delete("/designs/{design_id}")
def delete_design(design_id: int):
    designs.delete_design(design_id)
    return ok()


@router.post("/designs/{design_id}/duplicate")
def duplicate_design(design_id: int):
    return ok(designs.duplicate_design(design_id))


@router.post("/designs/{design_id}/generate")
async def generate_design(design_id: int, body: dict[str, Any] | None = None):
    from ..api.stream import on_event_factory
    from ..jobs import get_runner
    body = body or {}
    if get_runner().has_running("design", design_id):
        raise AppError("JOB_ALREADY_RUNNING", stage="generate")
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    trace = trace_var.get() or trace_id()

    async def run(job_id: int):
        on_event = await on_event_factory(job_id)
        try:
            result = await designs.generate_design(
                design_id, trace=trace, job_id=job_id, on_event=on_event,
                scope=body.get("scope"))
            await on_event({"type": "result", "design_id": design_id})
            await on_event({"type": "done", "message": "章节设计已生成"})
            return result
        except AppError as e:
            await on_event({"type": "error", **e.to_dict()})
            raise

    job_id, started = get_runner().start("design_generate", "design", design_id, trace, run)
    return ok({"job_id": job_id, "started": started})


@router.post("/designs/{design_id}/conflict-check")
async def conflict_check(design_id: int):
    trace = trace_var.get() or trace_id()
    report = await designs.conflict_check(design_id, trace=trace)
    return ok(report)


@router.post("/designs/{design_id}/confirm")
def confirm_design(design_id: int, body: dict[str, Any] | None = None):
    body = body or {}
    return ok(designs.confirm_design(
        design_id, force=bool(body.get("force")),
        force_reason=body.get("force_reason", "")))


@router.get("/designs/{design_id}/chunks")
def design_chunks(design_id: int):
    rows = db.fetch_all(
        "SELECT dc.design_chapter_id, dc.chunk_id, dc.score, dc.selected, "
        "c.text, c.page_start, c.page_end, c.heading_path, d.title, d.authors "
        "FROM design_chunk dc JOIN chunk c ON c.id=dc.chunk_id "
        "JOIN document d ON d.id=c.document_id WHERE dc.design_id=? ORDER BY dc.id",
        (design_id,))
    return ok(rows)
