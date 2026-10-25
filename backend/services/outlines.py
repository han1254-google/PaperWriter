"""大纲设计（产品文档 §7）：只对叶子章节生成简化写作方案。

- 一个大纲对应一个已确认的章节设计；一个设计可有多个大纲。
- 父节点只作结构容器；大纲页不修改章节结构。
- 上游设计变化 → 「同步结构」按编号匹配，已有内容不丢。
"""
from __future__ import annotations

import copy
from typing import Any

from .. import db
from ..ai.prompts import OUTLINE_PROMPT, PROMPT_VERSION, render_chunks
from ..config import get_config
from ..errors import AppError
from ..services.designs import get_tree, leaf_ids, renumber
from ..textutil import truncate_sentence

OUTLINE_LENGTH_RATIO = 0.2  # 大纲长度约为目标字数的 15%–25%


def create_outline(design_id: int, name: str | None = None) -> dict[str, Any]:
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    if design["status"] != "confirmed":
        raise AppError("UPSTREAM_NOT_READY",
                       message="章节设计尚未确认，不能创建大纲", stage="save")
    tree = renumber(get_tree(design_id))
    leaves = [n for n in tree if n["id"] in leaf_ids(tree)]
    if not leaves:
        raise AppError("UPSTREAM_NOT_READY", message="设计没有叶子章节", stage="save")
    import datetime as dt
    outline_id = db.insert("outline", {
        "design_id": design_id,
        "name": name or f"{design['title'] or '未命名'}-{dt.date.today().isoformat()}",
        "status": "active", "model": get_config().get("deepseek.model"),
        "prompt_version": PROMPT_VERSION,
        "created_at": db.now(), "updated_at": db.now()})
    for i, n in enumerate(leaves):
        db.insert("outline_chapter", {
            "outline_id": outline_id, "design_chapter_id": n["id"],
            "chapter_number": n["number"], "chapter_title": n["title"],
            "chapter_idea_snapshot": n.get("idea") or "",
            "word_budget": n["word_budget"], "status": "pending", "sort": i,
            "created_at": db.now(), "updated_at": db.now()})
    return get_outline(outline_id)


def get_outline(outline_id: int) -> dict[str, Any]:
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (outline_id,))
    if not outline:
        raise AppError("NOT_FOUND")
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (outline["design_id"],))
    design_tree = renumber(get_tree(outline["design_id"]))
    chapters = db.fetch_all(
        "SELECT * FROM outline_chapter WHERE outline_id=? ORDER BY sort, id", (outline_id,))
    by_design_chapter = {c["design_chapter_id"]: c for c in chapters
                         if c["design_chapter_id"] is not None}
    # 设计树 + 大纲内容合并（父节点为容器）
    tree = []
    for n in design_tree:
        node = {**n, "outline_chapter": by_design_chapter.get(n["id"])}
        tree.append(node)
    leaves = [n for n in tree if n["outline_chapter"]]
    generated = sum(1 for n in leaves if (n["outline_chapter"].get("content") or "").strip())
    chunks = db.fetch_all(
        "SELECT oc.outline_chapter_id, oc.chunk_id, oc.score, oc.selected, "
        "c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        "d.id AS document_id, d.title, d.authors, d.publisher, d.published_at, d.doc_type "
        "FROM outline_chunk oc JOIN chunk c ON c.id = oc.chunk_id "
        "JOIN document d ON d.id = c.document_id WHERE oc.outline_chapter_id IN "
        "(SELECT id FROM outline_chapter WHERE outline_id=?) ORDER BY oc.id", (outline_id,))
    return {**outline, "design": design, "tree": tree, "chunks": chunks,
            "progress": {"generated": generated, "total": len(leaves)}}


def list_outlines() -> list[dict[str, Any]]:
    rows = db.fetch_all(
        "SELECT o.*, d.title AS design_title, d.status AS design_status, "
        "d.total_word_count, "
        "(SELECT COUNT(*) FROM outline_chapter oc WHERE oc.outline_id=o.id) AS leaf_count "
        "FROM outline o JOIN design d ON d.id=o.design_id ORDER BY o.updated_at DESC")
    return rows


def update_outline_chapter(outline_chapter_id: int, content: str,
                           *, snapshot: bool = False) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM outline_chapter WHERE id=?", (outline_chapter_id,))
    if not row:
        raise AppError("NOT_FOUND")
    if snapshot:
        db.save_version("outline_chapter", outline_chapter_id,
                        {"content": row["content"], "saved_at": db.now()})
    status = "modified" if (row["content"] or "") != content else row["status"]
    db.update("outline_chapter", outline_chapter_id, {
        "content": content, "status": status, "updated_at": db.now()})
    return db.fetch_one("SELECT * FROM outline_chapter WHERE id=?", (outline_chapter_id,))


