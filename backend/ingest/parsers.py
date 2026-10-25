"""文档解析（产品文档 §5.3）。

输出统一的 ParsedDoc：
- blocks: 文本块列表（段落 / 标题 / 表格 / 脚注），带 heading_path、页码、段落序号
- title_hint: 从标题样式 / 首页大字推断的标题候选
- page_count: 页码
- needs_ocr: PDF 无文本层标记

解析原则：不跨页错乱；页眉页脚 / 页码剔除；表格整体保留。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import AppError

BLOCK_TYPES = ("body", "heading", "table", "figure_caption", "footnote",
               "reference", "toc", "abstract")


@dataclass
class Block:
    text: str
    block_type: str = "body"
    page: int | None = None
    para_no: int = 0
    heading_path: str = ""
    table_meta: dict[str, Any] | None = None


@dataclass
class ParsedDoc:
    blocks: list[Block] = field(default_factory=list)
    title_hint: str = ""
    page_count: int = 0
    needs_ocr: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return any(b.text.strip() for b in self.blocks)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
_HEADING_FONT_RATIO = 1.15          # 字号大于正文中位数 1.15 倍视为标题
_PAGE_NO_RE = re.compile(r"^\s*-?\s*\d{1,4}\s*-?\s*$")
_HEADER_TOLERANCE_PAGES = 5


def _clean_text(s: str) -> str:
    # 断词连字符（英文）：行尾 "de-\n" 拼接
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _heading_level(font_size: float, body_median: float) -> int:
    if not body_median or font_size <= 0:
        return 0
    ratio = font_size / body_median
    if ratio >= _HEADING_FONT_RATIO + 0.35:
        return 1
    if ratio >= _HEADING_FONT_RATIO + 0.1:
        return 2
    return 0


def parse_pdf(path: Path) -> ParsedDoc:
    import pymupdf  # PyMuPDF 2.x

    doc = pymupdf.open(str(path))
    if doc.needs_pass:
        doc.close()
        raise AppError("FILE_ENCRYPTED", stage="parse")
    if doc.page_count == 0:
        doc.close()
        raise AppError("FILE_CORRUPT", stage="parse")

    result = ParsedDoc(page_count=doc.page_count)
    text_pages = 0

    # 1. 统计正文字号中位数（排除页眉页脚）
    size_samples: list[float] = []
    for page in doc[: min(doc.page_count, 20)]:
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = span.get("text", "").strip()
                    if t and not _PAGE_NO_RE.match(t):
                        size_samples.append(span.get("size", 0))
    size_samples.sort()
    body_median = size_samples[len(size_samples) // 2] if size_samples else 0.0

    # 2. 逐页提取：剔除页眉页脚（首页/尾页重复文本）与页码
    header_candidates: dict[str, int] = {}
    para_no = 0
    # 表格区域（用于把表格从正文流中抽出）
    for pno in range(doc.page_count):
        page = doc[pno]
        d = page.get_text("dict")
        lines: list[dict[str, Any]] = []
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans).strip()
                size = max(s.get("size", 0) for s in spans)
                y = line.get("bbox", (0, 0, 0, 0))[1]
                lines.append({"text": _clean_text(text), "size": size, "y": y,
                              "flags": spans[0].get("flags", 0)})
        if not lines:
            continue
        text_pages += 1
        top_line = lines[0]["text"]
        bottom_line = lines[-1]["text"]
        header_candidates[top_line] = header_candidates.get(top_line, 0) + 1
        header_candidates[bottom_line] = header_candidates.get(bottom_line, 0) + 1
        if len(lines) >= 2:
            header_candidates[lines[1]["text"]] = header_candidates.get(lines[1]["text"], 0) + 1

    repeated = {t for t, n in header_candidates.items()
                if n >= min(_HEADER_TOLERANCE_PAGES, max(2, doc.page_count // 2)) and len(t) <= 60}

    # 表格检测（PyMuPDF find_tables，无表格时返回空）
    def table_zones(pno: int) -> list[tuple[float, float, float, float]]:
        try:
            tabs = doc[pno].find_tables()
            return [t.bbox for t in tabs.tables if t.row_count > 1]
        except Exception:
            return []

    def in_zone(y: float, zones: list[tuple[float, float, float, float]]) -> bool:
        return any(z[1] - 2 <= y <= z[3] + 2 for z in zones)

    # 3. 组装块
    for pno in range(doc.page_count):
        page = doc[pno]
        d = page.get_text("dict")
        zones = table_zones(pno)
        lines = []
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = _clean_text("".join(s["text"] for s in spans))
                if not text or _PAGE_NO_RE.match(text) or text in repeated:
                    continue
                size = max(s.get("size", 0) for s in spans)
                y = line.get("bbox", (0, 0, 0, 0))[1]
                flags = spans[0].get("flags", 0)
                lines.append({"text": text, "size": size, "y": y,
                              "flags": flags, "in_table": in_zone(y, zones)})

        # 表格文本块
        for z in zones:
            try:
                rows = doc[pno].find_tables().tables
                for t in rows:
                    if abs(t.bbox[1] - z[1]) < 2:
                        cells = []
                        for row in t.extract():
                            cells.append(" | ".join(c or "" for c in row))
                        text = "\n".join(cells)
                        if text.strip():
                            para_no += 1
                            result.blocks.append(Block(text=text, block_type="table",
                                                       page=pno + 1, para_no=para_no,
                                                       table_meta={"rows": t.row_count,
                                                                   "cols": t.col_count}))
            except Exception:
                continue

        # 正文行 → 合并为段落 / 标题
        i = 0
        path: list[str] = []
        while i < len(lines):
            line = lines[i]
            if line["in_table"]:
                i += 1
                continue
            level = _heading_level(line["size"], body_median)
            is_bold = bool(line["flags"] & 16)
            is_short = len(line["text"]) <= 40
            looks_heading = (level > 0 and is_short) or (is_bold and is_short and
                                                         not line["text"].endswith(("。", "；", "，", ".", ",", ";")))
            if looks_heading:
                while path and path[-1] != "":
                    path.pop()
                path = path[: max(level - 1, 0)]
                path.append(line["text"])
                para_no += 1
                result.blocks.append(Block(text=line["text"], block_type="heading",
                                           page=pno + 1, para_no=para_no,
                                           heading_path=" / ".join(p for p in path if p)))
                i += 1
                continue
            # 普通段落：把同一段落的行合并（PDF 里每行一个 line）
            para_lines = [line["text"]]
            i += 1
            para_no += 1
            result.blocks.append(Block(text="".join(para_lines), block_type="body",
                                       page=pno + 1, para_no=para_no,
                                       heading_path=" / ".join(p for p in path if p)))

    # 4. 文本层判断
    if not result.has_text:
        result.needs_ocr = True
        result.warnings.append("PDF 无文本层，可能为扫描件")
    # 5. 标题提示：首块标题或最大字号短行
    for b in result.blocks[:5]:
        if b.block_type == "heading" and len(b.text) <= 60:
            result.title_hint = b.text
            break
    if not result.title_hint:
        bodies = [b for b in result.blocks if b.block_type == "body"]
        if bodies:
            result.title_hint = bodies[0].text[:60]
    doc.close()
    return result


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def _docx_paragraphs_with_styles(doc: Any):
    """python-docx 段落 + 样式名。"""
    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        yield para, style


def _docx_footnotes(doc: Any) -> dict[str, str]:
    """从 docx 包中读取脚注文本（python-docx 未暴露该 API）。"""
    try:
        from docx.oxml.ns import qn
        fn_part = next((p for p in doc.part.package.parts
                        if p.partname.endswith("footnotes.xml")), None)
        if fn_part is None:
            return {}
    except Exception:
        return {}
    notes: dict[str, str] = {}
    try:
        root = fn_part.element
        for fn in root.findall(qn("w:footnote")):
            fid = fn.get(qn("w:id"))
            texts = [t.text or "" for t in fn.iter(qn("w:t"))]
            if fid and fid not in ("-1", "0"):
                notes[fid] = "".join(texts).strip()
    except Exception:
        pass
    return notes


def parse_docx(path: Path) -> ParsedDoc:
    import docx as docx_mod

    try:
        doc = docx_mod.Document(str(path))
    except Exception as e:
        raise AppError("FILE_CORRUPT", message=str(e), stage="parse") from e

    result = ParsedDoc()
    para_no = 0
    path_stack: list[str] = []
    current_heading: dict[int, str] = {}
    section = "body"
    in_reference = False
    footnotes = _docx_footnotes(doc)

    for para, style in _docx_paragraphs_with_styles(doc):
        text = para.text.strip()
        if not text:
            continue
        level = 0
        m = re.match(r"Heading (\d)", style) or re.match(r"标题 ?(\d)", style)
        if m:
            level = int(m.group(1))
        elif style.lower() in ("title", "标题"):
            level = 0
            result.title_hint = result.title_hint or text
            para_no += 1
            result.blocks.append(Block(text=text, block_type="heading", para_no=para_no))
            continue

        if level > 0:
            current_heading = {k: v for k, v in current_heading.items() if k < level}
            current_heading[level] = text
            hp = " / ".join(current_heading[k] for k in sorted(current_heading))
            para_no += 1
            result.blocks.append(Block(text=text, block_type="heading", para_no=para_no,
                                       heading_path=hp))
            if "参考文献" in text or "references" in text.lower():
                in_reference = True
            continue

        hp = " / ".join(current_heading[k] for k in sorted(current_heading))
        # 脚注：从 XML 中取引用 id，附加为 footnote 块（法学文献脚注密集，可检索）
        try:
            qn = docx_mod.oxml.ns.qn
            fids = [r.get(qn("w:id"))
                    for r in para._p.findall(f".//{qn('w:footnoteReference')}")]
        except Exception:
            fids = []
        for fid in fids:
            fn_text = footnotes.get(str(fid), "")
            if fn_text:
                para_no += 1
                result.blocks.append(Block(text=fn_text, block_type="footnote",
                                           para_no=para_no, heading_path=hp))

        btype = "reference" if in_reference else "body"
        para_no += 1
        result.blocks.append(Block(text=text, block_type=btype, para_no=para_no, heading_path=hp))

    if not result.title_hint:
        for b in result.blocks:
            if b.block_type == "heading":
                result.title_hint = b.text
                break
    if not result.title_hint:
        result.title_hint = path.stem
    return result


# ---------------------------------------------------------------------------
# DOC（LibreOffice 转换）
# ---------------------------------------------------------------------------
def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for guess in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                  "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(guess).exists():
            return guess
    return None


def convert_doc(path: Path) -> Path:
    """.doc → .docx（本机 LibreOffice）；失败抛 FILE_DOC_CONVERT_FAILED。"""
    soffice = _find_soffice()
    if not soffice:
        raise AppError("FILE_DOC_CONVERT_FAILED",
                       message="LibreOffice not found",
                       suggestion="请用 Word/WPS 打开后另存为 .docx，或导出为 PDF 后重试",
                       stage="parse")
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            proc = subprocess.run(
                [soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir),
                 str(path)], capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired as e:
            raise AppError("FILE_DOC_CONVERT_FAILED", message="conversion timed out",
                           stage="parse") from e
        converted = out_dir / (path.stem + ".docx")
        if proc.returncode != 0 or not converted.exists():
            raise AppError("FILE_DOC_CONVERT_FAILED", message=proc.stderr[:500], stage="parse")
        # 复制到与源文件同目录的临时位置，供后续解析
        dest = path.with_suffix(".converted.docx")
        shutil.copyfile(converted, dest)
        return dest


def parse_file(path: Path) -> ParsedDoc:
    """按扩展名分发解析。"""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return parse_pdf(path)
    if ext == ".docx":
        return parse_docx(path)
    if ext == ".doc":
        converted = convert_doc(path)
        try:
            return parse_docx(converted)
        finally:
            converted.unlink(missing_ok=True)
    raise AppError("FILE_TYPE_UNSUPPORTED", stage="upload")
