"""文件入库管线（产品文档 §5）。

上传 → 格式/大小校验 → 去重(SHA-256) → 解析 → 元数据/摘要(模型) → 分块
     → 向量化 → FTS+向量索引 → ready

失败按文件隔离；嵌入限流指数退避（任务层重试）；删除文档同步清理
向量与 FTS；旧引用依靠 document_snapshot_json 保留。
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from .. import db
from ..config import get_config
from ..errors import AppError
from ..logging_setup import log_backend
from ..paths import get_data_dir
from ..textutil import truncate_sentence
from .chunker import chunk_document, chunk_to_row
from .parsers import parse_file

SUPPORTED_EXT = {".docx", ".doc", ".pdf"}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    return _ILLEGAL.sub("_", name).strip()[:200] or "document"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sane_title(t: str) -> bool:
    """标题合理性检查：拒绝乱码/纯符号（PDF 字体损坏时提取结果可能是点线符号）。"""
    if not t:
        return False
    cjk = sum(1 for c in t if "一" <= c <= "鿿")
    alnum = sum(1 for c in t if c.isalnum())
    return cjk + alnum >= max(2, len(t) // 2)


# ---------------------------------------------------------------------------
# 上传与去重
# ---------------------------------------------------------------------------
def save_upload(content: bytes, filename: str, *, category_id: int | None = None) -> dict[str, Any]:
    """校验并落盘。重复文件返回 {duplicate: existing_doc}，不新建记录。"""
    cfg = get_config()
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise AppError("FILE_TYPE_UNSUPPORTED", stage="upload")
    max_mb = cfg.get("limits.max_file_mb", 100)
    if len(content) > max_mb * 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", stage="upload")
    if len(content) == 0:
        raise AppError("FILE_CORRUPT", stage="upload")

    digest = hashlib.sha256(content).hexdigest()
    existing = db.fetch_one("SELECT * FROM document WHERE file_hash=?", (digest,))
    if existing:
        return {"duplicate": True, "existing": existing}

    doc_id = db.insert("document", {
        "title": Path(filename).stem[:200],
        "category_id": category_id,
        "file_name": sanitize_filename(filename),
        "file_hash": digest,
        "file_size": len(content),
        "status": "uploading",
        "created_at": db.now(), "updated_at": db.now(),
    })
    dest = _doc_path(doc_id, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    db.update("document", doc_id, {"file_path": str(dest), "status": "parsing"})
    return {"duplicate": False, "doc_id": doc_id}


def _doc_path(doc_id: int, filename: str) -> Path:
    return get_data_dir() / "files" / f"{doc_id}_{sanitize_filename(filename)}"


def overwrite_document(doc_id: int, content: bytes, filename: str) -> int:
    """覆盖已有文档（去重时的「覆盖」选项）：替换文件并重新解析。"""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise AppError("FILE_TYPE_UNSUPPORTED", stage="upload")
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND", stage="upload")
    digest = hashlib.sha256(content).hexdigest()
    _remove_doc_artifacts(doc_id)
    dest = _doc_path(doc_id, filename)
    dest.write_bytes(content)
    db.update("document", doc_id, {
        "file_name": sanitize_filename(filename), "file_path": str(dest),
        "file_hash": digest, "file_size": len(content), "status": "parsing",
        "parse_error": None, "page_count": None, "updated_at": db.now(),
    })
    return doc_id


def copy_as_new(existing_doc_id: int) -> int:
    """去重时的「另存为副本」：复制文件与元数据为新文档。"""
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (existing_doc_id,))
    if not row:
        raise AppError("NOT_FOUND", stage="upload")
    new_id = db.insert("document", {
        "title": (row["title"] or "") + "（副本）",
        "category_id": row["category_id"], "doc_type": row["doc_type"],
        "authors": row["authors"], "publisher": row["publisher"],
        "published_at": row["published_at"], "language": row["language"],
        "file_name": row["file_name"], "file_hash": row["file_hash"],
        "file_size": row["file_size"], "status": "uploading",
        "created_at": db.now(), "updated_at": db.now(),
    })
    src = Path(row["file_path"]) if row["file_path"] else None
    if src and src.exists():
        dest = _doc_path(new_id, row["file_name"] or "doc")
        shutil.copyfile(src, dest)
        db.update("document", new_id, {"file_path": str(dest), "status": "parsing"})
    return new_id


# ---------------------------------------------------------------------------
# 解析 + 索引任务
# ---------------------------------------------------------------------------
def _set_status(doc_id: int, status: str, error: str | None = None) -> None:
    data: dict[str, Any] = {"status": status, "updated_at": db.now()}
    if error is not None:
        data["parse_error"] = error
    db.update("document", doc_id, data)


async def run_parse_job(doc_id: int, job_id: int, trace: str) -> dict[str, Any]:
    """解析→分块→(模型元数据/摘要)→向量化→索引。job 内运行。"""
    from ..jobs import JobCancelled, get_runner
    runner = get_runner()
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND", stage="parse")
    path = Path(row["file_path"])
    if not path.exists():
        raise AppError("FILE_CORRUPT", message="file missing", stage="parse")

    # 1. 解析
    runner.set_progress(job_id, 5, "解析文件")
    try:
        parsed = parse_file(path)
    except AppError:
        _set_status(doc_id, "failed")
        raise
    runner.check_cancel(job_id)
    log_backend(trace=trace, module="ingest", msg="parse ok",
                file_name=row["file_name"], page_count=parsed.page_count,
                blocks=len(parsed.blocks), needs_ocr=parsed.needs_ocr)

    if parsed.needs_ocr:
        _set_status(doc_id, "needs_ocr")
        return {"needs_ocr": True}
    if not parsed.has_text:
        _set_status(doc_id, "failed", "FILE_EMPTY_PARSE")
        raise AppError("FILE_EMPTY_PARSE", stage="parse")

    runner.set_progress(job_id, 25, "生成片段")

    # 2. 分块（新片段就绪后原子替换旧索引）
    chunks = chunk_document(parsed)
    if not chunks:
        _set_status(doc_id, "failed", "FILE_EMPTY_PARSE")
        raise AppError("FILE_EMPTY_PARSE", stage="parse")
    with db.connect() as conn:
        conn.execute("DELETE FROM chunk WHERE document_id=?", (doc_id,))
        for i, c in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunk (document_id, chunk_index, chunk_type, text, token_count, "
                "page_start, page_end, para_start, para_end, heading_path) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (doc_id, i, c.chunk_type, c.text, chunk_to_row(doc_id, i, c)["token_count"],
                 c.page_start, c.page_end, c.para_start, c.para_end, c.heading_path))
    db.update("document", doc_id, {
        "page_count": parsed.page_count,
        "title": (parsed.title_hint if _sane_title(parsed.title_hint) else row["title"]),
        "status": "extracting", "updated_at": db.now(),
    })
    runner.check_cancel(job_id)

    # 3. 元数据与摘要（DeepSeek 可用时）
    runner.set_progress(job_id, 40, "抽取元数据")
    await _extract_metadata(doc_id, trace, job_id)

    # 4. 向量化
    runner.set_progress(job_id, 60, "建立索引")
    try:
        await _embed_document(doc_id, trace, job_id)
    except AppError as e:
        if e.code in ("CFG_KEY_MISSING",):
            _set_status(doc_id, "parsed")  # 无嵌入密钥：关键词检索仍可用
            return {"status": "parsed", "reason": "embedding key missing"}
        # 索引失败：标记文档失败并给出原因，避免状态卡在「抽取元数据」
        _set_status(doc_id, "failed", e.code)
        raise
    runner.check_cancel(job_id)
    _set_status(doc_id, "ready")
    return {"status": "ready", "chunks": len(chunks)}


async def _extract_metadata(doc_id: int, trace: str, job_id: int) -> None:
    from ..ai.clients import chat
    from ..ai.jsonutil import parse_json_loose
    from ..ai.prompts import METADATA_PROMPT, PROMPT_VERSION, SUMMARY_PROMPT
    from ..jobs import get_runner
    cfg = get_config()
    if not cfg.get("deepseek.api_key"):
        _set_status(doc_id, "parsed")
        return
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    # 注意：取前 6 个片段拼接（此前只取 1 个；期刊论文首页版式复杂，
    # 标题可能出现在摘要之后，需要更宽的窗口）
    head_rows = db.fetch_all(
        "SELECT text FROM chunk WHERE document_id=? AND chunk_type IN ('body','abstract') "
        "ORDER BY chunk_index LIMIT 6", (doc_id,))
    text = truncate_sentence("\n".join(r["text"] for r in head_rows), 6000)
    if not text.strip():
        return
    get_runner().check_cancel(job_id)
    try:
        content = await chat(
            [{"role": "user", "content": METADATA_PROMPT.format(text=text)}],
            trace=trace, module="metadata", prompt_version=PROMPT_VERSION,
            json_mode=True, temperature=0.1)
        data, _ = parse_json_loose(content)
        if isinstance(data, dict):
            db.update("document", doc_id, {
                "title": (data.get("title") or row["title"]).strip()[:200],
                "doc_type": data.get("doc_type"), "authors": data.get("authors"),
                "publisher": data.get("publisher"),
                "published_at": data.get("published_at"),
                "language": data.get("language"),
                "metadata_json": db_metadata_dumps(data),
                "updated_at": db.now(),
            })
            # 摘要：原文有摘要则直接用作摘要；没有才由模型概括
            if data.get("abstract"):
                db.update("document", doc_id, {
                    "summary": str(data["abstract"]).strip()[:600],
                    "updated_at": db.now()})
            else:
                try:
                    body_rows = db.fetch_all(
                        "SELECT text FROM chunk WHERE document_id=? AND chunk_type='body' "
                        "ORDER BY chunk_index LIMIT 3", (doc_id,))
                    summary_src = truncate_sentence("\n".join(r["text"] for r in body_rows), 3000)
                    if summary_src:
                        meta_str = "；".join(f"{k}: {v}" for k, v in
                                             (("标题", data.get("title")), ("作者", data.get("authors")),
                                              ("类型", data.get("doc_type")), ("出处", data.get("publisher")),
                                              ("日期", data.get("published_at"))) if v)
                        s = await chat(
                            [{"role": "user", "content": SUMMARY_PROMPT.format(
                                meta=meta_str or "未知", text=summary_src)}],
                            trace=trace, module="summary", prompt_version=PROMPT_VERSION,
                            json_mode=True, temperature=0.3)
                        sdata, _ = parse_json_loose(s)
                        if isinstance(sdata, dict) and sdata.get("summary"):
                            db.update("document", doc_id, {
                                "summary": str(sdata["summary"])[:600],
                                "updated_at": db.now()})
                except Exception:
                    log_backend(trace=trace, module="ingest", level="WARN",
                                msg="summary extraction failed")
    except Exception as e:
        # 元数据失败不阻断入库：保留文件名标题，状态仍可继续
        log_backend(trace=trace, module="ingest", level="WARN",
                    msg=f"metadata extraction failed: {e}")


def db_metadata_dumps(data: dict[str, Any]) -> str:
    import json
    meta_fields = ("journal", "volume", "issue", "pages", "doi", "school", "degree",
                   "publisher_place", "isbn", "issuing_authority", "document_no",
                   "effective_date", "court", "case_no", "cause", "url", "access_date",
                   "confidence", "evidence")
    return json.dumps({k: v for k, v in data.items() if k in meta_fields},
                      ensure_ascii=False)


async def _embed_document(doc_id: int, trace: str, job_id: int) -> None:
    from ..ai.clients import embed
    from ..jobs import get_runner
    from ..search.hybrid import fts_index_document
    from ..search.vector import get_index
    cfg = get_config()
    model = cfg.get("qwen.embedding_model", "")
    if not cfg.get("qwen.api_key") or not model:
        raise AppError("CFG_KEY_MISSING", stage="embed")

    rows = db.fetch_all(
        "SELECT id, text FROM chunk WHERE document_id=? AND chunk_type NOT IN ('reference','toc') "
        "ORDER BY chunk_index", (doc_id,))
    if not rows:
        return
    get_runner().check_cancel(job_id)
    BATCH = 20
    vectors: list[list[float]] = []
    ids: list[int] = []
    failed = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
        try:
            vecs = await embed([r["text"] for r in batch], trace=trace)
        except AppError:
            # 指数退避由 embed 内部重试承担；仍失败则整批失败并重试任务
            raise
        vectors.extend(vecs)
        ids.extend(r["id"] for r in batch)
        get_runner().set_progress(job_id, 60 + int(35 * (i + len(batch)) / len(rows)),
                                  f"向量化 {min(i + len(batch), len(rows))}/{len(rows)}")
        get_runner().check_cancel(job_id)
    idx = get_index(model)
    idx.ensure(len(vectors[0]))
    # 新索引就绪后原子替换：先删旧向量再写新
    old_ids = [r["id"] for r in db.fetch_all(
        "SELECT id FROM chunk WHERE document_id=? AND embedding_model IS NOT NULL", (doc_id,))]
    idx.delete_chunks(old_ids)
    idx.store(vectors, ids)
    with db.connect() as conn:
        conn.executemany(
            "UPDATE chunk SET embedding_model=?, embedding_dim=? WHERE id=?",
            [(model, len(vectors[0]), cid) for cid in ids])
    fts_index_document(doc_id)


# ---------------------------------------------------------------------------
# 删除与重建
# ---------------------------------------------------------------------------
def _remove_doc_artifacts(doc_id: int) -> None:
    from ..search.hybrid import fts_delete_chunks
    from ..search.vector import get_index
    rows = db.fetch_all(
        "SELECT id, embedding_model FROM chunk WHERE document_id=?", (doc_id,))
    chunk_ids = [r["id"] for r in rows]
    fts_delete_chunks(chunk_ids)
    for model in {r["embedding_model"] for r in rows if r["embedding_model"]}:
        get_index(model).delete_chunks(chunk_ids)
    with db.connect() as conn:
        conn.execute("DELETE FROM chunk WHERE document_id=?", (doc_id,))


def delete_document(doc_id: int) -> None:
    row = db.fetch_one("SELECT * FROM document WHERE id=?", (doc_id,))
    if not row:
        raise AppError("NOT_FOUND", stage="save")
    _remove_doc_artifacts(doc_id)
    # 引用保留：chunk_id 置空，依靠 document_snapshot_json
    with db.connect() as conn:
        conn.execute(
            "UPDATE citation SET chunk_id=NULL WHERE chunk_id IN "
            "(SELECT id FROM chunk WHERE document_id=?)", (doc_id,))
    db.delete("document", doc_id)
    if row["file_path"]:
        try:
            Path(row["file_path"]).unlink(missing_ok=True)
        except OSError:
            pass


def doc_referenced_by(doc_id: int) -> list[dict[str, Any]]:
    """被引用情况（§5.5）：哪些设计/大纲/创作稿引用了该文档。"""
    rows = db.fetch_all(
        "SELECT DISTINCT 'design' AS kind, ds.title AS name, ds.id AS entity_id "
        "FROM design_chunk dc JOIN design ds ON ds.id = dc.design_id "
        "JOIN chunk c ON c.id = dc.chunk_id WHERE c.document_id=? "
        "UNION ALL "
        "SELECT DISTINCT 'outline', o.name, o.id FROM outline_chunk oc "
        "JOIN outline_chapter ocp ON ocp.id = oc.outline_chapter_id "
        "JOIN outline o ON o.id = ocp.outline_id JOIN chunk c ON c.id = oc.chunk_id "
        "WHERE c.document_id=? "
        "UNION ALL "
        "SELECT DISTINCT 'draft', dr.title, dr.id FROM citation ci "
        "JOIN draft_chapter dc2 ON dc2.id = ci.draft_chapter_id "
        "JOIN draft dr ON dr.id = dc2.draft_id WHERE ci.chunk_id IN "
        "(SELECT id FROM chunk WHERE document_id=?)",
        (doc_id, doc_id, doc_id))
    return rows
