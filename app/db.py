import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH, ensure_dirs


PUBLIC_FIELDS = "id, type, path, name, mtime, size, file_count, added_at, cover_path"


@contextmanager
def connect():
    ensure_dirs()
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
    with connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY,
                type TEXT NOT NULL CHECK(type IN ('folder', 'zip')),
                path TEXT NOT NULL,
                path_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                mtime INTEGER NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                file_count INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                cover_kind TEXT NOT NULL DEFAULT 'default',
                cover_ref TEXT,
                cover_path TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_albums_path ON albums(path);
            CREATE TABLE IF NOT EXISTS thumbs (
                id INTEGER PRIMARY KEY,
                album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
                cache_key TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                source_sig TEXT NOT NULL,
                last_accessed TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_thumbs_album ON thumbs(album_id);
            """
        )


def album_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in PUBLIC_FIELDS.split(", ")}


def invalidate_thumbs(conn: sqlite3.Connection, album_id: int) -> None:
    files = conn.execute("SELECT file_path FROM thumbs WHERE album_id = ?", (album_id,)).fetchall()
    conn.execute("DELETE FROM thumbs WHERE album_id = ?", (album_id,))
    for row in files:
        try:
            Path(row["file_path"]).unlink(missing_ok=True)
        except OSError:
            pass


def delete_album(conn: sqlite3.Connection, album_id: int) -> None:
    invalidate_thumbs(conn, album_id)
    row = conn.execute(
        "SELECT cover_kind, cover_ref FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    if row and row["cover_kind"] == "upload" and row["cover_ref"]:
        try:
            Path(row["cover_ref"]).unlink(missing_ok=True)
        except OSError:
            pass


def path_key(path: str) -> str:
    return os.path.normcase(path).replace("\\", "/")

