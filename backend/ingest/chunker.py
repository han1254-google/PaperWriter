"""分块规则（产品文档 §5.4）—— 按内容切分，段落优先。

原则（按优先级）：
1. 标题边界：每个标题开启新片段，标题文字进入片段；
2. 段落完整：完整的段落尽量不拆开——宁可片段到 1000–2000 字，
   也不把一段连贯论述腰斩；
3. 整段超过硬上限（约 1000 tokens，低于规范 1200 token 上限）时，
   才在句末标点处切分，相邻片段重叠 80–120 字；
4. 目录/参考文献/表格/脚注独立成块，保持完整（仅绝对上限硬切）。

段落级对齐的片段之间不设重叠：段落本身是完整内容单元，
重叠是为「滑动窗口切分」设计的，按内容切分时不再需要。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .parsers import Block, ParsedDoc

CHUNK_SOFT_MAX = 1000   # 软上限：段落完整时允许的片段长度（目标区间 500–1000 字）
CHUNK_HARD_MAX = 2000   # 硬上限：整段超过才在段内按句切分（约 1000 tokens）
OVERLAP = 100           # 段内切分时的重叠字符
MAX_CHUNK_CHARS = 2400  # 特殊块（表格/脚注等）的绝对上限（约 1200 tokens）


@dataclass
class Chunk:
    text: str
    chunk_type: str
    heading_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    para_start: int | None = None
    para_end: int | None = None


_SENT_END = re.compile(r"(?<=[。！？!?；;])|(?<=\.\s)|(?<=\n)")


def _split_long(text: str, page: int | None, heading: str, btype: str,
                para_no: int) -> list[Chunk]:
    """超长内容切分：正文按句末标点切（带重叠）；表格/脚注等仅在绝对上限硬切。"""
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if btype in ("table", "footnote", "reference", "toc"):
        if len(text) <= MAX_CHUNK_CHARS:
            return [Chunk(text=text, chunk_type=btype, heading_path=heading,
                          page_start=page, page_end=page,
                          para_start=para_no, para_end=para_no)]
        out = []
        for i in range(0, len(text), MAX_CHUNK_CHARS - OVERLAP):
            out.append(Chunk(text=text[i : i + MAX_CHUNK_CHARS].strip(),
                             chunk_type=btype, heading_path=heading,
                             page_start=page, page_end=page,
                             para_start=para_no, para_end=para_no))
        return out
    if len(text) <= CHUNK_HARD_MAX:
        return [Chunk(text=text, chunk_type=btype, heading_path=heading,
                      page_start=page, page_end=page,
                      para_start=para_no, para_end=para_no)]
    sentences = [s for s in _SENT_END.split(text) if s.strip()]
    chunks: list[Chunk] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) > CHUNK_SOFT_MAX and len(cur) >= CHUNK_SOFT_MAX // 3:
            chunks.append(Chunk(text=cur.strip(), chunk_type=btype, heading_path=heading,
                                page_start=page, page_end=page,
                                para_start=para_no, para_end=para_no))
            cur = cur[-OVERLAP:] if len(cur) > OVERLAP else cur
        cur += s
        # 单句过长（无句界）：硬切，携带重叠
        while len(cur) > CHUNK_HARD_MAX:
            cut = cur[:CHUNK_SOFT_MAX]
            chunks.append(Chunk(text=cut.strip(), chunk_type=btype, heading_path=heading,
                                page_start=page, page_end=page,
                                para_start=para_no, para_end=para_no))
            cur = cur[CHUNK_SOFT_MAX - OVERLAP:]
    if cur.strip():
        chunks.append(Chunk(text=cur.strip(), chunk_type=btype, heading_path=heading,
                            page_start=page, page_end=page,
                            para_start=para_no, para_end=para_no))
    return chunks


def chunk_document(parsed: ParsedDoc) -> list[Chunk]:
    """按内容切分：段落完整优先，标题/段落边界天然成为片段边界。"""
    chunks: list[Chunk] = []
    buf: list[str] = []
    meta: dict[str, Any] = {}

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        text = "\n".join(buf).strip()
        if text:
            btype = meta.get("type", "body")
            if len(text) > CHUNK_HARD_MAX:
                chunks.extend(_split_long(text, meta.get("page_start"),
                                          meta.get("heading", ""), btype,
                                          meta.get("para_start") or 0))
            else:
                chunks.append(Chunk(
                    text=text, chunk_type=btype,
                    heading_path=meta.get("heading", ""),
                    page_start=meta.get("page_start"),
                    page_end=meta.get("page_end"),
                    para_start=meta.get("para_start"),
                    para_end=meta.get("para_end")))
        buf = []
        meta.clear()

    for b in parsed.blocks:
        if not b.text.strip():
            continue
        # 目录 / 参考文献 / 表格 / 脚注 / 摘要 / 图表说明：独立成块，保持完整
        if b.block_type in ("reference", "toc", "footnote", "table",
                            "abstract", "figure_caption"):
            if buf and meta.get("heading_only"):
                # 缓冲里只有标题（如「参考文献」）：并入特殊块，避免 4 字碎片片段
                chunks.extend(_split_long(buf[0] + "\n" + b.text, b.page,
                                          b.heading_path, b.block_type, b.para_no))
                buf = []
                meta.clear()
            else:
                flush()
                chunks.extend(_split_long(b.text, b.page, b.heading_path,
                                          b.block_type, b.para_no))
            continue
        # 标题：天然边界；标题文字本身进入新片段开头
        if b.block_type == "heading":
            flush()
            buf = [b.text]
            meta = {"heading": b.heading_path, "type": "body",
                    "page_start": b.page, "page_end": b.page,
                    "para_start": b.para_no, "para_end": b.para_no,
                    "heading_only": True}
            continue
        # 正文段落：整段加入；只有加上后会超硬上限才提前 flush
        joined = "".join(buf)
        if buf and len(joined) + len(b.text) > CHUNK_HARD_MAX:
            flush()
        if not buf:
            meta = {"heading": b.heading_path, "type": "body",
                    "page_start": b.page, "page_end": b.page,
                    "para_start": b.para_no, "para_end": b.para_no}
        buf.append(b.text)
        meta["page_end"] = b.page
        meta["para_end"] = b.para_no
        meta["heading_only"] = False
        # 达到目标区间即收尾（在段落边界上，不切断内容）
        if len("".join(buf)) >= CHUNK_SOFT_MAX:
            flush()
    flush()
    return chunks


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def chunk_to_row(doc_id: int, index: int, c: Chunk) -> dict[str, Any]:
    return {
        "document_id": doc_id,
        "chunk_index": index,
        "chunk_type": c.chunk_type,
        "text": c.text,
        "token_count": estimate_tokens(c.text),
        "page_start": c.page_start,
        "page_end": c.page_end,
        "para_start": c.para_start,
        "para_end": c.para_end,
        "heading_path": c.heading_path,
    }
