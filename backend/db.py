"""SQLite 数据层（产品文档 §4）。

- WAL 模式 + 外键约束；每个操作短连接。
- meta 表记录 schema_version，按迁移列表升级。
- chunk_fts：FTS5 trigram 全文索引（external content，手动同步）。
- 所有时间戳为 ISO8601 字符串。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

from .errors import AppError
from .paths import get_data_dir

SCHEMA_VERSION = 4

SCHEMA_V1 = """
CREATE TABLE category (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER REFERENCES category(id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE document (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER REFERENCES category(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  doc_type TEXT,
  authors TEXT,
  publisher TEXT,
  published_at TEXT,
  language TEXT,
  file_name TEXT,
  file_path TEXT,
  file_hash TEXT,
  file_size INTEGER,
  page_count INTEGER,
  summary TEXT,
  metadata_json TEXT,
  status TEXT NOT NULL DEFAULT 'uploading',
  parse_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_document_status ON document(status);
CREATE INDEX idx_document_hash ON document(file_hash);

CREATE TABLE chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_type TEXT NOT NULL DEFAULT 'body',
  text TEXT NOT NULL,
  token_count INTEGER,
  page_start INTEGER, page_end INTEGER,
  para_start INTEGER, para_end INTEGER,
  heading_path TEXT,
  embedding_model TEXT,
  embedding_dim INTEGER,
  UNIQUE(document_id, chunk_index)
);
CREATE INDEX idx_chunk_doc ON chunk(document_id);

CREATE TABLE design (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  intent TEXT NOT NULL,
  description TEXT,
  overall_idea TEXT,
  total_word_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'draft',
  model TEXT,
  prompt_version TEXT,
  conflict_report_json TEXT,
  confirm_notes TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE design_chapter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  design_id INTEGER NOT NULL REFERENCES design(id) ON DELETE CASCADE,
  parent_id INTEGER REFERENCES design_chapter(id) ON DELETE CASCADE,
  number TEXT NOT NULL,
  title TEXT NOT NULL,
  idea TEXT,
  word_budget INTEGER NOT NULL DEFAULT 0,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_design_chapter_design ON design_chapter(design_id);

CREATE TABLE design_chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  design_id INTEGER NOT NULL REFERENCES design(id) ON DELETE CASCADE,
  design_chapter_id INTEGER REFERENCES design_chapter(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
  score REAL,
  query TEXT,
  selected INTEGER NOT NULL DEFAULT 1,
  UNIQUE(design_id, design_chapter_id, chunk_id)
);
CREATE INDEX idx_design_chunk_design ON design_chunk(design_id);

CREATE TABLE outline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  design_id INTEGER NOT NULL REFERENCES design(id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_outline_design ON outline(design_id);

CREATE TABLE outline_chapter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outline_id INTEGER NOT NULL REFERENCES outline(id) ON DELETE CASCADE,
  design_chapter_id INTEGER REFERENCES design_chapter(id) ON DELETE SET NULL,
  chapter_number TEXT NOT NULL,
  chapter_title TEXT NOT NULL,
  chapter_idea_snapshot TEXT,
  content TEXT,
  word_budget INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_outline_chapter_outline ON outline_chapter(outline_id);

CREATE TABLE outline_chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outline_chapter_id INTEGER NOT NULL REFERENCES outline_chapter(id) ON DELETE CASCADE,
  chunk_id INTEGER NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
  score REAL,
  selected INTEGER NOT NULL DEFAULT 1,
  UNIQUE(outline_chapter_id, chunk_id)
);

CREATE TABLE draft (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outline_id INTEGER NOT NULL REFERENCES outline(id) ON DELETE RESTRICT,
  title TEXT,
  abstract TEXT,
  keywords TEXT,
  author TEXT,
  school TEXT,
  publish_date TEXT,
  target_word_count INTEGER,
  status TEXT NOT NULL DEFAULT 'editing',
  citation_format TEXT NOT NULL DEFAULT 'gb7714',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_draft_outline ON draft(outline_id);

CREATE TABLE draft_chapter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL REFERENCES draft(id) ON DELETE CASCADE,
  outline_chapter_id INTEGER NOT NULL REFERENCES outline_chapter(id) ON DELETE CASCADE,
  content TEXT,
  word_count INTEGER NOT NULL DEFAULT 0,
  target_word_count INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_draft_chapter_draft ON draft_chapter(draft_id);

CREATE TABLE citation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_chapter_id INTEGER NOT NULL REFERENCES draft_chapter(id) ON DELETE CASCADE,
  chunk_id INTEGER REFERENCES chunk(id) ON DELETE SET NULL,
  marker TEXT NOT NULL,
  order_no INTEGER NOT NULL,
  quote TEXT,
  page_start INTEGER, page_end INTEGER,
  document_snapshot_json TEXT,
  valid INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_citation_chapter ON citation(draft_chapter_id);

CREATE TABLE notice (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_chapter_id INTEGER NOT NULL REFERENCES draft_chapter(id) ON DELETE CASCADE,
  notice_type TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE job (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  entity_type TEXT,
  entity_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  progress REAL NOT NULL DEFAULT 0,
  error_code TEXT,
  error_detail TEXT,
  trace_id TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  retry_of INTEGER,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_job_status ON job(status);

CREATE TABLE version (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_version_entity ON version(entity_type, entity_id);

-- FTS5 全文索引：trigram 分词，兼容中英文（§5.4 混合检索）
-- 仅索引正文；标题/作者检索走 document 表 LIKE（§5.5）
CREATE VIRTUAL TABLE chunk_fts USING fts5(
  text, content='chunk', content_rowid='id', tokenize='trigram'
);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, SCHEMA_V1),
    # v2：章节设计增加学科字段（理工版：学科决定提示词风格与章节预设）
    (2, "ALTER TABLE design ADD COLUMN discipline TEXT DEFAULT '法学';"),
    # v3：实验方案设计（理工版）：方案 + 溯源引用（[来源N] 绑定）
    (3, """
CREATE TABLE experiment_plan (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  research_idea TEXT NOT NULL,
  intent_json TEXT,
  content TEXT,
  model TEXT,
  prompt_version TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE plan_citation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL REFERENCES experiment_plan(id) ON DELETE CASCADE,
  source_no INTEGER NOT NULL,
  chunk_id INTEGER REFERENCES chunk(id) ON DELETE SET NULL,
  quote TEXT,
  verified INTEGER NOT NULL DEFAULT 1,
  data_note TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(plan_id, source_no)
);
CREATE INDEX idx_plan_citation_plan ON plan_citation(plan_id);
"""),
    # v4：方案保存生成时的证据清单（source_no → chunk），供重新校验精确恢复
    (4, "ALTER TABLE experiment_plan ADD COLUMN evidence_json TEXT;"),
]


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def db_path() -> Path:
    return get_data_dir() / "app.db"


_lock = threading.Lock()


@contextlib.contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """短连接；WAL + 外键。失败转 AppError。"""
    try:
        conn = sqlite3.connect(db_path(), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        yield conn
        conn.commit()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "disk i/o" in msg or "full" in msg or "no space" in msg:
            raise AppError("DB_DISK_FULL", message=str(e), stage="save", retryable=True) from e
        raise AppError("DB_WRITE_FAILED", message=str(e), stage="save", retryable=True) from e
    except sqlite3.Error as e:
        raise AppError("DB_WRITE_FAILED", message=str(e), stage="save", retryable=True) from e
    finally:
        try:
            conn.close()
        except Exception:
            pass


def init_db() -> None:
    """建表 + 迁移。启动时调用；失败抛出（进入启动自检失败页）。"""
    with _lock:
        db_path().parent.mkdir(parents=True, exist_ok=True)
        try:
            with connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                current = int(row["value"]) if row else 0
                if current == SCHEMA_VERSION:
                    return
                if current > SCHEMA_VERSION:
                    raise AppError("DB_MIGRATION_FAILED",
                                   message=f"db schema {current} > app {SCHEMA_VERSION}")
                for version, ddl in MIGRATIONS:
                    if version <= current:
                        continue
                    conn.executescript(ddl)
                    conn.execute(
                        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(version),),
                    )
                # FTS 脏位：新建库时置 1，首次检索前重建
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('fts_dirty', '1') "
                    "ON CONFLICT(key) DO NOTHING")
        except AppError:
            raise
        except sqlite3.Error as e:
            raise AppError("DB_MIGRATION_FAILED", message=str(e)) from e


# ---- 通用 helpers ----
def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def fetch_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with connect() as conn:
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or cur.rowcount


def insert(table: str, data: dict[str, Any]) -> int:
    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))
        return cur.lastrowid


def update(table: str, row_id: int, data: dict[str, Any]) -> None:
    if not data:
        return
    sets = ", ".join(f"{k}=?" for k in data)
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), row_id))


def delete(table: str, row_id: int) -> None:
    with connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))


def save_version(entity_type: str, entity_id: int, snapshot: dict[str, Any]) -> int:
    """版本快照：默认保留最近 20 个（§4.4 / §8.4）。"""
    from .config import get_config
    keep = get_config().get("retention.versions_keep", 20)
    vid = insert("version", {"entity_type": entity_type, "entity_id": entity_id,
                             "snapshot_json": json.dumps(snapshot, ensure_ascii=False),
                             "created_at": now()})
    with connect() as conn:
        conn.execute(
            "DELETE FROM version WHERE id IN ("
            " SELECT id FROM version WHERE entity_type=? AND entity_id=? "
            " ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (entity_type, entity_id, keep),
        )
    return vid


def get_json_col(row: dict[str, Any] | None, col: str) -> Any:
    if not row or not row.get(col):
        return None
    try:
        return json.loads(row[col])
    except (json.JSONDecodeError, TypeError):
        return None
