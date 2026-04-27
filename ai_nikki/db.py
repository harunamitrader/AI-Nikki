from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .util import ensure_directory, ensure_parent, month_key_for_day_key, month_key_for_timestamp, now_utc_iso


STATE_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  root_path TEXT,
  extractor_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_files (
  source_id TEXT NOT NULL,
  path TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  last_run_id TEXT,
  last_ingested_at TEXT,
  status TEXT NOT NULL,
  PRIMARY KEY (source_id, path)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  message TEXT,
  discovered_files INTEGER NOT NULL DEFAULT 0,
  processed_files INTEGER NOT NULL DEFAULT 0,
  skipped_files INTEGER NOT NULL DEFAULT 0,
  sessions_upserted INTEGER NOT NULL DEFAULT 0,
  messages_upserted INTEGER NOT NULL DEFAULT 0,
  actions_upserted INTEGER NOT NULL DEFAULT 0
);
"""


DATA_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
  session_uid TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_session_id TEXT NOT NULL,
  thread_id TEXT,
  workspace_key TEXT,
  workspace_path TEXT,
  title TEXT,
  ai_name TEXT NOT NULL,
  model TEXT,
  started_at TEXT,
  ended_at TEXT,
  metadata_json TEXT,
  raw_payload TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  message_uid TEXT PRIMARY KEY,
  session_uid TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_message_id TEXT NOT NULL,
  ts TEXT,
  day_key TEXT,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL,
  model TEXT,
  content_text TEXT,
  tokens_input INTEGER,
  tokens_output INTEGER,
  tokens_total INTEGER,
  is_final INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT,
  raw_payload TEXT
);

CREATE TABLE IF NOT EXISTS actions (
  action_uid TEXT PRIMARY KEY,
  session_uid TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_action_id TEXT NOT NULL,
  ts TEXT,
  day_key TEXT,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  name TEXT,
  status TEXT,
  summary TEXT,
  arguments_json TEXT,
  result_json TEXT,
  metadata_json TEXT,
  raw_payload TEXT
);

CREATE TABLE IF NOT EXISTS diary_posts (
  day_key TEXT NOT NULL,
  post_index INTEGER NOT NULL,
  kind TEXT NOT NULL,
  ai_name TEXT,
  tag TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (day_key, post_index)
);

CREATE INDEX IF NOT EXISTS idx_messages_day_key ON messages(day_key, ts, seq);
CREATE INDEX IF NOT EXISTS idx_messages_session_uid ON messages(session_uid, seq);
CREATE INDEX IF NOT EXISTS idx_actions_day_key ON actions(day_key, ts, seq);
CREATE INDEX IF NOT EXISTS idx_actions_session_uid ON actions(session_uid, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_source_session ON sessions(source_id, source_session_id);
CREATE INDEX IF NOT EXISTS idx_diary_posts_ai_kind_day ON diary_posts(ai_name, kind, day_key);
"""


def _connect_sqlite(path: Path, schema: str) -> sqlite3.Connection:
    ensure_parent(path)
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000;")
    connection.executescript(schema)
    return connection


def _state_db_path(db_dir: str) -> Path:
    return Path(db_dir) / "_state.sqlite"


def connect_state_db(db_dir: str) -> sqlite3.Connection:
    db_path = _state_db_path(db_dir)
    ensure_directory(db_path.parent)
    return _connect_sqlite(db_path, STATE_SCHEMA)


def month_db_path(db_dir: str, month_key: str) -> Path:
    return Path(db_dir) / f"{month_key}.sqlite"


def connect_month_db(db_dir: str, month_key: str) -> sqlite3.Connection:
    db_path = month_db_path(db_dir, month_key)
    ensure_directory(db_path.parent)
    return _connect_sqlite(db_path, DATA_SCHEMA)


def connect_day_db(db_dir: str, day_key: str) -> sqlite3.Connection:
    return connect_month_db(db_dir, month_key_for_day_key(day_key))


def month_db_paths(db_dir: str) -> list[Path]:
    base = Path(db_dir)
    if not base.exists():
        return []
    return sorted(path for path in base.glob("????-??.sqlite") if path.is_file())


def month_keys(db_dir: str) -> list[str]:
    return [path.stem for path in month_db_paths(db_dir)]


