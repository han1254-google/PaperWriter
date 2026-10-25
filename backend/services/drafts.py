"""章节创作（产品文档 §8 / §9）：正文生成、引用标记、提示块、保存。"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db
from ..ai.prompts import DRAFT_PROMPT, PROMPT_VERSION, render_chunks
from ..config import get_config
from ..errors import AppError
from ..textutil import truncate_sentence, truncate_tail

MARKER_RE = re.compile(r"\[(\d+(?:\.\d+)*)-(\d+)\]")
NOTICE_RE = re.compile(r'<NOTICE\s+type="(\w+)">(.*?)</NOTICE>', re.DOTALL | re.IGNORECASE)
NOTICE_TYPES = {"verify", "cite", "case", "data", "image", "transition"}

CJK_RE = re.compile(r"[一-鿿]")


def count_words(text: str) -> int:
    """字数统计：CJK 一字一计 + 英文按词计。"""
    if not text:
        return 0
    cjk = len(CJK_RE.findall(text))
    ascii_words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + ascii_words


# ---------------------------------------------------------------------------
# 创建 / 查询
# ---------------------------------------------------------------------------
def create_draft(outline_id: int, title: str | None = None) -> dict[str, Any]:
    from ..services.outlines import get_outline
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (outline_id,))
    if not outline:
        raise AppError("NOT_FOUND")
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (outline["design_id"],))
    chapters = db.fetch_all(
        "SELECT * FROM outline_chapter WHERE outline_id=? ORDER BY sort, id", (outline_id,))
    active = [c for c in chapters if c["status"] != "invalid"]
    if not active:
        raise AppError("UPSTREAM_NOT_READY", message="大纲没有可用章节", stage="save")
    if not any((c["content"] or "").strip() for c in active):
        raise AppError("UPSTREAM_NOT_READY",
                       message="至少有一个叶子章节已生成大纲后才能创建创作稿", stage="save")
    import datetime as dt
    draft_id = db.insert("draft", {
        "outline_id": outline_id,
        "title": title or (design["title"] or "未命名论文"),
        "target_word_count": design["total_word_count"],
        "status": "editing",
        "citation_format": "gb7714",
        "created_at": db.now(), "updated_at": db.now()})
    for i, c in enumerate(active):
        db.insert("draft_chapter", {
            "draft_id": draft_id, "outline_chapter_id": c["id"],
            "target_word_count": c["word_budget"], "status": "pending", "sort": i,
            "created_at": db.now(), "updated_at": db.now()})
    return get_draft(draft_id)


def get_draft(draft_id: int) -> dict[str, Any]:
    draft = db.fetch_one("SELECT * FROM draft WHERE id=?", (draft_id,))
    if not draft:
        raise AppError("NOT_FOUND")
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (draft["outline_id"],))
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (outline["design_id"],))
    design_tree = get_design_tree(design["id"])
    chapters = db.fetch_all(
        "SELECT dc.*, oc.chapter_number, oc.chapter_title, oc.content AS outline_content, "
        "oc.status AS outline_status "
        "FROM draft_chapter dc JOIN outline_chapter oc ON oc.id = dc.outline_chapter_id "
        "WHERE dc.draft_id=? ORDER BY dc.sort, dc.id", (draft_id,))
    tree = []
    for n in design_tree:
        dc = next((c for c in chapters if c["chapter_number"] == n["number"]), None)
        tree.append({**n, "draft_chapter": dc, "is_leaf": n["id"] in
                     {c2["outline_chapter_id"] for c2 in chapters}})
    citations = db.fetch_all(
        "SELECT ci.*, d.title AS doc_title, d.authors AS doc_authors, d.publisher AS doc_publisher, "
        "d.published_at AS doc_published_at, d.doc_type AS doc_doc_type, d.id AS doc_id "
        "FROM citation ci LEFT JOIN chunk c ON c.id = ci.chunk_id "
        "LEFT JOIN document d ON d.id = c.document_id "
        "WHERE ci.draft_chapter_id IN (SELECT id FROM draft_chapter WHERE draft_id=?) "
        "ORDER BY ci.id", (draft_id,))
    notices = db.fetch_all(
        "SELECT * FROM notice WHERE draft_chapter_id IN "
        "(SELECT id FROM draft_chapter WHERE draft_id=?) ORDER BY id", (draft_id,))
    # 各章片段（参考内容面板）
    chunks = db.fetch_all(
        "SELECT oc.outline_chapter_id, oc.chunk_id, oc.score, "
        "c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        "d.id AS document_id, d.title, d.authors, d.publisher, d.published_at, d.doc_type "
        "FROM outline_chunk oc JOIN chunk c ON c.id = oc.chunk_id "
        "JOIN document d ON d.id = c.document_id "
        "WHERE oc.outline_chapter_id IN (SELECT outline_chapter_id FROM draft_chapter "
        "WHERE draft_id=?) ORDER BY oc.id", (draft_id,))
    stats = _draft_stats(chapters)
    return {**draft, "outline": outline, "design": design, "tree": tree,
            "chapters": chapters, "citations": citations, "notices": notices,
            "chunks": chunks, "stats": stats}


def get_design_tree(design_id: int) -> list[dict[str, Any]]:
    from ..services.designs import get_tree, renumber
    return renumber(get_tree(design_id))


def _draft_stats(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    actual = sum(c["word_count"] or 0 for c in chapters)
    done = sum(1 for c in chapters if c["status"] == "done")
    generated = sum(1 for c in chapters if (c["content"] or "").strip())
    draft_ids = {c["draft_id"] for c in chapters}
    invalid_cites = 0
    if draft_ids:
        invalid_cites = db.fetch_one(
            f"SELECT COUNT(*) AS n FROM citation WHERE valid=0 AND draft_chapter_id IN "
            f"(SELECT id FROM draft_chapter WHERE draft_id IN "
            f"({','.join('?' * len(draft_ids))}))", tuple(draft_ids))["n"]
    return {"actual_words": actual, "done": done, "generated": generated,
            "total": len(chapters), "invalid_citations": invalid_cites}


def update_draft_info(draft_id: int, fields: dict[str, Any], *, snapshot: bool = False) -> dict[str, Any]:
    draft = db.fetch_one("SELECT * FROM draft WHERE id=?", (draft_id,))
    if not draft:
        raise AppError("NOT_FOUND")
    if snapshot:
        db.save_version("draft", draft_id, dict(draft))
    data = {"updated_at": db.now()}
    for k in ("title", "abstract", "keywords", "author", "school", "publish_date"):
        if k in fields:
            data[k] = (fields[k] or "").strip() or None
    db.update("draft", draft_id, data)
    return get_draft(draft_id)


# ---------------------------------------------------------------------------
# 正文生成
# ---------------------------------------------------------------------------
async def generate_draft_chapter(draft_id: int, draft_chapter_id: int, *, trace: str,
                                 job_id: int, on_event, target_words: int | None = None,
                                 cite_density: str = "中", tone: str = "学术严谨",
                                 prev_context: str = "") -> dict[str, Any]:
    from ..ai.clients import chat_stream
    from ..jobs import get_runner
    from ..search.hybrid import hybrid_search

    runner = get_runner()
    draft = db.fetch_one("SELECT * FROM draft WHERE id=?", (draft_id,))
    dc = db.fetch_one("SELECT * FROM draft_chapter WHERE id=?", (draft_chapter_id,))
    if not draft or not dc or dc["draft_id"] != draft_id:
        raise AppError("NOT_FOUND")
    oc = db.fetch_one("SELECT * FROM outline_chapter WHERE id=?", (dc["outline_chapter_id"],))
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (draft["outline_id"],))
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (outline["design_id"],))
    if not (oc["content"] or "").strip():
        raise AppError("UPSTREAM_NOT_READY", message="请先生成本节大纲", stage="generate")

    tgt = target_words or dc["target_word_count"] or oc["word_budget"] or 1000
    if target_words and dc["target_word_count"] and \
       abs(target_words - dc["target_word_count"]) > dc["target_word_count"] * 0.3:
        await on_event({"type": "warning", "message":
                        f"目标字数 {target_words} 与章节预算 {dc['target_word_count']} 偏差超过 30%"})
    if tgt > 6000:
        await on_event({"type": "warning", "message": "本章目标超过 6000 字，建议缩短目标"})

    tree = get_design_tree(design["id"])
    tree_text = _render_tree_with_outline(tree, draft_id, highlight=oc["chapter_number"])

    # 前文衔接：上一叶子结尾 500 字，更早章节摘要
    if not prev_context:
        prev_context = _prev_context(draft_id, dc["sort"])
    runner.check_cancel(job_id)

    # 片段：大纲关联 + 补充检索
    await on_event({"type": "progress", "message": "检索本节材料…"})
    cfg = get_config()
    linked = db.fetch_all(
        "SELECT c.id, c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        "d.id AS document_id, d.title, d.authors, d.publisher, d.published_at, d.doc_type "
        "FROM outline_chunk ock JOIN chunk c ON c.id = ock.chunk_id "
        "JOIN document d ON d.id = c.document_id "
        "WHERE ock.outline_chapter_id=? AND ock.selected=1 ORDER BY ock.score DESC",
        (dc["outline_chapter_id"],))
    extra = await hybrid_search(
        [f"{oc['chapter_title']} {truncate_sentence(oc['content'] or '', 500)}"],
        scope={"all": True}, trace=trace,
        top_n=cfg.get("limits.draft_extra_candidates", 50))
    linked_ids = {c["id"] for c in linked}
    extra = [dict(e, id=e["chunk_id"]) for e in extra if e["chunk_id"] not in linked_ids]
    picked = (linked + extra)[: cfg.get("limits.draft_in_chunks", 30)]
    chunks_text = render_chunks(picked, max_chars=cfg.get("limits.draft_in_tokens", 60000) * 2)
    if picked:
        await on_event({"type": "retrieval", "chunks": [
            {"chunk_id": p["id"], "title": p["title"],
             "page_start": p.get("page_start"), "heading_path": p.get("heading_path"),
             "text_preview": (p["text"] or "")[:120]} for p in picked]})
    else:
        await on_event({"type": "retrieval_empty"})

    prompt = DRAFT_PROMPT.format(
        paper_title=draft["title"] or "（未定）",
        author=draft["author"] or "（未填）",
        intent=truncate_sentence(design["intent"], 1500),
        overall_idea=truncate_sentence(design["overall_idea"] or "（无）", 1500),
        tree=tree_text,
        outline_content=truncate_sentence(oc["content"], 3000),
        chapter_idea=oc["chapter_idea_snapshot"] or "（无）",
        chunks=chunks_text or "（没有检索到相关文献材料，请多用 <NOTICE> 提示补充）",
        chapter_number=oc["chapter_number"],
        target_words=tgt,
        cite_density=cite_density,
        tone=tone,
        prev_tail=prev_context or "（无前文）")

    buf: list[str] = []
    async for ev in chat_stream([{"role": "user", "content": prompt}],
                                trace=trace, module="draft",
                                prompt_version=PROMPT_VERSION, temperature=0.5):
        runner.check_cancel(job_id)
        if ev["type"] == "delta":
            buf.append(ev["text"])
            await on_event({"type": "delta", "text": ev["text"]})
        elif ev["type"] == "error":
            raise AppError(ev["code"], detail=ev.get("detail"), stage="generate",
                           trace=trace, retryable=ev.get("retryable", False))
    content = "".join(buf).strip()
    if not content:
        raise AppError("AI_EMPTY_OUTPUT", stage="generate", trace=trace)

    # 旧内容保留到新内容成功生成
    db.save_version("draft_chapter", draft_chapter_id,
                    {"content": dc["content"], "saved_at": db.now()})

    # 保存并校验引用
    await on_event({"type": "progress", "message": "校验引用与提示块…"})
    db.update("draft_chapter", draft_chapter_id, {
        "content": content, "word_count": count_words(content),
        "status": "generated", "updated_at": db.now()})
    validation = validate_chapter_citations(draft_chapter_id, content, picked,
                                            oc["chapter_number"])
    return {"draft_chapter_id": draft_chapter_id, "content": content,
            "validation": validation}


def _render_tree_with_outline(tree: list[dict[str, Any]], draft_id: int,
                              highlight: str | None = None) -> str:
    chapters = db.fetch_all(
        "SELECT dc.*, oc.chapter_number FROM draft_chapter dc "
        "JOIN outline_chapter oc ON oc.id=dc.outline_chapter_id WHERE dc.draft_id=?",
        (draft_id,))
    by_number = {c["chapter_number"]: c for c in chapters}
    lines = []
    for n in tree:
        mark = "【" if n["number"] == highlight else ""
        c = by_number.get(n["number"])
        extra = ""
        if c and (c["content"] or "").strip():
            extra = "（已生成）" if c["status"] == "done" else "（草稿）"
        lines.append(f"{mark}{n['number']} {n['title']}{extra}{mark}")
    return "\n".join(lines)


def _prev_context(draft_id: int, sort: int) -> str:
    chapters = db.fetch_all(
        "SELECT dc.*, oc.chapter_number, oc.chapter_title FROM draft_chapter dc "
        "JOIN outline_chapter oc ON oc.id=dc.outline_chapter_id "
        "WHERE dc.draft_id=? AND dc.sort<? AND dc.content IS NOT NULL AND dc.content!='' "
        "ORDER BY dc.sort", (draft_id, sort))
    if not chapters:
        return ""
    prev = chapters[-1]
    prev_tail = truncate_tail(prev["content"] or "", 500)
    earlier = ""
    for c in chapters[:-1]:
        earlier += f"[{c['chapter_number']} {c['chapter_title']}要点] " + \
                   truncate_sentence(c["content"] or "", 120) + "\n"
    return f"{earlier}……\n（上一节结尾）{prev_tail}"


# ---------------------------------------------------------------------------
# 引用与提示块（§9）
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _lcs_len(a: str, b: str, max_len: int = 60) -> int:
    """最长公共子串长度（滚动数组）。"""
    if len(a) > len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
                    if best >= max_len:
                        return best
        prev = cur
    return best


def _find_best_chunk(sentence: str, chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """把含引用的句子匹配到上下文片段。

    两级匹配：① 最长公共子串 ≥ 10 字（强信号）；② 5-gram 重叠率 ≥ 0.3。
    返回得分最高的片段。
    """
    sent = _norm(sentence)
    best, best_score = None, 0.0
    for c in chunks:
        text = _norm(c["text"])
        if not text or not sent or len(sent) < 12:
            continue
        lcs = _lcs_len(sent, text)
        if lcs >= 10:
            score = 1.0 + lcs / 100  # 强匹配优先
        else:
            grams_s = {sent[i:i + 5] for i in range(len(sent) - 4)}
            grams_t = {text[i:i + 5] for i in range(len(text) - 4)}
            inter = len(grams_s & grams_t)
            if not grams_s:
                continue
            score = inter / len(grams_s)
            if score < 0.3:
                continue
        if score > best_score:
            best, best_score = c, score
    return best


def validate_chapter_citations(draft_chapter_id: int, content: str,
                               context_chunks: list[dict[str, Any]],
                               chapter_number: str) -> dict[str, Any]:
    """解析引用标记 → 建引用记录 → 校验映射与引文（§9.2）。"""
    with db.connect() as conn:
        conn.execute("DELETE FROM citation WHERE draft_chapter_id=?", (draft_chapter_id,))
        conn.execute("DELETE FROM notice WHERE draft_chapter_id=?", (draft_chapter_id,))

    # 提示块
    for m in NOTICE_RE.finditer(content):
        ntype = m.group(1).lower() if m.group(1).lower() in NOTICE_TYPES else "verify"
        db.insert("notice", {
            "draft_chapter_id": draft_chapter_id, "notice_type": ntype,
            "content": m.group(2).strip()[:500], "status": "open",
            "created_at": db.now(), "updated_at": db.now()})

    # 引用标记
    seen: dict[str, int] = {}
    stats = {"citations": 0, "invalid": 0, "unverified_quote": 0, "notices": 0}
    # 归一化片段键名（design_chunk 查询用 id，检索结果用 chunk_id）
    context_chunks = [{**c, "id": c.get("id", c.get("chunk_id"))} for c in context_chunks]
    for m in MARKER_RE.finditer(content):
        ch_num, seq = m.group(1), int(m.group(2))
        marker = f"[{ch_num}-{seq}]"
        if ch_num != chapter_number:
            _insert_citation(draft_chapter_id, marker, seq, None, None, content,
                             f"章节号 {ch_num} 与当前章节 {chapter_number} 不符")
            stats["invalid"] += 1
            stats["citations"] += 1
            continue
        if marker in seen:
            continue  # 同章重复引用复用同一序号
        start = max(0, m.start() - 80)
        sentence = content[start:m.end() + 30]
        chunk = _find_best_chunk(sentence, context_chunks)
        if chunk is None:
            _insert_citation(draft_chapter_id, marker, seq, None, sentence, content,
                             "无法映射到本次检索的片段")
            stats["invalid"] += 1
        else:
            doc = db.fetch_one("SELECT * FROM document WHERE id=?", (chunk["document_id"],))
            _insert_citation(draft_chapter_id, marker, seq, chunk, sentence, content, None,
                             doc)
        seen[marker] = seq
        stats["citations"] += 1

    stats["notices"] = db.fetch_one(
        "SELECT COUNT(*) AS n FROM notice WHERE draft_chapter_id=?", (draft_chapter_id,))["n"]
    return stats


def _insert_citation(draft_chapter_id: int, marker: str, order_no: int,
                     chunk: dict[str, Any] | None, quote: str, content: str,
                     error: str | None, doc: dict[str, Any] | None = None) -> None:
    snapshot = None
    if doc:
        snapshot = json.dumps({
            "title": doc["title"], "authors": doc["authors"],
            "publisher": doc["publisher"], "published_at": doc["published_at"],
            "doc_type": doc["doc_type"], "document_id": doc["id"],
            "metadata": json.loads(doc["metadata_json"]) if doc.get("metadata_json") else None,
        }, ensure_ascii=False)
    db.insert("citation", {
        "draft_chapter_id": draft_chapter_id, "chunk_id": chunk["id"] if chunk else None,
        "marker": marker, "order_no": order_no,
        "quote": (quote or "")[:500],
        "page_start": chunk.get("page_start") if chunk else None,
        "page_end": chunk.get("page_end") if chunk else None,
        "document_snapshot_json": snapshot,
        "valid": 0 if error else 1,
        "created_at": db.now(), "updated_at": db.now(),
    })


# ---------------------------------------------------------------------------
# 保存 / 完成
# ---------------------------------------------------------------------------
def save_draft_chapter(draft_chapter_id: int, content: str, *, snapshot: bool = False) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM draft_chapter WHERE id=?", (draft_chapter_id,))
    if not row:
        raise AppError("NOT_FOUND")
    if snapshot:
        db.save_version("draft_chapter", draft_chapter_id,
                        {"content": row["content"], "saved_at": db.now()})
    new_status = row["status"]
    if row["status"] == "done":
        new_status = "modified"
    elif row["content"] != content:
        new_status = "modified" if row["status"] == "generated" else row["status"]
    db.update("draft_chapter", draft_chapter_id, {
        "content": content, "word_count": count_words(content),
        "status": new_status, "updated_at": db.now()})
    return db.fetch_one("SELECT * FROM draft_chapter WHERE id=?", (draft_chapter_id,))


def mark_chapter_done(draft_chapter_id: int, *, force: bool = False) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM draft_chapter WHERE id=?", (draft_chapter_id,))
    if not row:
        raise AppError("NOT_FOUND")
    invalid = db.fetch_one(
        "SELECT COUNT(*) AS n FROM citation WHERE draft_chapter_id=? AND valid=0",
        (draft_chapter_id,))["n"]
    if invalid and not force:
        raise AppError("VALIDATION_ERROR",
                       message=f"存在 {invalid} 条无效引用，请处理后重试或确认完成",
                       http_status=409)
    db.update("draft_chapter", draft_chapter_id,
              {"status": "done", "updated_at": db.now()})
    return db.fetch_one("SELECT * FROM draft_chapter WHERE id=?", (draft_chapter_id,))


def update_notice(notice_id: int, *, status: str | None = None,
                  content: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"updated_at": db.now()}
    if status in ("open", "resolved"):
        data["status"] = status
    if content is not None:
        data["content"] = content[:500]
    db.update("notice", notice_id, data)
    return db.fetch_one("SELECT * FROM notice WHERE id=?", (notice_id,))


def fix_citation(citation_id: int, *, chunk_id: int | None) -> dict[str, Any]:
    """把无效引用手动映射到片段（标红 → 有效）。"""
    row = db.fetch_one("SELECT * FROM citation WHERE id=?", (citation_id,))
    if not row:
        raise AppError("NOT_FOUND")
    chunk = db.fetch_one("SELECT c.*, d.title, d.authors, d.publisher, d.published_at, "
                         "d.doc_type, d.id AS document_id FROM chunk c "
                         "JOIN document d ON d.id=c.document_id WHERE c.id=?",
                         (chunk_id,)) if chunk_id else None
    data: dict[str, Any] = {"valid": 1 if chunk else row["valid"],
                            "chunk_id": chunk_id, "updated_at": db.now()}
    if chunk:
        data["page_start"] = chunk["page_start"]
        data["page_end"] = chunk["page_end"]
        snapshot = json.dumps({
            "title": chunk["title"], "authors": chunk["authors"],
            "publisher": chunk["publisher"], "published_at": chunk["published_at"],
            "doc_type": chunk["doc_type"], "document_id": chunk["document_id"],
        }, ensure_ascii=False)
        data["document_snapshot_json"] = snapshot
    db.update("citation", citation_id, data)
    return db.fetch_one("SELECT * FROM citation WHERE id=?", (citation_id,))


def delete_draft(draft_id: int) -> None:
    db.delete("draft", draft_id)


def list_drafts() -> list[dict[str, Any]]:
    rows = db.fetch_all(
        "SELECT d.*, o.name AS outline_name, ds.title AS design_title, "
        "(SELECT COUNT(*) FROM draft_chapter dc WHERE dc.draft_id=d.id) AS leaf_count, "
        "(SELECT COUNT(*) FROM draft_chapter dc WHERE dc.draft_id=d.id AND dc.status='done') "
        "AS done_count, "
        "(SELECT COALESCE(SUM(word_count),0) FROM draft_chapter dc WHERE dc.draft_id=d.id) "
        "AS actual_words "
        "FROM draft d JOIN outline o ON o.id=d.outline_id "
        "JOIN design ds ON ds.id=o.design_id ORDER BY d.updated_at DESC")
    return rows
