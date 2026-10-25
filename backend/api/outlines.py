"""大纲设计 API（产品文档 §7）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..errors import AppError, ok
from ..ids import trace_id
from ..logging_setup import trace_var
from ..services import outlines

router = APIRouter(prefix="/api", tags=["outlines"])


@router.get("/outlines")
def list_outlines():
    return ok(outlines.list_outlines())


@router.post("/outlines")
def create_outline(body: dict[str, Any]):
    return ok(outlines.create_outline(int(body["design_id"]),
                                      name=body.get("name")))


@router.get("/outlines/{outline_id}")
def get_outline(outline_id: int):
    return ok(outlines.get_outline(outline_id))


@router.patch("/outlines/{outline_id}")
def update_outline(outline_id: int, body: dict[str, Any]):
    from .. import db
    if "name" in body and body["name"].strip():
        db.update("outline", outline_id,
                  {"name": body["name"].strip()[:100], "updated_at": db.now()})
    return ok(outlines.get_outline(outline_id))


@router.delete("/outlines/{outline_id}")
def delete_outline(outline_id: int):
    outlines.delete_outline(outline_id)
    return ok()


@router.post("/outlines/{outline_id}/sync")
def sync_outline(outline_id: int):
    return ok(outlines.sync_structure(outline_id))


@router.get("/outlines/{outline_id}/diff")
def outline_diff(outline_id: int):
    from .. import db
    row = db.fetch_one("SELECT design_id FROM outline WHERE id=?", (outline_id,))
    if not row:
        raise AppError("NOT_FOUND")
    return ok(outlines.design_diff(row["design_id"], outline_id))


@router.put("/outlines/chapters/{chapter_id}")
def update_outline_chapter(chapter_id: int, body: dict[str, Any]):
    snapshot = not body.get("autosave", False)
    row = outlines.update_outline_chapter(chapter_id, body.get("content", ""),
                                          snapshot=snapshot)
    return ok(row)


@router.post("/outlines/chapters/{chapter_id}/generate")
async def generate_outline_chapter(chapter_id: int, body: dict[str, Any] | None = None):
    from .. import db
    from ..api.stream import on_event_factory
    from ..jobs import get_runner
    row = db.fetch_one("SELECT * FROM outline_chapter WHERE id=?", (chapter_id,))
    if not row:
        raise AppError("NOT_FOUND")
    if get_runner().has_running("outline_chapter", chapter_id):
        raise AppError("JOB_ALREADY_RUNNING", stage="generate")
    trace = trace_var.get() or trace_id()

    async def run(job_id: int):
        on_event = await on_event_factory(job_id)
        try:
            result = await outlines.generate_outline(
                row["outline_id"], chapter_id, trace=trace, job_id=job_id,
                on_event=on_event)
            await on_event({"type": "result", "chapter_id": chapter_id})
            await on_event({"type": "done", "message": "大纲已生成"})
            return result
        except AppError as e:
            await on_event({"type": "error", **e.to_dict()})
            raise

    job_id, started = get_runner().start(
        "outline_generate", "outline_chapter", chapter_id, trace, run)
    return ok({"job_id": job_id, "started": started})