def available_days(db_dir: str) -> list[str]:
    days: set[str] = set()
    for db_path in month_db_paths(db_dir):
        connection = _connect_sqlite(db_path, DATA_SCHEMA)
        try:
            rows = connection.execute(
                """
                SELECT day_key
                FROM (
                  SELECT day_key FROM messages WHERE day_key IS NOT NULL
                  UNION
                  SELECT day_key FROM actions WHERE day_key IS NOT NULL
                )
                """
            ).fetchall()
            days.update(row[0] for row in rows if row[0])
        finally:
            connection.close()
    return sorted(days)


def touched_month_keys(messages: list[dict[str, Any]], actions: list[dict[str, Any]], fallback_ts: str | None = None) -> list[str]:
    months: set[str] = set()
    for record in messages:
        month_key = month_key_for_day_key(record.get("day_key")) if record.get("day_key") else month_key_for_timestamp(record.get("ts"))
        if month_key:
            months.add(month_key)
    for record in actions:
        month_key = month_key_for_day_key(record.get("day_key")) if record.get("day_key") else month_key_for_timestamp(record.get("ts"))
        if month_key:
            months.add(month_key)
    if not months and fallback_ts:
        month_key = month_key_for_timestamp(fallback_ts)
        if month_key:
            months.add(month_key)
    return sorted(months)


def upsert_source(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    display_name: str,
    source_type: str,
    root_path: str,
    extractor_version: str,
) -> None:
    now = now_utc_iso()
    connection.execute(
        """
        INSERT INTO sources (source_id, display_name, source_type, root_path, extractor_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          display_name = excluded.display_name,
          source_type = excluded.source_type,
          root_path = excluded.root_path,
          extractor_version = excluded.extractor_version,
          updated_at = excluded.updated_at
        """,
        (source_id, display_name, source_type, root_path, extractor_version, now, now),
    )


def get_file_state(connection: sqlite3.Connection, source_id: str, path: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM source_files WHERE source_id = ? AND path = ?",
        (source_id, path),
    ).fetchone()


