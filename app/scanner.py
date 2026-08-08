import os
import re
import zipfile
from pathlib import Path
from typing import Callable

from .db import connect, delete_album, invalidate_thumbs, path_key


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
Emit = Callable[[dict], None]


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def normalize_path(value: str | Path) -> str:
    path = Path(value).expanduser().resolve(strict=False).as_posix()
    anchor = Path(path).anchor.replace("\\", "/")
    return path if path == anchor else path.rstrip("/")


def is_image(name: str) -> bool:
    return Path(name).suffix.casefold() in IMAGE_EXTS


def zip_stats(path: Path) -> tuple[int, int]:
    with zipfile.ZipFile(path) as archive:
        files = [item for item in archive.infolist() if not item.is_dir() and is_image(item.filename)]
    return len(files), sum(item.file_size for item in files)


def _upsert(kind: str, path: Path, count: int, size: int) -> tuple[int, bool]:
    normalized = normalize_path(path)
    mtime = int(path.stat().st_mtime)
    name = path.name or normalized
    with connect() as conn:
        old = conn.execute("SELECT * FROM albums WHERE path_key = ?", (path_key(normalized),)).fetchone()
        if old:
            changed = old["mtime"] != mtime or old["type"] != kind
            aggregate_changed = old["file_count"] != count or old["size"] != size
            conn.execute(
                "UPDATE albums SET type=?, path=?, name=?, mtime=?, size=?, file_count=?, "
                "cover_path=CASE WHEN ? THEN NULL ELSE cover_path END WHERE id=?",
                (kind, normalized, name, mtime, size, count, changed, old["id"]),
            )
            if changed or aggregate_changed:
                invalidate_thumbs(conn, old["id"])
            return old["id"], changed or aggregate_changed
        cursor = conn.execute(
            "INSERT INTO albums(type,path,path_key,name,mtime,size,file_count) VALUES(?,?,?,?,?,?,?)",
            (kind, normalized, path_key(normalized), name, mtime, size, count),
        )
        return cursor.lastrowid, True


def _scan_zip(path: Path, emit: Emit) -> tuple[int, int, int] | None:
    try:
        count, content_size = zip_stats(path)
        if not count:
            emit({"type": "skipped", "path": normalize_path(path), "reason": "ZIP 中没有支持的图片"})
            return None
        album_id, changed = _upsert("zip", path, count, path.stat().st_size)
        return album_id, count, path.stat().st_size
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        emit({"type": "skipped", "path": normalize_path(path), "reason": f"无法读取 ZIP：{exc}"})
        return None


def _scan_folder(path: Path, recursive: bool, emit: Emit) -> tuple[int, int, int] | None:
    count = size = 0
    try:
        entries = sorted(os.scandir(path), key=lambda item: natural_key(item.name))
    except OSError as exc:
        emit({"type": "skipped", "path": normalize_path(path), "reason": f"无法读取目录：{exc}"})
        return None

    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            item = Path(entry.path)
            if entry.is_file(follow_symlinks=False) and is_image(entry.name):
                count += 1
                size += entry.stat(follow_symlinks=False).st_size
            elif entry.is_file(follow_symlinks=False) and item.suffix.casefold() == ".zip":
                result = _scan_zip(item, emit)
                if result:
                    count += result[1]
                    size += result[2]
            elif recursive and entry.is_dir(follow_symlinks=False):
                result = _scan_folder(item, True, emit)
                if result:
                    count += result[1]
                    size += result[2]
        except OSError as exc:
            emit({"type": "skipped", "path": normalize_path(entry.path), "reason": str(exc)})

    if not count:
        return None
    album_id, changed = _upsert("folder", path, count, size)
    return album_id, count, size


def scan_paths(paths: list[str], recursive: bool, emit: Emit) -> dict:
    registered: set[int] = set()
    skipped = 0

    def relay(event: dict) -> None:
        nonlocal skipped
        if event["type"] == "skipped":
            skipped += 1
        emit(event)

    for raw in paths:
        normalized = normalize_path(raw)
        relay({"type": "started", "path": normalized})
        path = Path(normalized)
        result = None
        if path.is_dir():
            result = _scan_folder(path, recursive, relay)
        elif path.is_file() and path.suffix.casefold() == ".zip":
            result = _scan_zip(path, relay)
        else:
            relay({"type": "skipped", "path": normalized, "reason": "路径不存在或类型不受支持"})
        if result:
            registered.add(result[0])
    summary = {"registered": len(registered), "skipped": skipped}
    emit({"type": "completed", **summary})
    return summary


def refresh_all() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT id,type,path,mtime FROM albums ORDER BY length(path)").fetchall()
    removed: list[int] = []
    changed: list[str] = []
    changed_folders: list[str] = []
    for row in rows:
        path = Path(row["path"])
        valid = path.is_dir() if row["type"] == "folder" else path.is_file()
        if not valid:
            with connect() as conn:
                delete_album(conn, row["id"])
            removed.append(row["id"])
            continue
        try:
            is_changed = int(path.stat().st_mtime) != row["mtime"]
        except OSError:
            is_changed = False
        if is_changed:
            key = path_key(row["path"])
            if not any(key.startswith(parent + "/") for parent in changed_folders):
                changed.append(row["path"])
                if row["type"] == "folder":
                    changed_folders.append(key)
    if changed:
        scan_paths(changed, True, lambda _: None)
    return {"checked": len(rows), "removed": len(removed), "removed_ids": removed}

