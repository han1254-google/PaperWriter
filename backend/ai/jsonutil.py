"""JSON 提取与修复（§12：JSON 解析失败自动修复一次，仍失败展示原始输出）。"""
from __future__ import annotations

import json
import re

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> str:
    """从模型输出中提取 JSON 主体：去代码围栏、截取首尾花括号。"""
    s = _CODE_FENCE.sub("", text).strip()
    # 定位第一层完整 JSON（平衡花括号扫描，跳过字符串）
    depth = 0
    in_str = False
    escape = False
    start = -1
    for i, ch in enumerate(s):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return s[start : i + 1]
    return s


def _repair_once(s: str) -> str:
    """常见修复：尾逗号、单引号、注释。"""
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = re.sub(r"^\s*//.*$", "", s, flags=re.MULTILINE)
    s = s.replace("：", ":").replace("，", ",").replace("“", '"').replace("”", '"')
    return s


def parse_json_loose(text: str) -> tuple[object | None, str | None]:
    """尽力解析 JSON。返回 (结果, 修复说明)；失败返回 (None, 原始文本)。"""
    candidate = extract_json(text)
    for label, s in (("原始", candidate), ("修复", _repair_once(candidate))):
        try:
            return json.loads(s), None if label == "原始" else "已自动修复格式"
        except json.JSONDecodeError:
            continue
    return None, text