def update_file_state(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    path: str,
    size: int,
    mtime_ns: int,
    fingerprint: str,
    run_id: str,
    status: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_files (source_id, path, size, mtime_ns, fingerprint, last_run_id, last_ingested_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, path) DO UPDATE SET
          size = excluded.size,
          mtime_ns = excluded.mtime_ns,
          fingerprint = excluded.fingerprint,
          last_run_id = excluded.last_run_id,
          last_ingested_at = excluded.last_ingested_at,
          status = excluded.status
        """,
        (source_id, path, size, mtime_ns, fingerprint, run_id, now_utc_iso(), status),
    )


def begin_run(connection: sqlite3.Connection, run_id: str, mode: str) -> None:
    connection.execute(
        """
        INSERT INTO ingestion_runs (run_id, mode, started_at, status)
        VALUES (?, ?, ?, ?)
        """,
        (run_id, mode, now_utc_iso(), "running"),
    )


def finish_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    message: str,
    stats: dict[str, int],
) -> None:
    connection.execute(
        """
        UPDATE ingestion_runs
        SET finished_at = ?,
            status = ?,
            message = ?,
            discovered_files = ?,
            processed_files = ?,
            skipped_files = ?,
            sessions_upserted = ?,
            messages_upserted = ?,
            actions_upserted = ?
        WHERE run_id = ?
        """,
        (
            now_utc_iso(),
            status,
            message,
            stats.get("discovered_files", 0),
            stats.get("processed_files", 0),
            stats.get("skipped_files", 0),
            stats.get("sessions_upserted", 0),
            stats.get("messages_upserted", 0),
            stats.get("actions_upserted", 0),
            run_id,
        ),
    )


def _executemany(connection: sqlite3.Connection, query: str, rows: Iterable[tuple[Any, ...]]) -> None:
    batch = list(rows)
    if batch:
        connection.executemany(query, batch)


def upsert_sessions(connection: sqlite3.Connection, sessions: list[dict[str, Any]]) -> None:
    _executemany(
        connection,
        """
        INSERT INTO sessions (
          session_uid, source_id, source_session_id, thread_id, workspace_key, workspace_path, title,
          ai_name, model, started_at, ended_at, metadata_json, raw_payload, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_uid) DO UPDATE SET
          thread_id = excluded.thread_id,
          workspace_key = excluded.workspace_key,
          workspace_path = excluded.workspace_path,
          title = excluded.title,
          ai_name = excluded.ai_name,
          model = COALESCE(excluded.model, sessions.model),
          started_at = COALESCE(sessions.started_at, excluded.started_at),
          ended_at = COALESCE(excluded.ended_at, sessions.ended_at),
          metadata_json = excluded.metadata_json,
          raw_payload = excluded.raw_payload,
          updated_at = excluded.updated_at
        """,
        (
            (
                row["session_uid"],
                row["source_id"],
                row["source_session_id"],
                row.get("thread_id"),
                row.get("workspace_key"),
                row.get("workspace_path"),
                row.get("title"),
                row["ai_name"],
                row.get("model"),
                row.get("started_at"),
                row.get("ended_at"),
                row.get("metadata_json"),
                row.get("raw_payload"),
                row["updated_at"],
            )
            for row in sessions
        ),
    )


def upsert_messages(connection: sqlite3.Connection, messages: list[dict[str, Any]]) -> None:
    _executemany(
        connection,
        """
        INSERT INTO messages (
          message_uid, session_uid, source_id, source_message_id, ts, day_key, seq, role, model, content_text,
          tokens_input, tokens_output, tokens_total, is_final, metadata_json, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_uid) DO UPDATE SET
          ts = excluded.ts,
          day_key = excluded.day_key,
          seq = excluded.seq,
          role = excluded.role,
          model = COALESCE(excluded.model, messages.model),
          content_text = excluded.content_text,
          tokens_input = excluded.tokens_input,
          tokens_output = excluded.tokens_output,
          tokens_total = excluded.tokens_total,
          is_final = excluded.is_final,
          metadata_json = excluded.metadata_json,
          raw_payload = excluded.raw_payload
        """,
        (
            (
                row["message_uid"],
                row["session_uid"],
                row["source_id"],
                row["source_message_id"],
                row.get("ts"),
                row.get("day_key"),
                row["seq"],
                row["role"],
                row.get("model"),
                row.get("content_text"),
                row.get("tokens_input"),
                row.get("tokens_output"),
                row.get("tokens_total"),
                row.get("is_final", 1),
                row.get("metadata_json"),
                row.get("raw_payload"),
            )
            for row in messages
        ),
    )


def upsert_actions(connection: sqlite3.Connection, actions: list[dict[str, Any]]) -> None:
    _executemany(
        connection,
        """
        INSERT INTO actions (
          action_uid, session_uid, source_id, source_action_id, ts, day_key, seq, kind, name, status, summary,
          arguments_json, result_json, metadata_json, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(action_uid) DO UPDATE SET
          ts = excluded.ts,
          day_key = excluded.day_key,
          seq = excluded.seq,
          kind = excluded.kind,
          name = excluded.name,
          status = excluded.status,
          summary = excluded.summary,
          arguments_json = excluded.arguments_json,
          result_json = excluded.result_json,
          metadata_json = excluded.metadata_json,
          raw_payload = excluded.raw_payload
        """,
        (
            (
                row["action_uid"],
                row["session_uid"],
                row["source_id"],
                row["source_action_id"],
                row.get("ts"),
                row.get("day_key"),
                row["seq"],
                row["kind"],
                row.get("name"),
                row.get("status"),
                row.get("summary"),
                row.get("arguments_json"),
                row.get("result_json"),
                row.get("metadata_json"),
                row.get("raw_payload"),
            )
            for row in actions
        ),
    )


def upsert_diary_posts(connection: sqlite3.Connection, day_key: str, posts: list[dict[str, Any]]) -> None:
    connection.executemany(
        """
        INSERT INTO diary_posts (
          day_key, post_index, kind, ai_name, tag, char_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(day_key, post_index) DO UPDATE SET
          kind = excluded.kind,
          ai_name = excluded.ai_name,
          tag = excluded.tag,
          char_count = excluded.char_count,
          created_at = excluded.created_at
        """,
        [
            (
                day_key,
                post["post_index"],
                post["kind"],
                post["ai_name"],
                post["tag"],
                post["char_count"],
                now_utc_iso(),
            )
            for post in posts
        ],
    )
