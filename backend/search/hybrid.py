"""混合检索（产品文档 §6.3）：向量 + FTS5 关键词，RRF 融合。

- 每条查询分别做向量与关键词检索，RRF 融合排序。
- 候选池上限 100；低于阈值丢弃；近似重复合并；同一文档最多 5 条。
- 默认排除 reference / toc 类型片段。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from .. import db
from ..config import get_config
from ..logging_setup import log_backend
from .vector import get_index

EXCLUDED_TYPES = ("reference", "toc")

_RRF_K = 60


# ---------------------------------------------------------------------------
# FTS 同步
#
# 注意：SQLite 3.53.x 的 FTS5 external-content 'delete' 命令存在损坏 bug，
# 因此统一用 'rebuild'（从内容表全量重建）代替逐行删除。
# 写入侧只标记脏位，检索前惰性重建，避免频繁全量扫描。
# ---------------------------------------------------------------------------
def fts_mark_dirty() -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES('fts_dirty', '1') "
                     "ON CONFLICT(key) DO UPDATE SET value='1'")


def fts_rebuild(conn) -> None:
    conn.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO meta(key, value) VALUES('fts_dirty', '0') "
                 "ON CONFLICT(key) DO UPDATE SET value='0'")


def fts_ensure_fresh() -> None:
    """检索前调用：索引脏时重建。"""
    dirty = db.fetch_one("SELECT value FROM meta WHERE key='fts_dirty'")
    if dirty and dirty["value"] == "1":
        with db.connect() as conn:
            fts_rebuild(conn)


def fts_index_document(doc_id: int) -> None:
    """解析完成、片段落库后调用（标记重建）。"""
    fts_mark_dirty()


def fts_delete_chunks(chunk_ids: list[int]) -> None:
    """片段删除后调用（标记重建）。"""
    if not chunk_ids:
        return
    fts_mark_dirty()


_FTS_SPECIAL = re.compile(r'["*:^()]')


def fts_search(query: str, limit: int = 50) -> list[tuple[int, float]]:
    """关键词检索，返回 [(chunk_id, bm25_score)]（分数越低越相关）。"""
    q = _FTS_SPECIAL.sub(" ", query).strip()
    if len(q) < 2:
        return []
    fts_ensure_fresh()
    with db.connect() as conn:
        try:
            rows = conn.execute(
                "SELECT rowid, bm25(chunk_fts) AS score FROM chunk_fts "
                "WHERE chunk_fts MATCH ? ORDER BY score LIMIT ?",
                (q, limit)).fetchall()
        except Exception:
            return []
        return [(r["rowid"], float(r["score"])) for r in rows if r["rowid"]]


# ---------------------------------------------------------------------------
# 检索主流程
# ---------------------------------------------------------------------------
async def embed_queries(queries: list[str], trace: str) -> dict[str, list[list[float]]]:
    """为每个查询生成向量。按库中使用的嵌入模型分别调用。"""
    from ..ai.clients import embed
    models = {r["embedding_model"] for r in
              db.fetch_all("SELECT DISTINCT embedding_model FROM chunk "
                           "WHERE embedding_model IS NOT NULL")}
    cfg_model = get_config().get("qwen.embedding_model", "")
    if cfg_model:
        models.add(cfg_model)
    out: dict[str, list[list[float]]] = {}
    for model in models:
        if not model:
            continue
        try:
            out[model] = await embed(queries, trace=trace)
        except Exception:
            log_backend(trace=trace, module="search", level="WARN",
                        msg=f"embed failed, fallback to keyword-only model={model}")
    return out


def _jaccard(a: str, b: str) -> float:
    sa = set(a[i : i + 5] for i in range(0, max(1, len(a) - 4), 5))
    sb = set(b[i : i + 5] for i in range(0, max(1, len(b) - 4), 5))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _dedupe(entries: list[dict[str, Any]], texts: dict[int, str]) -> list[dict[str, Any]]:
    """近似重复合并：保留得分高者。"""
    out: list[dict[str, Any]] = []
    for e in entries:
        dup = False
        for kept in out:
            a, b = texts.get(e["chunk_id"], ""), texts.get(kept["chunk_id"], "")
            if a and b and _jaccard(a, b) > 0.85:
                dup = True
                break
        if not dup:
            out.append(e)
    return out


async def hybrid_search(queries: list[str], *, scope: dict[str, Any] | None = None,
                        trace: str | None = None, top_n: int | None = None,
                        job_id: int | None = None) -> list[dict[str, Any]]:
    """混合检索。

    scope: {all: true} | {category_id} | {document_ids: [...]} | {chunk_ids: [...]}
    返回：[{chunk_id, score, query, matched_by}…]，按融合分降序。
    """
    cfg = get_config()
    pool_size = top_n or cfg.get("limits.candidate_pool_size", 100)
    per_doc_max = cfg.get("limits.per_doc_max_chunks", 5)

    # 范围 → 允许的 chunk ids（None 表示全部）
    allowed_ids = _scope_chunk_ids(scope)

    # 查询向量
    queries = [q for q in queries if q and q.strip()]
    vecs_by_model = await embed_queries(queries, trace or "")

    # 每条查询：向量 + 关键词 → RRF 融合
    rrf: dict[int, float] = {}
    matched_by: dict[int, str] = {}
    from ..jobs import JobCancelled
    for qi, query in enumerate(queries):
        ranked: list[list[int]] = []
        # 向量（按模型各自检索，旧索引继续可用）
        for model, vecs in vecs_by_model.items():
            idx = get_index(model)
            idx.discover()
            if idx.dim is None:
                continue
            hits = idx.query(vecs[qi], k=30, filter_chunk_ids=allowed_ids)
            ranked.append([cid for cid, _ in hits])
        # 关键词
        kw = fts_search(query, limit=30)
        if allowed_ids is not None:
            allowed = set(allowed_ids)
            kw = [(cid, s) for cid, s in kw if cid in allowed]
        ranked.append([cid for cid, _ in kw])
        for rl in ranked:
            for rank, cid in enumerate(rl):
                rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
                matched_by[cid] = matched_by.get(cid, "") + f"q{qi + 1} "

    if not rrf:
        return []

    ids = list(rrf.keys())
    id_rows = db.fetch_all(
        f"SELECT id, text, document_id FROM chunk WHERE id IN ({','.join('?' * len(ids))})",
        tuple(ids))
    texts = {r["id"]: r["text"] for r in id_rows}
    doc_of = {r["id"]: r["document_id"] for r in id_rows}

    entries = [{"chunk_id": cid, "score": round(rrf[cid], 6),
                "matched_by": matched_by[cid].strip()} for cid in ids]
    entries.sort(key=lambda e: -e["score"])

    # 阈值过滤：去掉与最高分差距过大的（相对阈值）
    if entries:
        top_score = entries[0]["score"]
        entries = [e for e in entries if e["score"] >= top_score * 0.35]

    # 近似重复合并 + 每文档最多 5 条
    entries = _dedupe(entries, texts)
    kept: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    for e in entries:
        doc_id = doc_of.get(e["chunk_id"])
        if doc_id is None:
            continue
        if per_doc.get(doc_id, 0) >= per_doc_max:
            continue
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        kept.append(e)
        if len(kept) >= pool_size:
            break

    if not kept:
        return []

    # 补全片段与文档信息
    cids = [e["chunk_id"] for e in kept]
    rows = db.fetch_all(
        f"SELECT c.id AS chunk_id, c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        f"c.para_start, c.para_end, d.id AS document_id, d.title, d.authors, d.publisher, "
        f"d.published_at, d.doc_type, d.page_count "
        f"FROM chunk c JOIN document d ON d.id = c.document_id "
        f"WHERE c.id IN ({','.join('?' * len(cids))})", tuple(cids))
    by_id = {r["chunk_id"]: r for r in rows}
    out = []
    for e in kept:
        r = by_id.get(e["chunk_id"])
        if not r:
            continue
        out.append({**dict(r), "score": e["score"], "matched_by": e["matched_by"]})
    return out


def _scope_chunk_ids(scope: dict[str, Any] | None) -> list[int] | None:
    """检索范围 → 允许的 chunk id 列表；None = 全部。"""
    if not scope or scope.get("all"):
        return None
    if scope.get("chunk_ids"):
        return scope["chunk_ids"]
    sql = "SELECT c.id FROM chunk c JOIN document d ON d.id = c.document_id WHERE 1=1"
    params: list[Any] = []
    if scope.get("category_id"):
        sql += " AND (d.category_id = ?)"
        params.append(scope["category_id"])
    if scope.get("document_ids"):
        sql += f" AND d.id IN ({','.join('?' * len(scope['document_ids']))})"
        params.extend(scope["document_ids"])
    if "category_id" not in scope and "document_ids" not in scope:
        return None
    return [r["id"] for r in db.fetch_all(sql, tuple(params))]


# ---------------------------------------------------------------------------
# 查询扩展（§6.3.2）
# ---------------------------------------------------------------------------
async def expand_queries(intent: str, extra: str, trace: str) -> list[str]:
    from ..ai.clients import chat
    from ..ai.jsonutil import parse_json_loose
    from ..ai.prompts import PROMPT_VERSION, QUERY_EXPANSION_PROMPT
    from ..errors import AppError

    content = await chat(
        [{"role": "user", "content": QUERY_EXPANSION_PROMPT.format(
            intent=intent[:2000], extra=(extra or "无")[:2000])}],
        trace=trace, module="query_expansion", prompt_version=PROMPT_VERSION,
        json_mode=True, temperature=0.3)
    data, repair = parse_json_loose(content)
    if not isinstance(data, dict):
        raise AppError("AI_BAD_JSON", stage="retrieve", trace=trace)
    queries = [q for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
    return queries[:5] or [intent[:100]]


async def retrieval_pipeline(intent: str, *, title: str = "", description: str = "",
                             discipline: str = "", scope: dict[str, Any] | None = None,
                             trace: str | None = None, job_id: int | None = None,
                             extra_queries: list[str] | None = None,
                             limit: int | None = None,
                             out_limit: int | None = None) -> list[dict[str, Any]]:
    """章节设计入口的检索主流程：查询构造 → 扩展 → 混合检索。"""
    from ..jobs import JobCancelled
    base = " ".join(x for x in [intent, title, description, discipline] if x)
    queries = extra_queries or []
    try:
        queries += await expand_queries(base, "", trace or "")
    except Exception:
        log_backend(trace=trace, module="search", level="WARN", msg="query expansion failed")
        queries.append(intent[:200])
    results = await hybrid_search(queries, scope=scope, trace=trace,
                                  top_n=limit, job_id=job_id)
    return results[: out_limit or limit or 100]
