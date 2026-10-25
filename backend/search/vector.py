"""本地向量索引（产品文档 §5.4）。

优先 sqlite-vec（vec0 虚拟表，按嵌入模型分表）；
不可用时降级 NumPy 暴力检索（mmap 持久化，不依赖 C 扩展）。
换嵌入模型/维度 → 全量重建，旧索引在新索引就绪前继续可用。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from .. import db
from ..paths import get_data_dir

_log = logging.getLogger("backend")

try:
    import sqlite_vec  # noqa: F401
    HAVE_SQLITE_VEC = True
except ImportError:
    HAVE_SQLITE_VEC = False


def _table_slug(model: str) -> str:
    return "vec_" + re.sub(r"[^a-z0-9_]", "_", model.lower())[:40]


def _vec_dll_path() -> str | None:
    """sqlite-vec 扩展库路径（按平台）。"""
    import os
    import sqlite_vec
    base = os.path.dirname(sqlite_vec.__file__)
    candidates = ["vec0.dll", "vec0.dylib", "vec0.so"]
    for name in candidates:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


def _try_load_extension(conn: sqlite3.Connection) -> bool:
    """直接加载 vec0 扩展库（sqlite_vec.load() 在部分构建上报 not authorized，
    手动 enable → load_extension → disable 更可靠）。"""
    if not HAVE_SQLITE_VEC:
        return False
    try:
        import sqlite_vec  # noqa: F401
        dll = _vec_dll_path()
        if not dll:
            return False
        conn.enable_load_extension(True)
        conn.load_extension(dll)
        conn.enable_load_extension(False)
        return True
    except Exception as e:  # 平台不支持扩展加载 → 降级 NumPy
        _log.warning("sqlite-vec unavailable: %s", e)
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
        return False


def _numpy_store_path(model: str) -> Path:
    return get_data_dir() / "vectors" / f"{_table_slug(model)}.npz"


class VectorIndex:
    """统一接口：store(vectors, chunk_ids) / query(vector, k, filter_chunk_ids) / delete_doc。"""

    def __init__(self, model: str) -> None:
        self.model = model
        self.dim: int | None = None
        self.backend = "sqlite-vec" if HAVE_SQLITE_VEC else "numpy"

    # ---- 初始化 ----
    def ensure(self, dim: int) -> None:
        """确定维度并建表。换维度会重建（调用方负责数据迁移）。"""
        if self.dim == dim:
            return
        if self.dim is None:
            self.discover()  # 重启后避免误删既有索引表
        if self.dim == dim:
            return
        with db.connect() as conn:
            if _try_load_extension(conn):
                self.backend = "sqlite-vec"
                if self.dim is not None:  # 维度变化 → 重建该模型的索引表
                    _log.warning("embedding dim changed %s -> %s, rebuilding vector table %s",
                                 self.dim, dim, _table_slug(self.model))
                    conn.execute(f"DROP TABLE IF EXISTS {_table_slug(self.model)}")
                conn.execute(
                    f"CREATE VIRTUAL TABLE {_table_slug(self.model)} USING vec0("
                    f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{dim}])")
                self.dim = dim
                return
        self.backend = "numpy"
        self.dim = dim

    def discover(self) -> None:
        """进程重启后从数据库恢复维度与后端类型（索引数据持久化在库中）。"""
        if self.dim is not None:
            return
        row = db.fetch_one(
            "SELECT embedding_dim FROM chunk WHERE embedding_model=? "
            "AND embedding_dim IS NOT NULL LIMIT 1", (self.model,))
        if not row:
            return
        dim = int(row["embedding_dim"])
        with db.connect() as conn:
            if _try_load_extension(conn):
                self.backend = "sqlite-vec"
                self.dim = dim
                return
        # numpy 降级：存在对应文件才生效
        if _numpy_store_path(self.model).exists():
            self.backend = "numpy"
            self.dim = dim

    # ---- 写入 ----
    def store(self, vectors: list[list[float]], chunk_ids: list[int]) -> None:
        if not vectors:
            return
        assert self.dim is not None
        if self.backend == "sqlite-vec":
            with db.connect() as conn:
                if _try_load_extension(conn):
                    conn.executemany(
                        f"INSERT INTO {_table_slug(self.model)}(chunk_id, embedding) "
                        f"VALUES (?, ?)",
                        [(cid, sqlite_vec.serialize_float32(v)) for cid, v in zip(chunk_ids, vectors)])
                    return
                # 扩展加载突然失败 → 切回 numpy
                self.backend = "numpy"
        self._numpy_store(vectors, chunk_ids)

    def _numpy_store(self, vectors: list[list[float]], chunk_ids: list[int]) -> None:
        import numpy as np
        path = _numpy_store_path(self.model)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_ids: list[int] = []
        existing_vecs = None
        if path.exists():
            old = np.load(path, allow_pickle=False)
            existing_ids = old["ids"].tolist()
            existing_vecs = old["vecs"]
        mat = np.asarray(vectors, dtype=np.float32)
        if existing_vecs is not None:
            all_vecs = np.vstack([existing_vecs, mat])
            all_ids = existing_ids + list(chunk_ids)
        else:
            all_vecs, all_ids = mat, list(chunk_ids)
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, ids=np.asarray(all_ids, dtype=np.int64), vecs=all_vecs)
        tmp.replace(path)
        self._numpy_cache = None

    def _numpy_load(self):
        import numpy as np
        cache = getattr(self, "_numpy_cache", None)
        if cache is None:
            path = _numpy_store_path(self.model)
            if not path.exists():
                return None
            data = np.load(path, allow_pickle=False, mmap_mode="r")
            cache = (data["ids"], data["vecs"])
            self._numpy_cache = cache
        return cache

    # ---- 查询 ----
    def query(self, vector: list[float], k: int,
              filter_chunk_ids: list[int] | None = None) -> list[tuple[int, float]]:
        """返回 [(chunk_id, distance)]，按距离升序。"""
        if self.dim is None:
            self.discover()
        if self.dim is None:
            return []
        if self.backend == "sqlite-vec":
            return self._query_vec(vector, k, filter_chunk_ids)
        return self._query_numpy(vector, k, filter_chunk_ids)

    def _query_vec(self, vector: list[float], k: int,
                   filter_ids: list[int] | None) -> list[tuple[int, float]]:
        import sqlite_vec
        # vec0 只支持 rowid IN 过滤：先放宽取 5k，再过滤
        kk = k * 5 if filter_ids else k
        try:
            with db.connect() as conn:
                if not _try_load_extension(conn):
                    self.backend = "numpy"
                    return self._query_numpy(vector, k, filter_ids)
                rows = conn.execute(
                    f"SELECT chunk_id, distance FROM {_table_slug(self.model)} "
                    f"WHERE embedding MATCH ? AND k = ?",
                    (sqlite_vec.serialize_float32(vector), kk)).fetchall()
        except sqlite3.OperationalError:
            self.backend = "numpy"
            return self._query_numpy(vector, k, filter_ids)
        out = [(r["chunk_id"], r["distance"]) for r in rows]
        if filter_ids is not None:
            allowed = set(filter_ids)
            out = [(cid, d) for cid, d in out if cid in allowed]
        return out[:k]

    def _query_numpy(self, vector: list[float], k: int,
                     filter_ids: list[int] | None) -> list[tuple[int, float]]:
        import numpy as np
        loaded = self._numpy_load()
        if loaded is None:
            return []
        ids, vecs = loaded
        q = np.asarray(vector, dtype=np.float32)[None, :]
        sims = vecs @ q.T
        sims = np.asarray(sims).reshape(-1)
        if filter_ids is not None:
            allowed = set(filter_ids)
            mask = np.array([i in allowed for i in ids.tolist()], dtype=bool)
        else:
            mask = np.ones(len(sims), dtype=bool)
        idx = np.argsort(-sims)[: k * 4]
        out = []
        for i in idx:
            if mask[i]:
                out.append((int(ids[i]), float(1.0 - sims[i])))
            if len(out) >= k:
                break
        return out

    # ---- 删除 ----
    def delete_chunks(self, chunk_ids: list[int]) -> None:
        if not chunk_ids:
            return
        if self.backend == "sqlite-vec":
            try:
                with db.connect() as conn:
                    if _try_load_extension(conn):
                        conn.executemany(
                            f"DELETE FROM {_table_slug(self.model)} WHERE chunk_id = ?",
                            [(cid,) for cid in chunk_ids])
                        return
            except sqlite3.Error:
                self.backend = "numpy"
        import numpy as np
        loaded = self._numpy_load()
        if loaded is None:
            return
        ids, vecs = loaded
        drop = set(chunk_ids)
        keep = [i for i, cid in enumerate(ids.tolist()) if cid not in drop]
        if len(keep) == len(ids):
            return
        path = _numpy_store_path(self.model)
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, ids=np.asarray(ids[keep]), vecs=vecs[keep])
        tmp.replace(path)
        self._numpy_cache = None

    def stats(self) -> dict[str, Any]:
        return {"backend": self.backend, "model": self.model, "dim": self.dim}


# 嵌入模型 → 索引实例缓存（换模型自动建新索引）
_indexes: dict[str, VectorIndex] = {}


def get_index(model: str) -> VectorIndex:
    if model not in _indexes:
        _indexes[model] = VectorIndex(model)
    return _indexes[model]


def reset_indexes() -> None:
    """清空数据后调用：释放进程内索引缓存，避免维度/向量残留。"""
    _indexes.clear()


def all_models() -> list[str]:
    """库中出现过的嵌入模型（按 chunk.embedding_model 去重）。"""
    rows = db.fetch_all("SELECT DISTINCT embedding_model FROM chunk WHERE embedding_model IS NOT NULL")
    return [r["embedding_model"] for r in rows]