# ---------------------------------------------------------------------------
# 生成大纲
# ---------------------------------------------------------------------------
async def generate_outline(outline_id: int, outline_chapter_id: int, *, trace: str,
                           job_id: int, on_event) -> dict[str, Any]:
    from ..ai.clients import chat_stream
    from ..jobs import get_runner
    from ..search.hybrid import hybrid_search

    runner = get_runner()
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (outline_id,))
    oc = db.fetch_one("SELECT * FROM outline_chapter WHERE id=?", (outline_chapter_id,))
    if not outline or not oc or oc["outline_id"] != outline_id:
        raise AppError("NOT_FOUND")
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (outline["design_id"],))
    if not design:
        raise AppError("NOT_FOUND")

    tree = renumber(get_tree(design["id"]))
    tree_text = _render_tree(tree, highlight=oc["chapter_number"])

    # 1. 其他叶子已有大纲（预算内全带，超出用摘要+相邻全文，界面提示「已压缩前文」）
    others = db.fetch_all(
        "SELECT chapter_number, chapter_title, content FROM outline_chapter "
        "WHERE outline_id=? AND id!=? AND content IS NOT NULL AND content!='' "
        "ORDER BY sort", (outline_id, outline_chapter_id))
    compressed = False
    cfg = get_config()
    budget_chars = cfg.get("limits.outline_in_tokens", 40000) * 2
    total_others = sum(len(o["content"] or "") for o in others)
    if total_others <= budget_chars:
        sibling_text = "\n\n".join(
            f"【{o['chapter_number']} {o['chapter_title']}】\n{o['content']}" for o in others)
    else:
        compressed = True
        parts = []
        for o in others[:-2]:
            parts.append(f"【{o['chapter_number']} {o['chapter_title']}】摘要："
                         f"{(o['content'] or '')[:150]}")
        for o in others[-2:]:
            parts.append(f"【{o['chapter_number']} {o['chapter_title']}】\n{o['content']}")
        sibling_text = "\n\n".join(parts)
    if compressed:
        await on_event({"type": "progress", "message": "已压缩前文"})

    # 2. 片段：设计中关联到本章的 + 补充检索
    await on_event({"type": "progress", "message": "检索本节材料…"})
    linked = db.fetch_all(
        "SELECT c.id, c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        "d.title, d.authors, d.publisher, d.published_at, d.doc_type "
        "FROM design_chunk dc JOIN chunk c ON c.id=dc.chunk_id "
        "JOIN document d ON d.id=c.document_id "
        "WHERE dc.design_id=? AND dc.design_chapter_id=? AND dc.selected=1 "
        "ORDER BY dc.score DESC", (design["id"], oc["design_chapter_id"]))
    runner.check_cancel(job_id)
    # 设计树中本叶子的相邻章节关键词
    query = f"{oc['chapter_title']} {oc['chapter_idea_snapshot'] or ''}"
    extra = await hybrid_search([query], scope={"all": True}, trace=trace,
                                top_n=cfg.get("limits.outline_extra_candidates", 50))
    linked_ids = {c["id"] for c in linked}
    extra = [dict(e, id=e["chunk_id"]) for e in extra if e["chunk_id"] not in linked_ids]
    picked = (linked + extra)[: cfg.get("limits.outline_in_chunks", 25)]
    chunks_text = render_chunks(picked, max_chars=budget_chars)
    if picked:
        await on_event({"type": "retrieval", "chunks": [
            {"chunk_id": p["id"], "title": p["title"], "page_start": p.get("page_start"),
             "heading_path": p.get("heading_path"), "text_preview": (p["text"] or "")[:120]}
            for p in picked]})
    else:
        await on_event({"type": "retrieval_empty"})

    # 3. 保存本节关联片段（替换）
    with db.connect() as conn:
        conn.execute("DELETE FROM outline_chunk WHERE outline_chapter_id=?", (outline_chapter_id,))
        for p in picked:
            conn.execute(
                "INSERT INTO outline_chunk (outline_chapter_id, chunk_id, score, selected) "
                "VALUES (?,?,?,1)", (outline_chapter_id, p["id"], p.get("score")))

    # 4. 生成
    prompt = OUTLINE_PROMPT.format(
        discipline=design.get("discipline") or "法学",
        intent=truncate_sentence(design["intent"], 2000),
        title=design["title"] or "（未定）",
        overall_idea=truncate_sentence(design["overall_idea"] or "（无）", 2000),
        tree=tree_text,
        sibling_outlines=sibling_text or "（尚无其他章节大纲）",
        chunks=chunks_text or "（没有检索到相关文献材料）",
        chapter_title=oc["chapter_title"],
        chapter_idea=oc["chapter_idea_snapshot"] or "（无）",
        word_budget=oc["word_budget"] or 1000,
        cite_density="中")
    buf: list[str] = []
    async for ev in chat_stream([{"role": "user", "content": prompt}],
                                trace=trace, module="outline",
                                prompt_version=PROMPT_VERSION, temperature=0.4):
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
    db.save_version("outline_chapter", outline_chapter_id,
                    {"content": oc["content"], "saved_at": db.now()})
    db.update("outline_chapter", outline_chapter_id, {
        "content": content, "status": "generated", "updated_at": db.now()})
    return {"outline_chapter_id": outline_chapter_id, "content": content,
            "compressed": compressed}


