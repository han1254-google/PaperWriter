"""配置管理：data/config.json 的加载、保存、遮蔽。

- API Key 明文保存在 data/config.json（单机单用户，产品文档 §10.2）。
- 环境变量 LAW_PAPER_DATA_DIR / DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL /
  QWEN_API_KEY / QWEN_BASE_URL / QWEN_EMBEDDING_MODEL 可覆盖（开发与测试用）。
- 所有对外展示走 redact()，密钥只显示末 4 位。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from . import APP_VERSION

_DEFAULTS: dict[str, Any] = {
    "app_version": APP_VERSION,
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "temperature": 0.4,
        # 注意：该模型带思维链（reasoning_content 先于 content 流式输出），
        # 输出预算需同时覆盖思考与正文，故默认给 16384。
        "max_output_tokens": 16384,
    },
    "qwen": {
        "api_key": "",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "embedding_model": "qwen3.7-text-embedding-flash",
    },
    "generation": {
        "timeout_s": 180,
        "max_retries": 2,
        "prompt_version": "v1",
    },
    "limits": {
        "max_file_mb": 100,
        "max_pages": 500,
        "max_batch_upload": 20,
        "candidate_pool_size": 100,
        "per_doc_max_chunks": 5,
        "design_in_chunks": 40,       # 章节设计入模片段上限
        "design_in_tokens": 60000,
        "outline_in_chunks": 25,
        "outline_in_tokens": 40000,
        "draft_in_chunks": 30,
        "draft_in_tokens": 60000,
        "outline_extra_candidates": 50,
        "draft_extra_candidates": 50,
    },
    "retention": {
        "versions_keep": 20,
        "logs_days": 14,
        "logs_total_mb": 500,
        "logs_file_mb": 20,
    },
    "privacy_consent": False,
    "setup_done": False,
}


def _env_overrides() -> dict[str, Any]:
    d = copy.deepcopy(_DEFAULTS)
    if os.environ.get("DEEPSEEK_API_KEY"):
        d["deepseek"]["api_key"] = os.environ["DEEPSEEK_API_KEY"]
    if os.environ.get("DEEPSEEK_BASE_URL"):
        d["deepseek"]["base_url"] = os.environ["DEEPSEEK_BASE_URL"]
    if os.environ.get("DEEPSEEK_MODEL"):
        d["deepseek"]["model"] = os.environ["DEEPSEEK_MODEL"]
    if os.environ.get("QWEN_API_KEY"):
        d["qwen"]["api_key"] = os.environ["QWEN_API_KEY"]
    if os.environ.get("QWEN_BASE_URL"):
        d["qwen"]["base_url"] = os.environ["QWEN_BASE_URL"]
    if os.environ.get("QWEN_EMBEDDING_MODEL"):
        d["qwen"]["embedding_model"] = os.environ["QWEN_EMBEDDING_MODEL"]
    return d


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """线程安全的配置读写。路径：<data_dir>/config.json"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "config.json"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = _env_overrides()
        self._loaded = False

    # ---- 加载 / 保存 ----
    def load(self) -> None:
        """加载配置文件；损坏时备份为 .bak 并以默认配置启动（§12）。"""
        with self._lock:
            self._loaded = True
            if not self.path.exists():
                self.save()
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = _deep_merge(_env_overrides(), raw)
            except (json.JSONDecodeError, OSError) as e:
                bak = self.path.with_suffix(".json.bak")
                try:
                    self.path.replace(bak)
                except OSError:
                    pass
                self._data = _env_overrides()
                self._data["_config_recovered"] = {"from_error": str(e), "backup": str(bak)}
                self.save()

    def save(self) -> None:
        from .errors import AppError
        with self._lock:
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                tmp.replace(self.path)
            except OSError as e:
                # 只读目录/被锁文件等：给用户可操作的提示，而不是笼统 500
                raise AppError("CFG_CONFIG_WRITE", message=str(e), stage="config") from e

    # ---- 访问 ----
    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node = self._data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            parts = key.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    # ---- 便捷 ----
    def key_status(self, provider: str) -> str:
        """configured / missing（日志只记录状态，不记录密钥）"""
        return "configured" if bool(self.get(f"{provider}.api_key")) else "missing"

    @staticmethod
    def mask_key(key: str) -> str:
        if not key:
            return ""
        return f"****{key[-4:]}" if len(key) >= 4 else "****"

    def redacted(self) -> dict[str, Any]:
        """对外展示用：密钥替换为 ****末4位。"""
        d = self.data
        if "deepseek" in d and d["deepseek"].get("api_key"):
            d["deepseek"]["api_key"] = self.mask_key(d["deepseek"]["api_key"])
        if "qwen" in d and d["qwen"].get("api_key"):
            d["qwen"]["api_key"] = self.mask_key(d["qwen"]["api_key"])
        return d

    # ---- 运行期动态覆盖（测试连接用，不落盘） ----
    def with_overrides(self, provider: str, *, api_key: str | None, base_url: str | None,
                       model: str | None) -> dict[str, Any]:
        d = self.data[provider]
        if api_key is not None:
            d["api_key"] = api_key
        if base_url is not None:
            d["base_url"] = base_url
        if model is not None:
            d[provider == "qwen" and "embedding_model" or "model"] = model
        return d


_config: Config | None = None


def get_config() -> Config:
    from .paths import get_data_dir  # 延迟导入避免循环
    global _config
    if _config is None:
        _config = Config(get_data_dir())
        _config.load()
    return _config


def set_data_dir_for_tests(path: Path) -> Config:
    """测试用：替换数据目录并重新加载。"""
    global _config
    _config = Config(path)
    _config.load()
    return _config
