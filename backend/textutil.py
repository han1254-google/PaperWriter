"""文本截断工具：按句子/标点边界截断，避免把内容砍在句子中间。

AI 上下文与提示词中的截断必须用这里的方法；纯展示用途（UI 预览、
日志 detail）可以继续硬切。
"""
from __future__ import annotations

_SENT_ENDERS = "。！？!?；;\n"
_SOFT_BOUNDARY = "，,、：: 　\t"


def truncate_sentence(text: str, max_len: int, min_len: int = 0) -> str:
    """在 max_len 之前最近的句末标点处截断（保证内容完整成句）。

    找不到句末标点时退而求其次：逗号/冒号/空白边界；再不行才硬切。
    """
    if len(text) <= max_len:
        return text
    lo = max(0, min_len)
    for i in range(max_len - 1, lo - 1, -1):
        if text[i] in _SENT_ENDERS:
            return text[: i + 1].rstrip()
    for i in range(max_len - 1, 0, -1):
        if text[i] in _SOFT_BOUNDARY:
            return text[:i].rstrip()
    return text[:max_len]


def truncate_tail(text: str, max_len: int) -> str:
    """保留末尾 max_len 字，起点对齐到句末标点之后（避免从半句开始）。

    用于「上一节结尾」之类的衔接文本。
    """
    if len(text) <= max_len:
        return text
    start = len(text) - max_len
    for i in range(start, min(len(text), start + 80)):
        if text[i] in _SENT_ENDERS:
            return text[i + 1 :]
    return text[start:]
