"""实验方案设计（理工版）：意图解析+查询扩展 → 证据检索 → 方案生成 → 溯源绑定与数字校验。

防编造机制：
- 证据在 prompt 中的 [来源N] 编号 = 输出中的 [来源N] = plan_citation 的 key（物理绑定）；
- 生成后数字校验：引用句中的数字必须能在绑定证据的原文里找到，否则标「数据待核实」；
- 模型引用证据之外的内容 → 记录为无效溯源（chunk_id 为空），界面标红。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db
from ..ai.jsonutil import parse_json_loose
from ..ai.prompts import (EXPERIMENT_INTENT_PROMPT, EXPERIMENT_PLAN_PROMPT,
                          PROMPT_VERSION, render_evidence)
from ..config import get_config
from ..errors import AppError
from ..textutil import truncate_sentence

SOURCE_RE = re.compile(r"\[来源\s*(\d+)(?:\s*[,，、]\s*(\d+))*\]")
NUMBER_RE = re.compile(r"\d+\.\d+|\d{2,}")

# Methods 类章节加权：实验方案最需要方法学证据（§heading_path 含这些词）
_METHODS_BONUS = 0.06
_METHODS_KEYWORDS = ("方法", "实验", "材料", "结果", "讨论", "methods", "method",
                     "materials", "experiment", "results", "discussion")


def _methods_bonus(chunk: dict[str, Any]) -> float:
    hp = (chunk.get("heading_path") or "").lower()
    for kw in _METHODS_KEYWORDS:
        if kw in hp:
            return _METHODS_BONUS
    return 0.0


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create_plan(research_idea: str, title: str | None = None) -> dict[str, Any]:
    idea = research_idea.strip()
    if not (10 <= len(idea) <= 2000):
        raise AppError("VALIDATION_ERROR", message="研究想法需 10–2000 字",
                       http_status=422)
    plan_id = db.insert("experiment_plan", {
        "title": (title or "").strip()[:100] or None,
        "research_idea": idea,
        "status": "draft",
        "model": get_config().get("deepseek.model"),
        "prompt_version": PROMPT_VERSION,
        "created_at": db.now(), "updated_at": db.now(),
    })
    return get_plan(plan_id)


def get_plan(plan_id: int) -> dict[str, Any]:
    plan = db.fetch_one("SELECT * FROM experiment_plan WHERE id=?", (plan_id,))
    if not plan:
        raise AppError("NOT_FOUND")
    plan["intent"] = db.get_json_col(plan, "intent_json")
    plan["intent_json"] = None
    citations = db.fetch_all(
        "SELECT pc.*, c.text AS chunk_text, c.heading_path, c.page_start, c.page_end, "
        "d.title AS doc_title, d.authors AS doc_authors, d.published_at AS doc_published_at, "
        "d.doc_type AS doc_doc_type, d.id AS doc_id "
        "FROM plan_citation pc LEFT JOIN chunk c ON c.id = pc.chunk_id "
        "LEFT JOIN document d ON d.id = c.document_id "
        "WHERE pc.plan_id=? ORDER BY pc.source_no", (plan_id,))
    return {**plan, "citations": citations,
            "verified_count": sum(1 for c in citations if c["verified"]),
            "invalid_count": sum(1 for c in citations if not c["verified"])}


def list_plans() -> list[dict[str, Any]]:
    rows = db.fetch_all(
        "SELECT p.*, (SELECT COUNT(*) FROM plan_citation pc WHERE pc.plan_id=p.id) "
        "AS citation_count FROM experiment_plan p ORDER BY p.updated_at DESC")
    return rows


def update_plan(plan_id: int, fields: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {"updated_at": db.now()}
    if "title" in fields:
        data["title"] = (fields["title"] or "").strip()[:100] or None
    if "content" in fields:
        data["content"] = fields["content"]
    if "research_idea" in fields and fields["research_idea"]:
        data["research_idea"] = str(fields["research_idea"]).strip()[:2000]
    db.update("experiment_plan", plan_id, data)
    return get_plan(plan_id)


def delete_plan(plan_id: int) -> None:
    db.delete("experiment_plan", plan_id)


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
async def generate_plan(plan_id: int, *, trace: str, job_id: int,
                        on_event) -> dict[str, Any]:
    from ..ai.clients import chat, chat_stream
    from ..jobs import get_runner
    from ..search.hybrid import hybrid_search

    runner = get_runner()
    plan = db.fetch_one("SELECT * FROM experiment_plan WHERE id=?", (plan_id,))
    if not plan:
        raise AppError("NOT_FOUND")

    # 1. 意图解析 + 查询扩展（合并为一次调用；max_tokens 给足，思维链会占预算）
    await on_event({"type": "progress", "message": "解析研究想法，构造检索查询…"})
    intent_content = await chat(
        [{"role": "user", "content": EXPERIMENT_INTENT_PROMPT.format(
            idea=truncate_sentence(plan["research_idea"], 1500))}],
        trace=trace, module="plan_intent", prompt_version=PROMPT_VERSION,
        json_mode=True, temperature=0.1, max_tokens=2048)
    intent, repair = parse_json_loose(intent_content)
    if not isinstance(intent, dict):
        intent = {"queries": [truncate_sentence(plan["research_idea"], 100)]}
    queries = [q for q in (intent.get("queries") or []) if isinstance(q, str) and q.strip()][:5]
    if not queries:
        queries = [truncate_sentence(plan["research_idea"], 100)]
    db.update("experiment_plan", plan_id, {
        "intent_json": json.dumps(intent, ensure_ascii=False),
        "updated_at": db.now()})
    runner.check_cancel(job_id)

    # 2. 证据检索（全部查询一次批量嵌入；按文档限流已在 hybrid_search 内）
    await on_event({"type": "progress", "message": "检索文献证据…"})
    results = await hybrid_search(queries, scope={"all": True}, trace=trace, top_n=30)
    runner.check_cancel(job_id)
    # Methods 类章节加权后重排
    for r in results:
        r["score"] = (r.get("score") or 0) + _methods_bonus(r)
    results.sort(key=lambda r: -(r.get("score") or 0))
    evidence = results[:30]
    if evidence:
        await on_event({"type": "retrieval", "chunks": [
            {"chunk_id": e["chunk_id"], "title": e["title"],
             "page_start": e.get("page_start"), "heading_path": e.get("heading_path"),
             "text_preview": (e["text"] or "")[:120]} for e in evidence]})
    else:
        await on_event({"type": "retrieval_empty"})

    # 3. 生成（流式；[来源N] 与证据编号物理绑定）
    evidence_text = render_evidence(evidence) if evidence else "（未检索到任何文献证据）"
    intent_text = json.dumps(intent, ensure_ascii=False, indent=2)
    prompt = EXPERIMENT_PLAN_PROMPT.format(
        idea=plan["research_idea"],
        intent=intent_text,
        evidence_count=len(evidence),
        evidence=evidence_text)
    buf: list[str] = []
    async for ev in chat_stream([{"role": "user", "content": prompt}],
                                trace=trace, module="plan_generate",
                                prompt_version=PROMPT_VERSION, temperature=0.3):
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

    # 4. 溯源绑定 + 数字校验
    await on_event({"type": "progress", "message": "绑定溯源与校验数据…"})
    db.save_version("experiment_plan", plan_id,
                    {"content": plan["content"], "saved_at": db.now()})
    db.update("experiment_plan", plan_id, {
        "content": content, "status": "draft",
        "model": get_config().get("deepseek.model"),
        "prompt_version": PROMPT_VERSION,
        "updated_at": db.now()})
    validation = _bind_and_verify(plan_id, content, evidence)
    return {"plan_id": plan_id, "validation": validation}


def _bind_and_verify(plan_id: int, content: str,
                     evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """解析 [来源N] 标记 → 写 plan_citation → 数字校验。

    证据编号 = prompt 里的编号 = 溯源 key；模型引用证据外的编号则记录无效溯源。
    """
    with db.connect() as conn:
        conn.execute("DELETE FROM plan_citation WHERE plan_id=?", (plan_id,))
    evidence_by_no = {i + 1: e for i, e in enumerate(evidence)}
    stats = {"sources": 0, "unverified": 0, "invalid_bind": 0}
    seen: set[int] = set()
    for m in SOURCE_RE.finditer(content):
        for num_s in m.groups():
            if not num_s:
                continue
            source_no = int(num_s)
            if source_no in seen:
                continue
            seen.add(source_no)
            stats["sources"] += 1
            chunk = evidence_by_no.get(source_no)
            if chunk is None:
                # 模型引用了证据之外的内容：记录无效溯源，界面标红
                db.insert("plan_citation", {
                    "plan_id": plan_id, "source_no": source_no, "chunk_id": None,
                    "quote": truncate_sentence(content[max(0, m.start() - 60):m.end() + 40], 200),
                    "verified": 0, "data_note": "模型引用了证据之外的内容",
                    "created_at": db.now(), "updated_at": db.now()})
                stats["invalid_bind"] += 1
                continue
            # 数字校验：引用句中的数字必须能在证据原文中找到
            sentence = content[max(0, m.start() - 100):m.end() + 60]
            missing = _missing_numbers(sentence, evidence_by_no, m)
            doc = db.fetch_one("SELECT * FROM document WHERE id=?", (chunk["document_id"],))
            snapshot = json.dumps({
                "title": doc["title"], "authors": doc["authors"],
                "publisher": doc["publisher"], "published_at": doc["published_at"],
                "doc_type": doc["doc_type"], "document_id": doc["id"],
            }, ensure_ascii=False) if doc else None
            verified = 0 if missing else 1
            db.insert("plan_citation", {
                "plan_id": plan_id, "source_no": source_no, "chunk_id": chunk["chunk_id"],
                "quote": truncate_sentence(sentence.strip(), 200),
                "verified": verified,
                "data_note": f"以下数字未在证据原文中找到：{', '.join(missing)}" if missing else None,
                "created_at": db.now(), "updated_at": db.now()})
            if missing:
                stats["unverified"] += 1
    return stats


def _missing_numbers(sentence: str, evidence_by_no: dict[int, dict[str, Any]],
                     marker_match: re.Match) -> list[str]:
    """检查引用句中的数字是否出现在绑定证据的原文里（忽略空白差异）。"""
    from ..services.drafts import _norm  # 复用空白归一化
    numbers = [n for n in NUMBER_RE.findall(sentence)
               if not n.replace(".", "").isdigit() or len(n) > 1]
    cited_nos = [int(g) for g in marker_match.groups() if g]
    texts = [_norm(evidence_by_no[n]["text"] or "")
             for n in cited_nos if n in evidence_by_no]
    if not texts:
        return numbers
    joined = "".join(texts)
    missing = [n for n in numbers if n not in joined]
    return missing
