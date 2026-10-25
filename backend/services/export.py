"""全文导出（产品文档 §8.5 / §9.4 / §9.5）。

- 工作稿：完整追溯信息（思路、大纲、片段清单、提示块、冲突检查）。
- 定稿：正文 + 参考文献（GB/T 7714-2015 顺序编码制 / 章节编码制 / 法学引注手册风格）。
- 定稿引用重编号 [1][2]…；缺失元数据输出 [待补充：字段名]，不编造。
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from .. import db
from ..errors import AppError
from ..paths import get_data_dir
from .drafts import NOTICE_RE, get_design_tree

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# GB/T 7714-2015 文献类型标识
_TYPE_CODES = {"学术论文": "[J]", "期刊论文": "[J]", "学位论文": "[D]", "专著": "[M]",
               "法律法规": "[Z]", "司法案例": "[Z]", "新闻报道": "[N]", "网络资料": "[EB/OL]",
               "会议论文": "[C]", "专利": "[P]", "标准": "[S]", "技术报告": "[R]",
               "其他": "[Z]"}


def export_checks(draft_id: int) -> dict[str, Any]:
    draft = db.fetch_one("SELECT * FROM draft WHERE id=?", (draft_id,))
    if not draft:
        raise AppError("NOT_FOUND")
    chapters = db.fetch_all(
        "SELECT dc.*, oc.chapter_number, oc.chapter_title FROM draft_chapter dc "
        "JOIN outline_chapter oc ON oc.id=dc.outline_chapter_id WHERE dc.draft_id=? "
        "ORDER BY dc.sort", (draft_id,))
    unfinished = [{"number": c["chapter_number"], "title": c["chapter_title"]}
                  for c in chapters if not (c["content"] or "").strip()]
    actual = sum(c["word_count"] or 0 for c in chapters)
    target = draft["target_word_count"] or 0
    info_missing = [k for k, v in (("标题", draft["title"]), ("作者", draft["author"]),
                                   ("摘要", draft["abstract"]), ("关键词", draft["keywords"]))
                    if not v]
    invalid = db.fetch_one(
        "SELECT COUNT(*) AS n FROM citation WHERE valid=0 AND draft_chapter_id IN "
        "(SELECT id FROM draft_chapter WHERE draft_id=?)", (draft_id,))["n"]
    deviation = abs(actual - target) / target if target else 0
    done = sum(1 for c in chapters if c["status"] == "done")
    return {
        "unfinished": unfinished,
        "info_missing": info_missing,
        "actual_words": actual, "target_words": target,
        "done": done, "total": len(chapters),
        "deviation": round(deviation, 3),
        "deviation_exceeded": deviation > 0.2,
        "invalid_citations": invalid,
        "can_final_export": invalid == 0,
    }


# ---------------------------------------------------------------------------
# 参考文献（GB/T 7714-2015）
# ---------------------------------------------------------------------------
def _authors(text: str | None) -> str:
    if not text:
        return "[待补充：作者]"
    parts = [p.strip() for p in re.split(r"[;；,，]", text) if p.strip()]
    if len(parts) > 3:
        return "，".join(parts[:3]) + "，等"
    return "，".join(parts)


def _year(date_str: str | None) -> str:
    if not date_str:
        return "[待补充：年份]"
    m = re.match(r"(\d{4})", str(date_str))
    return m.group(1) if m else str(date_str)


def gb7714_entry(doc: dict[str, Any] | None, metadata: dict[str, Any] | None) -> str:
    """按文献类型生成 GB/T 7714-2015 顺序编码制条目。"""
    if not doc and not metadata:
        return "[待补充：文献信息]"
    doc = doc or {}
    meta = metadata or {}
    d_type = doc.get("doc_type") or "其他"
    authors = _authors(doc.get("authors") or meta.get("authors"))
    title = doc.get("title") or meta.get("title") or "[待补充：题名]"
    publisher = doc.get("publisher") or meta.get("publisher")
    published = doc.get("published_at") or meta.get("published_at")
    year = _year(published)

    if d_type in ("学术论文", "期刊论文"):
        journal = meta.get("journal") or doc.get("publisher") or "[待补充：刊名]"
        vol = meta.get("volume")
        issue = meta.get("issue")
        pages = meta.get("pages")
        loc = f"{year}" + (f", {vol}" if vol else "")
        if issue:
            loc += f"({issue})"
        if pages:
            loc += f": {pages}"
        return f"{authors}. {title}[J]. {journal}, {loc}."
    if d_type == "学位论文":
        school = meta.get("school") or "[待补充：学校]"
        return f"{authors}. {title}[D]. {school}, {year}."
    if d_type == "专著":
        place = meta.get("publisher_place") or "[待补充：出版地]"
        pub = publisher or "[待补充：出版社]"
        return f"{authors}. {title}[M]. {place}: {pub}, {year}."
    if d_type == "法律法规":
        authority = meta.get("issuing_authority") or "[待补充：制定机关]"
        doc_no = f"（{meta['document_no']}）" if meta.get("document_no") else ""
        eff = f". {meta['effective_date']}施行" if meta.get("effective_date") else ""
        return f"{authority}. {title}{doc_no}[Z]{eff}."
    if d_type == "司法案例":
        court = meta.get("court") or "[待补充：法院]"
        case_no = meta.get("case_no") or "[待补充：案号]"
        cause = f"（{meta['cause']}）" if meta.get("cause") else ""
        return f"{court}. {title}{cause}{case_no}[Z]. {year}."
    if d_type == "会议论文":
        conf = meta.get("conference") or "[待补充：会议名称]"
        loc = meta.get("conference_location") or ""
        pages = meta.get("pages") or ""
        return f"{authors}. {title}[C]//{conf}. {loc}, {year}: {pages}."
    if d_type == "专利":
        patentee = meta.get("patentee") or authors
        no = meta.get("patent_no") or "[待补充：专利号]"
        gd = meta.get("grant_date") or year
        return f"{patentee}. {title}: {no}[P]. {gd}."
    if d_type == "标准":
        body = meta.get("issuing_body") or publisher or "[待补充：发布机构]"
        no = meta.get("standard_no") or "[待补充：标准号]"
        return f"{body}. {title}: {no}[S]. {year}."
    if d_type == "技术报告":
        inst = meta.get("institution") or "[待补充：机构]"
        no = f"（{meta['report_no']}）" if meta.get("report_no") else ""
        return f"{authors}. {title}{no}[R]. {inst}, {year}."
    if d_type in ("网络资料", "新闻报道"):
        url = meta.get("url") or "[待补充：URL]"
        access = f"[{meta['access_date']}]" if meta.get("access_date") else ""
        pub_date = f"({published})" if published else ""
        return f"{authors}. {title}[EB/OL]. {pub_date}{access}. {url}."
    pub = f". {publisher}" if publisher else ""
    return f"{authors}. {title}[Z]{pub}, {year}."


def citation_ref_entry(c: dict[str, Any]) -> dict[str, Any]:
    """引用记录 → 参考文献条目所需数据。优先快照（源文件删除后仍可用）。"""
    snap = None
    try:
        snap = json.loads(c["document_snapshot_json"]) if c.get("document_snapshot_json") else None
    except json.JSONDecodeError:
        snap = None
    doc = {
        "title": c.get("doc_title") or (snap or {}).get("title"),
        "authors": c.get("doc_authors") or (snap or {}).get("authors"),
        "publisher": c.get("doc_publisher") or (snap or {}).get("publisher"),
        "published_at": c.get("doc_published_at") or (snap or {}).get("published_at"),
        "doc_type": c.get("doc_doc_type") or (snap or {}).get("doc_type"),
    }
    meta = (snap or {}).get("metadata") or {}
    return {"doc": doc, "meta": meta, "entry": gb7714_entry(doc, meta),
            "deleted": c.get("chunk_id") is None and bool(snap)}


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def export_markdown(draft_id: int, *, mode: str = "final",
                    citation_format: str = "gb7714",
                    include_todo: bool = False) -> tuple[str, str]:
    """导出 Markdown。返回 (文本, 文件名)。"""
    draft = db.fetch_one("SELECT * FROM draft WHERE id=?", (draft_id,))
    if not draft:
        raise AppError("NOT_FOUND")
    outline = db.fetch_one("SELECT * FROM outline WHERE id=?", (draft["outline_id"],))
    design = db.fetch_one("SELECT * FROM design WHERE id=?", (outline["design_id"],))
    design_tree = get_design_tree(design["id"])
    chapters = db.fetch_all(
        "SELECT dc.*, oc.chapter_number, oc.chapter_title, oc.content AS outline_content, "
        "oc.chapter_idea_snapshot FROM draft_chapter dc "
        "JOIN outline_chapter oc ON oc.id=dc.outline_chapter_id WHERE dc.draft_id=? "
        "ORDER BY dc.sort", (draft_id,))
    if not any((c["content"] or "").strip() for c in chapters):
        raise AppError("EXPORT_NO_CONTENT", stage="export")

    citations = db.fetch_all(
        "SELECT ci.*, c.document_id AS doc_id, "
        "d.title AS doc_title, d.authors AS doc_authors, d.publisher AS doc_publisher, "
        "d.published_at AS doc_published_at, d.doc_type AS doc_doc_type "
        "FROM citation ci LEFT JOIN chunk c ON c.id=ci.chunk_id "
        "LEFT JOIN document d ON d.id=c.document_id "
        "WHERE ci.draft_chapter_id IN (SELECT id FROM draft_chapter WHERE draft_id=?) "
        "ORDER BY ci.id", (draft_id,))
    notices = db.fetch_all(
        "SELECT n.*, oc.chapter_number, oc.chapter_title FROM notice n "
        "JOIN draft_chapter dc ON dc.id=n.draft_chapter_id "
        "JOIN outline_chapter oc ON oc.id=dc.outline_chapter_id "
        "WHERE dc.draft_id=? ORDER BY n.id", (draft_id,))
    chunks = db.fetch_all(
        "SELECT oc.chapter_number, ock.chunk_id, c.text, c.page_start, c.page_end, "
        "c.heading_path, d.title, d.authors, d.publisher, d.published_at "
        "FROM outline_chunk ock JOIN outline_chapter oc ON oc.id=ock.outline_chapter_id "
        "JOIN chunk c ON c.id=ock.chunk_id JOIN document d ON d.id=c.document_id "
        "WHERE oc.outline_id=? ORDER BY ock.id", (outline["id"],))

    date_str = dt.date.today().strftime("%Y%m%d")
    mode_label = "工作稿" if mode == "work" else "定稿"
    safe_title = _ILLEGAL.sub("_", (draft["title"] or "论文").strip()[:60])
    filename = f"{safe_title}_{date_str}_{mode_label}.md"

    lines: list[str] = []
    if mode == "final":
        lines += _final_body(draft, design, design_tree, chapters, citations, notices,
                             chunks, citation_format, include_todo)
    else:
        lines += _work_body(draft, design, design_tree, chapters, citations, notices, chunks)
    return "\n".join(lines), filename


def _heading(level: int, text: str) -> str:
    return f"{'#' * level} {text}"


def _toc(design_tree: list[dict[str, Any]]) -> str:
    lines = ["## 目录"]
    for n in design_tree:
        indent = "  " * (n.get("depth", 1) - 1)
        lines.append(f"{indent}{n['number']} {n['title']}")
    return "\n".join(lines)


def _final_body(draft: dict[str, Any], design: dict[str, Any],
                design_tree: list[dict[str, Any]], chapters: list[dict[str, Any]],
                citations: list[dict[str, Any]], notices: list[dict[str, Any]],
                chunks: list[dict[str, Any]], citation_format: str,
                include_todo: bool) -> list[str]:
    lines = [_heading(1, draft["title"] or "未命名论文")]
    meta_lines = [f"> 作者：{draft['author'] or '[待补充]'}",
                  f"> 学校：{draft['school'] or '[待补充]'}",
                  f"> 日期：{draft['publish_date'] or dt.date.today().isoformat()}"]
    lines += meta_lines + [""]
    if draft["abstract"]:
        lines += [_heading(2, "摘要"), draft["abstract"], ""]
    if draft["keywords"]:
        lines += [f"**关键词**：{draft['keywords']}", ""]
    lines += [_toc(design_tree), ""]

    # 引用重编号：[章节号-序号] → 全局 [1][2]…
    # 同一文献多次引用合并为一条（§9.4 顺序编码制）
    def _doc_key(cit: dict[str, Any]) -> tuple:
        if cit.get("doc_id"):
            return ("doc", cit["doc_id"])
        snap = None
        try:
            snap = json.loads(cit["document_snapshot_json"]) if cit.get("document_snapshot_json") else None
        except json.JSONDecodeError:
            snap = None
        return ("snap", (snap or {}).get("document_id") or (snap or {}).get("title"))

    global_map: dict[tuple[int, str], int] = {}   # (draft_chapter_id, marker) → 全局序号
    ref_entries: dict[int, dict[str, Any]] = {}
    if citation_format != "chapter":
        seq = 0
        doc_seq: dict[tuple, int] = {}
        for c in chapters:
            for cit in [x for x in citations if x["draft_chapter_id"] == c["id"]]:
                key = (c["id"], cit["marker"])
                if key in global_map:
                    continue
                dkey = _doc_key(cit)
                if dkey in doc_seq:
                    global_map[key] = doc_seq[dkey]
                else:
                    seq += 1
                    global_map[key] = seq
                    doc_seq[dkey] = seq
                    ref_entries[seq] = citation_ref_entry(cit)

    chapters_by_dc = {c["id"]: c for c in chapters}
    for n in design_tree:
        dc = next((c for c in chapters if c["chapter_number"] == n["number"]), None)
        if dc is None or not (dc["content"] or "").strip():
            continue
        body = dc["content"]
        if citation_format != "chapter":
            for cit in [x for x in citations if x["draft_chapter_id"] == dc["id"]]:
                body = body.replace(cit["marker"], f"[{global_map[(dc['id'], cit['marker'])]}]")
        if not include_todo:
            body = NOTICE_RE.sub("", body)
        lines += [f"## {n['number']} {n['title']}", "", body, ""]

    # 参考文献
    lines += [_heading(2, "参考文献"), ""]
    if citation_format == "chapter":
        # 章节编码制：按章列出，保留 [4.1.1-1] 标记
        for dc in chapters:
            cites = [x for x in citations if x["draft_chapter_id"] == dc["id"]]
            if not cites:
                continue
            lines += [f"**{dc['chapter_number']} {dc['chapter_title']}**"]
            seen = set()
            for cit in cites:
                key = cit["marker"]
                if key in seen:
                    continue
                seen.add(key)
                entry = citation_ref_entry(cit)
                page = f"，第{cit['page_start']}页" if cit.get("page_start") else ""
                lines.append(f"- {cit['marker']} {entry['entry']}{page}"
                             + ("（源文件已删除）" if entry.get("deleted") else ""))
            lines.append("")
    else:
        for i in sorted(ref_entries):
            entry = ref_entries[i]
            lines.append(f"[{i}] {entry['entry']}"
                         + ("（源文件已删除）" if entry.get("deleted") else ""))
        lines.append("")

    # 待办清单（可选）
    open_notices = [n for n in notices if n["status"] == "open"]
    if include_todo and open_notices:
        lines += [_heading(2, "待办清单"), ""]
        for n in open_notices:
            lines.append(f"- [{n['notice_type']}] {n['chapter_number']} {n['chapter_title']}："
                         f"{n['content']}")
        lines.append("")
    return lines


def _work_body(draft: dict[str, Any], design: dict[str, Any],
               design_tree: list[dict[str, Any]], chapters: list[dict[str, Any]],
               citations: list[dict[str, Any]], notices: list[dict[str, Any]],
               chunks: list[dict[str, Any]]) -> list[str]:
    checks = export_checks(draft["id"])
    lines = [_heading(1, f"{draft['title'] or '未命名论文'}（工作稿）"), ""]
    lines.append(f"> 总目标字数：{draft['target_word_count'] or '-'} ｜ "
                 f"实际字数：{checks['actual_words']} ｜ 完成章节："
                 f"{checks['done']}/{checks['total'] if checks['total'] else len(chapters)}")
    lines += ["", _toc(design_tree), ""]
    if design["overall_idea"]:
        lines += [_heading(2, "文章整体思路"), design["overall_idea"], ""]
    conflict = db.get_json_col(design, "conflict_report_json")
    if conflict and conflict.get("conflicts"):
        lines += [_heading(2, "冲突检查结果（保存时）"), ""]
        for cf in conflict["conflicts"]:
            lines.append(f"- [{cf.get('severity')}] {cf.get('type')} "
                         f"{'、'.join(cf.get('chapters', []))}：{cf.get('description')} → "
                         f"{cf.get('suggestion')}")
        lines.append("")

    chunks_by_number: dict[str, list[dict[str, Any]]] = {}
    for ck in chunks:
        chunks_by_number.setdefault(ck["chapter_number"], []).append(ck)

    for n in design_tree:
        dc = next((c for c in chapters if c["chapter_number"] == n["number"]), None)
        if dc is None:
            continue
        lines += [_heading(2, f"{n['number']} {n['title']}"), ""]
        if dc["chapter_idea_snapshot"]:
            lines += ["**章节设计思路**：", "", dc["chapter_idea_snapshot"], ""]
        if dc["outline_content"]:
            lines += ["**大纲**：", "", dc["outline_content"], ""]
        if not (dc["content"] or "").strip():
            lines += ["（本节未生成，待补写）", ""]
            continue
        lines += ["**正文**：", "", dc["content"], ""]
        cites = [x for x in citations if x["draft_chapter_id"] == dc["id"]]
        if cites:
            lines += [f"**本章引用列表（{len(cites)} 条）**", ""]
            lines.append("| 标记 | 来源 | 页码 | 引文 | 状态 |")
            lines.append("|---|---|---|---|---|")
            for cit in cites:
                entry = citation_ref_entry(cit)
                status = "有效" if cit["valid"] else "⚠ 待核实"
                lines.append(
                    f"| {cit['marker']} | {entry['doc']['title'] or '[待补充]'} | "
                    f"{cit.get('page_start') or '-'} | {(cit.get('quote') or '')[:40]} | {status} |")
            lines.append("")
        clist = chunks_by_number.get(n["number"], [])
        if clist:
            lines += [f"**使用的片段（{len(clist)} 条）**", ""]
            for i, ck in enumerate(clist, 1):
                lines.append(f"{i}. 《{ck['title']}》第{ck['page_start'] or '-'}页"
                             f"（{ck['heading_path'] or '无标题路径'}）")
                lines.append(f"   > {(ck['text'] or '')[:200]}……")
            lines.append("")
    return lines


def export_to_file(draft_id: int, *, mode: str, citation_format: str = "gb7714",
                   include_todo: bool = False) -> Path:
    text, filename = export_markdown(draft_id, mode=mode, citation_format=citation_format,
                                     include_todo=include_todo)
    out_dir = get_data_dir() / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(text, encoding="utf-8")
    return path
