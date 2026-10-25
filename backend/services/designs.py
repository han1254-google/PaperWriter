"""章节设计（产品文档 §6）：树结构、重编号、字数预算、生成、冲突检查。"""
from __future__ import annotations

import copy
from typing import Any

from .. import db
from ..ai.jsonutil import parse_json_loose
from ..ai.prompts import CONFLICT_PROMPT, DESIGN_PROMPT, PROMPT_VERSION, render_chunks
from ..config import get_config
from ..errors import AppError
from ..logging_setup import log_backend
from ..textutil import truncate_sentence

MAX_DEPTH = 4
MAX_NODES = 200
BUDGET_TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# 章节树
# ---------------------------------------------------------------------------
def get_tree(design_id: int) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        "SELECT * FROM design_chapter WHERE design_id=? ORDER BY parent_id, sort, id",
        (design_id,))
    return rows


def renumber(tree: list[dict[str, Any]], max_depth: int = MAX_DEPTH) -> list[dict[str, Any]]:
    """深度优先全量重编号（§4.3）。输入树带 parent_id/sort，输出带 number 与 depth。"""
    children: dict[int | None, list[dict[str, Any]]] = {}
    for node in tree:
        children.setdefault(node["parent_id"], []).append(node)
    for lst in children.values():
        lst.sort(key=lambda n: (n["sort"], n["id"]))
    result: list[dict[str, Any]] = []

    def walk(parent_id: int | None, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        for i, node in enumerate(children.get(parent_id, []), 1):
            number = f"{prefix}.{i}" if prefix else str(i)
            node["number"] = number
            node["depth"] = depth
            result.append(node)
            walk(node["id"], number, depth + 1)

    walk(None, "", 1)
    return result


def recalc_parents(tree: list[dict[str, Any]]) -> dict[int, int]:
    """父章节预算 = 子章节之和。返回 {chapter_id: budget}。"""
    budgets: dict[int, int] = {}
    # 叶子预算
    child_ids: set[int] = set()
    for n in tree:
        if n["parent_id"] is not None:
            child_ids.add(n["parent_id"])
        budgets[n["id"]] = int(n["word_budget"] or 0)
    changed = True
    while changed:
        changed = False
        for n in tree:
            if n["id"] in child_ids:
                s = sum(budgets.get(c["id"], 0) for c in tree if c["parent_id"] == n["id"])
                if budgets.get(n["id"]) != s:
                    budgets[n["id"]] = s
                    changed = True
    return budgets


def save_tree(design_id: int, tree: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[Any, int]]:
    """保存章节树（全量替换）并重编号。

    tree: [{id(可为临时 id), parent_id, title, idea, word_budget, sort}]
    返回 (renumbered_tree, {临时 id → 新数据库 id})。
    """
    if len(tree) > MAX_NODES:
        raise AppError("VALIDATION_ERROR", message=f"章节节点超过上限 {MAX_NODES}",
                       stage="save", http_status=422)
    if any(not n.get("title", "").strip() for n in tree):
        raise AppError("VALIDATION_ERROR", message="章节标题不能为空", stage="save",
                       http_status=422)
    with db.connect() as conn:
        conn.execute("DELETE FROM design_chapter WHERE design_id=?", (design_id,))
        id_map: dict[Any, int] = {}
        for i, n in enumerate(tree):
            parent = id_map.get(n.get("parent_id"))
            new_id = conn.execute(
                "INSERT INTO design_chapter (design_id, parent_id, number, title, idea, "
                "word_budget, sort, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (design_id, parent, "", n["title"].strip()[:100],
                 (n.get("idea") or "").strip()[:1000], int(n.get("word_budget") or 0),
                 i, db.now(), db.now())).lastrowid
            id_map[n.get("id")] = new_id
        conn.execute(
            "UPDATE design_chunk SET design_chapter_id=NULL WHERE design_id=? "
            "AND design_chapter_id NOT IN (SELECT id FROM design_chapter WHERE design_id=?)",
            (design_id, design_id))
    tree = get_tree(design_id)
    return renumber(tree), id_map


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------
def leaf_ids(tree: list[dict[str, Any]]) -> list[int]:
    parents = {n["parent_id"] for n in tree if n["parent_id"] is not None}
    return [n["id"] for n in tree if n["id"] not in parents]


