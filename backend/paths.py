"""路径解析：项目根目录、data/ 目录。"""
from __future__ import annotations

import os
from pathlib import Path

# backend/paths.py → 项目根 = 上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """数据目录：优先环境变量 LAW_PAPER_DATA_DIR，其次项目根 data/。"""
    env = os.environ.get("LAW_PAPER_DATA_DIR")
    if env:
        return Path(env).resolve()
    return PROJECT_ROOT / "data"


def ensure_dirs() -> Path:
    d = get_data_dir()
    for sub in ("files", "logs", "logs/crash", "vectors", "exports"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d
