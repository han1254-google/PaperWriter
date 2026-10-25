"""文件入库 API（产品文档 §5）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..errors import AppError, ok
from ..ids import trace_id
from ..ingest import pipeline
from ..logging_setup import trace_var
from ..paths import get_data_dir

router = APIRouter(prefix="/api", tags=["files"])


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------
@router.get("/categories")
def list_categories():
    rows = db.fetch_all("SELECT * FROM category ORDER BY sort, id")
    return ok(rows)


@router.post("/categories")
def create_category(body: dict[str, Any]):
    name = (body.get("name") or "").strip()
    if not name or len(name) > 50:
        raise AppError("VALIDATION_ERROR", message="分类名需 1–50 字", http_status=422)
    if body.get("parent_id"):
        parent = db.fetch_one("SELECT * FROM category WHERE id=?", (body["parent_id"],))
        if not parent:
            raise AppError("NOT_FOUND")
    cid = db.insert("category", {"parent_id": body.get("parent_id"), "name": name,
                                 "sort": 0, "created_at": db.now(), "updated_at": db.now()})
    return ok(db.fetch_one("SELECT * FROM category WHERE id=?", (cid,)))


@router.patch("/categories/{cid}")
def rename_category(cid: int, body: dict[str, Any]):
    name = (body.get("name") or "").strip()
    if not name:
        raise AppError("VALIDATION_ERROR", message="分类名不能为空", http_status=422)
    db.update("category", cid, {"name": name[:50], "updated_at": db.now()})
    return ok(db.fetch_one("SELECT * FROM category WHERE id=?", (cid,)))


@router.delete("/categories/{cid}")
def delete_category(cid: int):
    count = db.fetch_one("SELECT COUNT(*) AS n FROM document WHERE category_id=?", (cid,))["n"]
    if count:
        raise AppError("UPSTREAM_IN_USE",
                       message=f"分类下还有 {count} 篇文献，请先迁移或删除", stage="save")
    db.delete("category", cid)
    return ok()


# ---------------------------------------------------------------------------
# 文献
# ---------------------------------------------------------------------------
@router.get("/documents")
def list_documents(q: str = "", category_id: int | None = None,
                   doc_type: str = "", status: str = "",
                   sort: str = "updated_at_desc", limit: int = 200):
    sql = "SELECT * FROM document WHERE 1=1"
    params: list[Any] = []
    if q:
        sql += " AND (title LIKE ? OR authors LIKE ? OR summary LIKE ?)"
        params += [f"%{q}%"] * 3
    if category_id:
        sql += " AND category_id=?"
        params.append(category_id)
    if doc_type:
        sql += " AND doc_type=?"
        params.append(doc_type)
    if status:
        sql += " AND status=?"
        params.append(status)
    order = {"updated_at_desc": "updated_at DESC", "title": "title",
             "created_at_desc": "created_at DESC"}.get(sort, "updated_at DESC")
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    rows = db.fetch_all(sql, tuple(params))
    for r in rows:
        r["metadata"] = db.get_json_col(r, "metadata_json")
        r["metadata_json"] = None
    return ok(rows)


@router.get("/documents/{doc_id}")
def get_document(doc_id: int):
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND")
    row["metadata"] = db.get_json_col(row, "metadata_json")
    row["metadata_json"] = None
    row["chunk_count"] = db.fetch_one(
        "SELECT COUNT(*) AS n FROM chunk WHERE document_id=?", (doc_id,))["n"]
    row["referenced_by"] = pipeline.doc_referenced_by(doc_id)
    return ok(row)


@router.patch("/documents/{doc_id}")
def update_document(doc_id: int, body: dict[str, Any]):
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND")
    simple = ("title", "doc_type", "authors", "publisher", "published_at", "language",
              "summary", "category_id")
    data = {"updated_at": db.now()}
    for k in simple:
        if k in body:
            v = body[k]
            data[k] = v.strip() if isinstance(v, str) else v
    if "metadata" in body and isinstance(body["metadata"], dict):
        data["metadata_json"] = json.dumps(body["metadata"], ensure_ascii=False)
    db.update("document", doc_id, data)
    return get_document(doc_id)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int):
    pipeline.delete_document(doc_id)
    return ok()


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int):
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row or not row["file_path"]:
        raise AppError("NOT_FOUND")
    p = Path(row["file_path"])
    if not p.exists():
        raise AppError("NOT_FOUND", message="原文件已不存在")
    return FileResponse(p, filename=row["file_name"] or p.name)


@router.get("/documents/{doc_id}/chunks")
def list_chunks(doc_id: int):
    rows = db.fetch_all(
        "SELECT * FROM chunk WHERE document_id=? ORDER BY chunk_index", (doc_id,))
    return ok(rows)


@router.post("/documents/{doc_id}/reparse")
async def reparse_document(doc_id: int):
    from ..jobs import get_runner
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND")
    if get_runner().has_running("document", doc_id):
        raise AppError("JOB_ALREADY_RUNNING", stage="parse")
    trace = trace_var.get() or trace_id()
    job_id, started = get_runner().start(
        "parse", "document", doc_id, trace,
        lambda jid: pipeline.run_parse_job(doc_id, jid, trace))
    return ok({"job_id": job_id, "started": started})


@router.post("/documents/upload")
async def upload_documents(request: Request, files: list[UploadFile] = File(...),
                           category_id: int | None = None,
                           on_duplicate: str = Query("skip")):
    """批量上传并自动开始解析。on_duplicate: skip|overwrite|copy"""
    from ..jobs import get_runner
    max_batch = 20
    if len(files) > max_batch:
        raise AppError("VALIDATION_ERROR",
                       message=f"单次最多上传 {max_batch} 个文件", http_status=422)
    trace = trace_var.get() or trace_id()
    results = []
    runner = get_runner()
    for f in files:
        name = f.filename or "unnamed"
        try:
            content = await f.read()
            res = pipeline.save_upload(content, name, category_id=category_id)
            if res.get("duplicate"):
                existing = res["existing"]
                action = on_duplicate
                if action == "overwrite":
                    doc_id = pipeline.overwrite_document(existing["id"], content, name)
                    res = {"duplicate": False, "doc_id": doc_id, "overwrote": existing["id"]}
                elif action == "copy":
                    doc_id = pipeline.copy_as_new(existing["id"])
                    res = {"duplicate": False, "doc_id": doc_id, "copied_from": existing["id"]}
                else:
                    results.append({"file": name, "duplicate": True,
                                    "existing_doc": existing["id"],
                                    "existing_title": existing["title"]})
                    continue
            doc_id = res["doc_id"]
            job_id, started = runner.start(
                "parse", "document", doc_id, trace,
                lambda jid, d=doc_id: pipeline.run_parse_job(d, jid, trace))
            results.append({"file": name, "duplicate": False, "doc_id": doc_id,
                            "job_id": job_id, "started": started})
        except AppError as e:
            results.append({"file": name, "error": e.to_dict()})
    return ok(results)


@router.post("/documents/{doc_id}/category-suggest")
async def suggest_category(doc_id: int):
    """模型给出 1–3 个分类建议（用户确认后生效）。"""
    from ..ai.clients import chat
    from ..ai.jsonutil import parse_json_loose
    from ..ai.prompts import CATEGORY_PROMPT, PROMPT_VERSION
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND")
    cats = db.fetch_all("SELECT * FROM category ORDER BY sort, id")
    existing = "、".join(c["name"] for c in cats) or "（暂无分类）"
    trace = trace_var.get() or trace_id()
    content = await chat([{"role": "user", "content": CATEGORY_PROMPT.format(
        existing=existing, title=row["title"], doc_type=row["doc_type"] or "未知",
        authors=row["authors"] or "未知")}],
        trace=trace, module="category", prompt_version=PROMPT_VERSION,
        json_mode=True, temperature=0.2)
    data, _ = parse_json_loose(content)
    if not isinstance(data, dict):
        raise AppError("AI_BAD_JSON", stage="parse", trace=trace)
    return ok(data)