def budget_summary(tree: list[dict[str, Any]], total: int) -> dict[str, Any]:
    leaves = leaf_ids(tree)
    allocated = sum(int(n["word_budget"] or 0) for n in tree if n["id"] in leaves)
    diff = total - allocated
    return {"total": total, "allocated": allocated, "diff": diff,
            "within_tolerance": abs(diff) <= total * BUDGET_TOLERANCE,
            "leaf_count": len(leaves)}


def normalize_budgets(tree: list[dict[str, Any]], total: int) -> None:
    """叶子预算按比例缩放，使之和等于总目标。"""
    leaves = leaf_ids(tree)
    if not leaves:
        return
    current = sum(int(n["word_budget"] or 0) for n in tree if n["id"] in leaves)
    if current <= 0:
        per = total // len(leaves)
        rest = total - per * len(leaves)
        for i, n in enumerate(tree):
            if n["id"] in leaves:
                n["word_budget"] = per + (rest if i == 0 else 0)
        return
    ratio = total / current
    allocated = 0
    for i, n in enumerate(tree):
        if n["id"] in leaves:
            v = int(round(int(n["word_budget"] or 0) * ratio))
            if i == len(leaves) - 1:  # 最后一片补齐差额
                pass
            n["word_budget"] = v
            allocated += v
    # 补齐四舍五入差额到第一片叶子
    first = next(n for n in tree if n["id"] in leaves)
    first["word_budget"] += total - allocated


def average_budgets(tree: list[dict[str, Any]], total: int) -> None:
    leaves = leaf_ids(tree)
    if not leaves:
        return
    per = total // len(leaves)
    rest = total - per * len(leaves)
    for i, n in enumerate(tree):
        if n["id"] in leaves:
            n["word_budget"] = per + (rest if i == 0 else 0)


# ---------------------------------------------------------------------------
# 生成（JSON 输出 → 树）
# ---------------------------------------------------------------------------
def _chunks_for_prompt(design_id: int, limit_tokens: int) -> tuple[list[dict[str, Any]], str]:
    cfg = get_config()
    rows = db.fetch_all(
        "SELECT c.id, c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        "d.title, d.authors, d.publisher, d.published_at, d.doc_type "
        "FROM chunk c JOIN document d ON d.id = c.document_id "
        "WHERE c.id IN (SELECT chunk_id FROM design_chunk WHERE design_id=? AND selected=1) "
        "ORDER BY c.id", (design_id,))
    return rows, render_chunks(rows, max_chars=limit_tokens * 2)


