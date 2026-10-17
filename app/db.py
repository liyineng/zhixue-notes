from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    page INTEGER,
    text TEXT NOT NULL,
    lines_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_notebook ON notes(notebook_id);

CREATE TABLE IF NOT EXISTS chat_exchanges (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id),
    question TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('pending', 'complete', 'error')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_notebook ON chat_exchanges(notebook_id, sequence);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "temperature": 0.2,
    "max_tokens": 1400,
    "top_k": 6,
}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)
        conn.execute("""UPDATE chat_exchanges SET status='error', result_json=? WHERE status='pending'""",
                     (json.dumps({"detail": "上次生成未完成，请重新提问。"}, ensure_ascii=False),))
        conn.execute(
            "INSERT OR IGNORE INTO notebooks VALUES (?, ?, ?)",
            ("default", "我的课程", datetime.now(UTC).isoformat()),
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        if "notebook_id" not in columns:
            conn.execute("ALTER TABLE documents ADD COLUMN notebook_id TEXT REFERENCES notebooks(id)")
        conn.execute("UPDATE documents SET notebook_id = 'default' WHERE notebook_id IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_notebook ON documents(notebook_id)")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )


def list_documents(notebook_id: str | None = None) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE (? IS NULL OR notebook_id = ?) ORDER BY created_at DESC",
            (notebook_id, notebook_id),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def insert_document(
    document: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO documents(
                id, name, kind, path, size_bytes, page_count, chunk_count, created_at, notebook_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document["id"],
                document["name"],
                document["kind"],
                document["path"],
                document["size_bytes"],
                document["page_count"],
                len(chunks),
                document.get("created_at") or datetime.now(UTC).isoformat(),
                document.get("notebook_id", "default"),
            ),
        )
        conn.executemany(
            """
            INSERT INTO chunks(id, document_id, ordinal, page, text, lines_json)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk["id"],
                    document["id"],
                    chunk["ordinal"],
                    chunk.get("page"),
                    chunk["text"],
                    json.dumps(chunk.get("lines") or [], ensure_ascii=False),
                )
                for chunk in chunks
            ],
        )


def delete_document(document_id: str) -> dict[str, Any] | None:
    document = get_document(document_id)
    if not document:
        return None
    with connection() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return document


def all_chunks() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT
                chunks.id,
                chunks.document_id,
                chunks.ordinal,
                chunks.page,
                chunks.text,
                chunks.lines_json,
                documents.name AS document_name,
                documents.kind AS document_kind
            FROM chunks
            JOIN documents ON documents.id = chunks.document_id
            ORDER BY documents.created_at DESC, chunks.ordinal ASC
            """
        ).fetchall()
    chunks: list[dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row)
        try:
            item["lines"] = json.loads(item.pop("lines_json"))
        except json.JSONDecodeError:
            item["lines"] = []
        chunks.append(item)
    return chunks


def document_count() -> int:
    with connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
    return int(row["count"])


def get_settings() -> dict[str, Any]:
    with connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            settings[row["key"]] = row["value"]
    return settings


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    current = get_settings()
    merged = {**current, **values}
    with connection() as conn:
        conn.executemany(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [
                (key, json.dumps(value, ensure_ascii=False))
                for key, value in merged.items()
            ],
        )
    return merged


def list_notebooks() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("""
            SELECT n.*,
                (SELECT COUNT(*) FROM documents d WHERE d.notebook_id=n.id) AS document_count,
                (SELECT COUNT(*) FROM notes s WHERE s.notebook_id=n.id) AS note_count
            FROM notebooks n ORDER BY n.created_at, n.id
        """).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_notebook(notebook_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM notebooks WHERE id=?", (notebook_id,)).fetchone()
    return _row_to_dict(row) if row else None


def create_notebook(notebook_id: str, name: str) -> dict[str, Any]:
    with connection() as conn:
        conn.execute("INSERT INTO notebooks VALUES (?, ?, ?)",
                     (notebook_id, name, datetime.now(UTC).isoformat()))
    return get_notebook(notebook_id)  # type: ignore[return-value]


def rename_notebook(notebook_id: str, name: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE notebooks SET name=? WHERE id=?", (name, notebook_id))


def delete_empty_notebook(notebook_id: str) -> bool:
    with connection() as conn:
        result = conn.execute("""DELETE FROM notebooks WHERE id=? AND id!='default'
            AND NOT EXISTS (SELECT 1 FROM documents WHERE notebook_id=?)
            AND NOT EXISTS (SELECT 1 FROM notes WHERE notebook_id=?)
            AND NOT EXISTS (SELECT 1 FROM chat_exchanges WHERE notebook_id=?)""",
            (notebook_id, notebook_id, notebook_id, notebook_id))
        return result.rowcount == 1


def move_document(document_id: str, notebook_id: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE documents SET notebook_id=? WHERE id=?", (notebook_id, document_id))


def list_notes(notebook_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM notes WHERE notebook_id=? ORDER BY updated_at DESC, id",
                            (notebook_id,)).fetchall()
    return [_note_dict(row) for row in rows]


def _note_dict(row: sqlite3.Row) -> dict[str, Any]:
    note = _row_to_dict(row)
    note["citations"] = json.loads(note.pop("citations_json"))
    return note


def get_note(note_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    return _note_dict(row) if row else None


def save_note(note_id: str, notebook_id: str, title: str, content: str,
              citations: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    with connection() as conn:
        conn.execute("""INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title, content=excluded.content,
                updated_at=excluded.updated_at""",
            (note_id, notebook_id, title, content, json.dumps(citations, ensure_ascii=False), now, now))
    return get_note(note_id)  # type: ignore[return-value]


def delete_note(note_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM notes WHERE id=?", (note_id,))


def start_exchange(exchange_id: str, notebook_id: str, question: str) -> None:
    with connection() as conn:
        conn.execute("""INSERT INTO chat_exchanges(id, notebook_id, question, status, created_at)
                        VALUES (?, ?, ?, 'pending', ?)""",
                     (exchange_id, notebook_id, question, datetime.now(UTC).isoformat()))


def finish_exchange(exchange_id: str, result: dict[str, Any], *, error: bool = False) -> None:
    with connection() as conn:
        conn.execute("UPDATE chat_exchanges SET result_json=?, status=? WHERE id=?",
                     (json.dumps(result, ensure_ascii=False), "error" if error else "complete", exchange_id))


def list_exchanges(notebook_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM chat_exchanges WHERE notebook_id=? ORDER BY sequence",
                            (notebook_id,)).fetchall()
    result = []
    for row in rows:
        item = _row_to_dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        result.append(item)
    return result


def clear_exchanges(notebook_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM chat_exchanges WHERE notebook_id=?", (notebook_id,))