def _render_tree(tree: list[dict[str, Any]], highlight: str | None = None) -> str:
    lines = []
    for n in tree:
        mark = "【" if n["number"] == highlight else ""
        lines.append(f"{mark}{n['number']} {n['title']}（{(n.get('idea') or '')[:60]}）{mark}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 同步结构（§6.7 / §7.4）
# ---------------------------------------------------------------------------
def sync_structure(outline_id: int) -> dict[str, Any]:
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (outline_id,))
    if not outline:
        raise AppError("NOT_FOUND")
    tree = renumber(get_tree(outline["design_id"]))
    leaves = [n for n in tree if n["id"] in leaf_ids(tree)]
    chapters = db.fetch_all(
        "SELECT * FROM outline_chapter WHERE outline_id=?", (outline_id,))
    by_dc = {c["design_chapter_id"]: c for c in chapters}
    changes = {"added": 0, "removed": 0, "renamed": 0}
    with db.connect() as conn:
        # 删除的叶子 → 标记已失效（内容保留，不参与导出）
        for c in chapters:
            if c["design_chapter_id"] not in {n["id"] for n in leaves}:
                if c["status"] != "invalid":
                    conn.execute("UPDATE outline_chapter SET status='invalid' WHERE id=?",
                                 (c["id"],))
                    changes["removed"] += 1
        # 新增叶子 → 补入；已有 → 同步标题与预算（内容不重写）
        for i, n in enumerate(leaves):
            c = by_dc.get(n["id"])
            if c:
                if (c["chapter_number"], c["chapter_title"]) != (n["number"], n["title"]):
                    conn.execute(
                        "UPDATE outline_chapter SET chapter_number=?, chapter_title=?, "
                        "word_budget=?, chapter_idea_snapshot=?, status=CASE WHEN status='invalid' "
                        "THEN 'pending' ELSE status END WHERE id=?",
                        (n["number"], n["title"], n["word_budget"], n.get("idea") or "",
                         c["id"]))
                    changes["renamed"] += 1
                else:
                    conn.execute(
                        "UPDATE outline_chapter SET word_budget=?, chapter_idea_snapshot=? "
                        "WHERE id=?", (n["word_budget"], n.get("idea") or "", c["id"]))
            else:
                conn.execute(
                    "INSERT INTO outline_chapter (outline_id, design_chapter_id, chapter_number, "
                    "chapter_title, chapter_idea_snapshot, word_budget, status, sort, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (outline_id, n["id"], n["number"], n["title"], n.get("idea") or "",
                     n["word_budget"], "pending", i, db.now(), db.now()))
                changes["added"] += 1
    db.update("outline", outline_id, {"updated_at": db.now()})
    return {"changes": changes, "outline": get_outline(outline_id)}


def delete_outline(outline_id: int) -> None:
    count = db.fetch_one("SELECT COUNT(*) AS n FROM draft WHERE outline_id=?", (outline_id,))["n"]
    if count:
        raise AppError("UPSTREAM_IN_USE",
                       message=f"该大纲有 {count} 个创作稿，请先删除创作稿", stage="save")
    db.delete("outline", outline_id)


def design_diff(design_id: int, outline_id: int) -> dict[str, Any]:
    """设计 vs 大纲的结构差异（供横幅提示）。"""
    tree = renumber(get_tree(design_id))
    leaves = {n["id"]: n for n in tree if n["id"] in leaf_ids(tree)}
    chapters = db.fetch_all("SELECT * FROM outline_chapter WHERE outline_id=? "
                            "AND design_chapter_id IS NOT NULL", (outline_id,))
    added, removed, changed = [], [], []
    for c in chapters:
        n = leaves.get(c["design_chapter_id"])
        if n is None:
            removed.append({"number": c["chapter_number"], "title": c["chapter_title"]})
        elif (c["chapter_number"], c["chapter_title"], c["word_budget"]) != \
             (n["number"], n["title"], n["word_budget"]):
            changed.append({"number": c["chapter_number"], "title": c["chapter_title"],
                            "new_number": n["number"], "new_title": n["title"]})
    for nid, n in leaves.items():
        if nid not in {c["design_chapter_id"] for c in chapters}:
            added.append({"number": n["number"], "title": n["title"]})
    return {"has_diff": bool(added or removed or changed),
            "added": added, "removed": removed, "changed": changed}