async def generate_design(design_id: int, *, trace: str, job_id: int,
                          on_event, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成章节设计。on_event(dict) 推送流式事件。"""
    from ..ai.clients import chat_stream
    from ..jobs import JobCancelled, get_runner
    from ..search.hybrid import retrieval_pipeline

    runner = get_runner()
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND", stage="generate")

    # 1. 检索
    await on_event({"type": "progress", "message": "检索相关文献…"})
    results = await retrieval_pipeline(
        design["intent"], title=design["title"] or "", description=design["description"] or "",
        scope=scope, trace=trace)
    runner.check_cancel(job_id)
    if results:
        await on_event({"type": "retrieval", "chunks": _chunk_briefs(results)})
    elif not results:
        await on_event({"type": "retrieval_empty"})

    # 2. 保存设计级片段关联（未分配区）
    with db.connect() as conn:
        conn.execute("DELETE FROM design_chunk WHERE design_id=?", (design_id,))
        for r in results:
            conn.execute(
                "INSERT INTO design_chunk (design_id, design_chapter_id, chunk_id, score, query, "
                "selected) VALUES (?, NULL, ?, ?, '', 1)",
                (design_id, r["chunk_id"], r["score"]))

    # 3. 提示词与流式生成
    cfg = get_config()
    prompt = DESIGN_PROMPT.format(
        intent=truncate_sentence(design["intent"], 3000),
        title=design["title"] or "（未填写，请给出建议）",
        description=truncate_sentence(design["description"] or "（无）", 3000),
        total_words=design["total_word_count"],
        depth=3, discipline="法学论文", language="中文",
        chunks=render_chunks(results, max_chars=cfg.get("limits.design_in_tokens", 60000) * 2))
    buf: list[str] = []
    async for ev in chat_stream(
            [{"role": "user", "content": prompt}],
            trace=trace, module="design", prompt_version=PROMPT_VERSION,
            temperature=0.4, json_mode=True):
        runner.check_cancel(job_id)
        if ev["type"] == "delta":
            buf.append(ev["text"])
            await on_event({"type": "delta", "text": ev["text"]})
        elif ev["type"] == "error":
            raise AppError(ev["code"], detail=ev.get("detail"), stage="generate",
                           trace=trace, retryable=ev.get("retryable", False))
    raw = "".join(buf)
    await on_event({"type": "parsing", "message": "解析章节结构…"})

    # 4. JSON 解析（失败自动修复一次）
    data, repair = parse_json_loose(raw)
    if not isinstance(data, dict):
        await on_event({"type": "parse_failed", "raw": raw})
        raise AppError("AI_BAD_JSON", stage="generate", trace=trace)

    # 5. 校验与落库
    chapters = data.get("chapters") or []
    if not chapters:
        raise AppError("AI_EMPTY_OUTPUT", stage="generate", trace=trace)
    tree, model_links = _flatten_model_chapters(chapters)
    total = int(design["total_word_count"])
    normalize_budgets(tree, total)
    tree, id_map = save_tree(design_id, tree)
    recalc = recalc_parents(tree)
    with db.connect() as conn:
        for cid, b in recalc.items():
            conn.execute("UPDATE design_chapter SET word_budget=? WHERE id=?", (b, cid))

    # 6. 章节-片段关联（chunk_ids 必须来自检索结果，不得编造）
    valid_ids = {r["chunk_id"] for r in results}
    with db.connect() as conn:
        for temp_id, cids in model_links.items():
            real_id = id_map.get(temp_id)
            if real_id is None:
                continue
            for cid in cids:
                if cid in valid_ids:
                    conn.execute(
                        "UPDATE design_chunk SET design_chapter_id=? WHERE design_id=? AND chunk_id=?",
                        (real_id, design_id, cid))

    overall = str(data.get("overall_idea") or "")[:2000]
    title = str(data.get("title_suggestion") or design["title"] or "")[:100]
    db.update("design", design_id, {
        "overall_idea": overall,
        "title": title or design["title"],
        "model": get_config().get("deepseek.model"),
        "prompt_version": PROMPT_VERSION,
        "updated_at": db.now(),
    })
    return {"design": get_design(design_id), "repaired": bool(repair)}


def _chunk_briefs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"chunk_id": r["chunk_id"], "title": r["title"], "authors": r["authors"],
             "page_start": r["page_start"], "page_end": r["page_end"],
             "heading_path": r["heading_path"], "score": r["score"],
             "text_preview": (r["text"] or "")[:120]}
            for r in results]


def _flatten_model_chapters(chapters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, list[int]]]:
    """模型输出的嵌套 chapters → 扁平树。临时 id 用负数自增。

    返回 (树列表, {临时 id → model chunk_ids})。
    """
    tree: list[dict[str, Any]] = []
    links: dict[int, list[int]] = {}
    counter = [-1]

    def walk(items: list[dict[str, Any]], parent_id: int | None) -> None:
        for c in items:
            tid = counter[0]
            counter[0] -= 1
            node = {"id": tid, "parent_id": parent_id,
                    "title": str(c.get("title") or "").strip()[:100],
                    "idea": str(c.get("idea") or "").strip()[:1000],
                    "word_budget": int(c.get("word_budget") or 0)}
            ids = [int(x) for x in (c.get("chunk_ids") or [])
                   if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]
            if ids:
                links[tid] = ids
            tree.append(node)
            walk(c.get("children") or [], tid)

    walk(chapters, None)
    return tree, links


def get_design(design_id: int) -> dict[str, Any]:
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    tree = get_tree(design_id)
    numbered = renumber(tree)
    outline_count = db.fetch_one(
        "SELECT COUNT(*) AS n FROM outline WHERE design_id=?", (design_id,))["n"]
    chunks = db.fetch_all(
        "SELECT dc.id AS link_id, dc.design_chapter_id, dc.chunk_id, dc.score, dc.selected, "
        "c.text, c.chunk_type, c.heading_path, c.page_start, c.page_end, "
        "d.id AS document_id, d.title, d.authors, d.publisher, d.published_at, d.doc_type "
        "FROM design_chunk dc JOIN chunk c ON c.id = dc.chunk_id "
        "JOIN document d ON d.id = c.document_id WHERE dc.design_id=? ORDER BY dc.id",
        (design_id,))
    conflict = db.get_json_col(design, "conflict_report_json")
    return {**design, "chapters": numbered, "chunks": chunks,
            "outline_count": outline_count,
            "budget": budget_summary(numbered, design["total_word_count"]),
            "conflict_report": conflict,
            "conflict_report_json": None,
            "metadata_json": None}


def list_designs() -> list[dict[str, Any]]:
    designs = db.fetch_all("SELECT * FROM design ORDER BY updated_at DESC")
    out = []
    for d in designs:
        chapters = db.fetch_one(
            "SELECT COUNT(*) AS n FROM design_chapter WHERE design_id=?", (d["id"],))
        outlines = db.fetch_one(
            "SELECT COUNT(*) AS n FROM outline WHERE design_id=?", (d["id"],))
        out.append({**d, "chapter_count": chapters["n"], "outline_count": outlines["n"],
                    "conflict_report_json": None})
    return out


# ---------------------------------------------------------------------------
# 编辑 / 保存
# ---------------------------------------------------------------------------
def create_design(intent: str, *, total_word_count: int, title: str | None = None,
                  description: str | None = None) -> dict[str, Any]:
    if not (3000 <= total_word_count <= 50000):
        raise AppError("VALIDATION_ERROR", message="整体目标字数需在 3000–50000 之间",
                       http_status=422)
    if not (20 <= len(intent.strip()) <= 2000):
        raise AppError("VALIDATION_ERROR", message="写作意图需 20–2000 字",
                       http_status=422)
    design_id = db.insert("design", {
        "title": (title or "").strip()[:100] or None,
        "intent": intent.strip(),
        "description": (description or "").strip()[:5000] or None,
        "total_word_count": total_word_count,
        "status": "draft",
        "model": get_config().get("deepseek.model"),
        "prompt_version": PROMPT_VERSION,
        "created_at": db.now(), "updated_at": db.now(),
    })
    return get_design(design_id)


def update_design(design_id: int, fields: dict[str, Any], *, snapshot: bool = False) -> dict[str, Any]:
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    if snapshot:
        db.save_version("design", design_id, _design_snapshot(design_id))
    data: dict[str, Any] = {"updated_at": db.now()}
    for k in ("title", "intent", "description", "overall_idea"):
        if k in fields:
            v = fields[k]
            data[k] = v.strip() if isinstance(v, str) else v
    if "total_word_count" in fields:
        tw = int(fields["total_word_count"])
        if not (3000 <= tw <= 50000):
            raise AppError("VALIDATION_ERROR", message="整体目标字数需在 3000–50000 之间",
                           http_status=422)
        data["total_word_count"] = tw
        tree = get_tree(design_id)
        normalize_budgets(tree, tw)
        save_tree(design_id, tree)
    db.update("design", design_id, data)
    if "tree" in fields:
        tree = fields["tree"]
        numbered, _ = save_tree(design_id, tree)
        # 父预算重算
        budgets = recalc_parents(numbered)
        with db.connect() as conn:
            for cid, b in budgets.items():
                conn.execute("UPDATE design_chapter SET word_budget=? WHERE id=?", (b, cid))
    if "chunk_links" in fields:
        _save_chunk_links(design_id, fields["chunk_links"])
    return get_design(design_id)


def _save_chunk_links(design_id: int, links: list[dict[str, Any]]) -> None:
    """links: [{chunk_id, chapter_id|null, selected}]"""
    with db.connect() as conn:
        conn.execute("DELETE FROM design_chunk WHERE design_id=?", (design_id,))
        for l in links:
            conn.execute(
                "INSERT INTO design_chunk (design_id, design_chapter_id, chunk_id, score, "
                "query, selected) VALUES (?,?,?,?,?,?)",
                (design_id, l.get("chapter_id"), l["chunk_id"], l.get("score"),
                 l.get("query") or "", 1 if l.get("selected", True) else 0))


def _design_snapshot(design_id: int) -> dict[str, Any]:
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    tree = get_tree(design_id)
    chunks = db.fetch_all("SELECT * FROM design_chunk WHERE design_id=?", (design_id,))
    return {"design": copy.deepcopy(design), "tree": copy.deepcopy(tree),
            "chunks": copy.deepcopy(chunks), "saved_at": db.now()}


# ---------------------------------------------------------------------------
# 冲突检查与确认
# ---------------------------------------------------------------------------
async def conflict_check(design_id: int, *, trace: str) -> dict[str, Any]:
    from ..ai.clients import chat
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    tree = renumber(get_tree(design_id))
    tree_text = "\n".join(
        f"{n['number']} | {n['title']} | {(n['idea'] or '')[:80]} | {n['word_budget']}字"
        for n in tree)
    try:
        content = await chat(
            [{"role": "user", "content": CONFLICT_PROMPT.format(
                intent=truncate_sentence(design["intent"], 2000),
                overall_idea=truncate_sentence(design["overall_idea"] or "（未填写）", 2000),
                tree=truncate_sentence(tree_text, 4000))}],
            trace=trace, module="conflict_check", prompt_version=PROMPT_VERSION,
            json_mode=True, temperature=0.2)
        data, repair = parse_json_loose(content)
        if not isinstance(data, dict):
            raise AppError("AI_BAD_JSON", stage="generate", trace=trace)
        report = {
            "has_conflict": bool(data.get("has_conflict")),
            "conflicts": data.get("conflicts") or [],
            "checked_at": db.now(),
            "repaired": bool(repair),
        }
        db.update("design", design_id, {
            "conflict_report_json": __import__("json").dumps(report, ensure_ascii=False),
            "updated_at": db.now()})
        return report
    except AppError as e:
        return {"has_conflict": None, "conflicts": [], "check_failed": True,
                "error": e.code}


def confirm_design(design_id: int, *, force: bool = False,
                   force_reason: str = "") -> dict[str, Any]:
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    tree = renumber(get_tree(design_id))
    total = design["total_word_count"]
    summary = budget_summary(tree, total)
    conflict = db.get_json_col(design, "conflict_report_json") or {}
    has_conflict = bool(conflict.get("has_conflict"))
    unchecked = bool(conflict.get("check_failed"))
    if (has_conflict or not summary["within_tolerance"]) and not force:
        raise AppError("VALIDATION_ERROR",
                       message="存在未解决冲突或预算失衡，请处理或选择强行保存",
                       http_status=409)
    notes = []
    if force:
        notes.append(f"强行保存：{force_reason or '（未填写原因）'}")
    if unchecked:
        notes.append("未完成冲突检查")
    elif has_conflict:
        notes.append("存在未解决冲突")
    db.update("design", design_id, {
        "status": "confirmed",
        "confirm_notes": "；".join(notes) or None,
        "updated_at": db.now()})
    db.save_version("design", design_id, _design_snapshot(design_id))
    return get_design(design_id)


def duplicate_design(design_id: int) -> dict[str, Any]:
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (design_id,))
    if not design:
        raise AppError("NOT_FOUND")
    new_id = db.insert("design", {
        "title": f"{design['title'] or '未命名'}（副本）", "intent": design["intent"],
        "description": design["description"], "overall_idea": design["overall_idea"],
        "total_word_count": design["total_word_count"], "status": "draft",
        "model": design["model"], "prompt_version": design["prompt_version"],
        "created_at": db.now(), "updated_at": db.now()})
    tree = get_tree(design_id)
    save_tree(new_id, [{**{k: n[k] for k in ("title", "idea", "word_budget", "sort")},
                        "parent_id": n["parent_id"], "id": n["id"]} for n in tree])
    chunks = db.fetch_all("SELECT * FROM design_chunk WHERE design_id=?", (design_id,))
    with db.connect() as conn:
        for c in chunks:
            conn.execute(
                "INSERT INTO design_chunk (design_id, design_chapter_id, chunk_id, score, "
                "query, selected) VALUES (?, NULL, ?, ?, ?, ?)",
                (new_id, c["chunk_id"], c["score"], c["query"], c["selected"]))
    return get_design(new_id)


def delete_design(design_id: int) -> None:
    count = db.fetch_one("SELECT COUNT(*) AS n FROM outline WHERE design_id=?", (design_id,))["n"]
    if count:
        raise AppError("UPSTREAM_IN_USE",
                       message=f"该设计有 {count} 个大纲，请先删除大纲", stage="save")
    db.delete("design", design_id)
