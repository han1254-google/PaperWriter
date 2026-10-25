"""分块规则（产品文档 §5.4）。

- 中文约 500–800 字 / 英文约 300–500 词；相邻片段重叠 80–120 字。
- 优先在段落、标题边界切分；表格整体保留；超长块按句切。
- 记录 heading_path、page_start/end、para_start/end。
- 默认检索排除 reference / toc（检索时过滤）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .parsers import Block, ParsedDoc

CHUNK_MIN = 300          # 低于此长度仍单独成块
CHUNK_TARGET = 600       # 目标长度（中文字符）
CHUNK_MAX = 850          # 超过即切分
OVERLAP = 100            # 重叠字符
MAX_CHUNK_CHARS = 2400   # 约 1200 tokens（中文 1 字 ≈ 0.5 token）


@dataclass
class Chunk:
    text: str
    chunk_type: str
    heading_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    para_start: int | None = None
    para_end: int | None = None


def _is_english(text: str) -> bool:
    letters = sum(1 for c in text if c.isalpha())
    if not letters:
        return False
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    return ascii_letters / letters > 0.7


_SENT_END = re.compile(r"(?<=[。！？!?；;])|(?<=\.\s)|(?<=\n)")


def _split_long(text: str, page: int | None, heading: str, btype: str,
                para_no: int) -> list[Chunk]:
    """超长块切分：正文按句边界切到 ~600 字；表格/脚注等整体保留，仅硬上限切分。"""
    text = re.sub(r"\n{2,}", "\n", text).strip()
    # 表格 / 脚注 / 参考文献 / 目录：整体保留，只受绝对上限约束
    if btype in ("table", "footnote", "reference", "toc"):
        if len(text) <= MAX_CHUNK_CHARS:
            return [Chunk(text=text, chunk_type=btype, heading_path=heading,
                          page_start=page, page_end=page,
                          para_start=para_no, para_end=para_no)]
        out = []
        for i in range(0, len(text), MAX_CHUNK_CHARS - OVERLAP):
            seg = text[i : i + MAX_CHUNK_CHARS]
            out.append(Chunk(text=seg.strip(), chunk_type=btype, heading_path=heading,
                             page_start=page, page_end=page,
                             para_start=para_no, para_end=para_no))
        return out
    if len(text) <= CHUNK_MAX:
        return [Chunk(text=text, chunk_type=btype, heading_path=heading,
                      page_start=page, page_end=page,
                      para_start=para_no, para_end=para_no)]
    sentences = [s for s in _SENT_END.split(text) if s.strip()]
    chunks: list[Chunk] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) > CHUNK_MAX and len(cur) >= CHUNK_MIN:
            chunks.append(Chunk(text=cur.strip(), chunk_type=btype, heading_path=heading,
                                page_start=page, page_end=page,
                                para_start=para_no, para_end=para_no))
            cur = cur[-OVERLAP:] if len(cur) > OVERLAP else cur
        cur += s
        # 单句过长（无句界）：硬切，携带重叠
        while len(cur) > CHUNK_MAX:
            cut = cur[:CHUNK_TARGET]
            chunks.append(Chunk(text=cut.strip(), chunk_type=btype, heading_path=heading,
                                page_start=page, page_end=page,
                                para_start=para_no, para_end=para_no))
            cur = cur[CHUNK_TARGET - OVERLAP:]
    if cur.strip():
        chunks.append(Chunk(text=cur.strip(), chunk_type=btype, heading_path=heading,
                            page_start=page, page_end=page,
                            para_start=para_no, para_end=para_no))
    return chunks


def chunk_document(parsed: ParsedDoc) -> list[Chunk]:
    """块级分块：标题边界优先，段落聚合，重叠 80–120 字。"""
    chunks: list[Chunk] = []
    buf_text = ""
    buf_type = "body"
    buf_heading = ""
    buf_page_start: int | None = None
    buf_page_end: int | None = None
    buf_para_start: int | None = None
    buf_para_end: int | None = None

    def flush() -> None:
        nonlocal buf_text
        if not buf_text.strip():
            return
        pieces = _split_long(buf_text, buf_page_start, buf_heading, buf_type, buf_para_start or 0)
        for i, p in enumerate(pieces):
            if len(pieces) > 1:
                p.page_start = buf_page_start
                p.page_end = buf_page_end
                p.para_start = buf_para_start
                p.para_end = buf_para_end
            chunks.append(p)
        buf_text = ""

    def boundary_cost(block: Block) -> int:
        # 标题 / 表格 / 类型变化 → 强边界
        if block.block_type == "heading":
            return 3
        if block.block_type == "table":
            return 3
        if block.block_type != buf_type:
            return 2
        return 1

    for b in parsed.blocks:
        if not b.text.strip():
            continue
        # 目录 / 参考文献单独成块（检索时过滤，但保留可追溯）
        if b.block_type in ("reference", "toc"):
            chunks.extend(_split_long(b.text, b.page, b.heading_path, b.block_type, b.para_no))
            continue
        # 脚注 / 表格 / 摘要 / 图表说明独立成块
        if b.block_type in ("footnote", "table", "abstract", "figure_caption"):
            chunks.extend(_split_long(b.text, b.page, b.heading_path, b.block_type, b.para_no))
            continue
        # 标题：边界
        if b.block_type == "heading":
            flush()
            buf_heading = b.heading_path
            continue
        cost = boundary_cost(b)
        est = len(buf_text) + len(b.text)
        if buf_text and (cost >= 2 or est > CHUNK_MAX):
            flush()
            buf_heading = b.heading_path
        if not buf_text:
            buf_type = b.block_type
            buf_heading = b.heading_path
            buf_page_start = b.page
            buf_para_start = b.para_no
        buf_text += ("\n" if buf_text else "") + b.text
        buf_page_end = b.page
        buf_para_end = b.para_no
        if len(buf_text) >= CHUNK_MAX:
            flush()
    flush()

    # 后处理：重叠（前一块末尾 ~100 字补到后一块开头）
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        cur = chunks[i]
        if prev.chunk_type in ("reference", "toc", "table", "footnote") or \
           cur.chunk_type in ("reference", "toc", "table", "footnote"):
            continue
        tail = prev.text[-OVERLAP:]
        if len(tail) >= 40 and not cur.text.startswith(tail):
            cur.text = tail + cur.text
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
