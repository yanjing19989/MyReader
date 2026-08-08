import hashlib
import io
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .config import THUMB_DIR
from .db import connect, invalidate_thumbs, path_key
from .scanner import is_image, natural_key, normalize_path


@dataclass
class CoverSource:
    kind: str
    container: str
    entry: str | None
    display: str


def get_album(album_id: int):
    with connect() as conn:
        return conn.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()


def image_entries(row) -> list[str]:
    path = Path(row["path"])
    try:
        if row["type"] == "folder":
            return sorted(
                [item.name for item in path.iterdir() if item.is_file() and is_image(item.name)],
                key=natural_key,
            )
        with zipfile.ZipFile(path) as archive:
            return sorted(
                [item.filename for item in archive.infolist() if not item.is_dir() and is_image(item.filename)],
                key=natural_key,
            )
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return []


def child_albums(row) -> list[dict]:
    parent_key = path_key(row["path"])
    prefix = parent_key + "/"
    with connect() as conn:
        rows = conn.execute("SELECT * FROM albums WHERE type = 'folder'").fetchall()
    children = []
    for candidate in rows:
        candidate_key = path_key(candidate["path"])
        if not candidate_key.startswith(prefix):
            continue
        has_registered_parent = any(
            other["id"] != candidate["id"]
            and candidate_key.startswith(path_key(other["path"]) + "/")
            and path_key(other["path"]).startswith(prefix)
            for other in rows
        )
        if not has_registered_parent:
            children.append(candidate)
    return sorted(children, key=lambda item: natural_key(item["name"]))


def _file_source(path: Path, display: str | None = None) -> CoverSource | None:
    try:
        if not path.is_file() or not is_image(path.name):
            return None
        return CoverSource("file", str(path), None, display or normalize_path(path))
    except OSError:
        return None


def _zip_source(path: Path, entry: str) -> CoverSource | None:
    try:
        with zipfile.ZipFile(path) as archive:
            archive.getinfo(entry)
        display = f"{normalize_path(path)}!/{entry}"
        return CoverSource("zip", str(path), entry, display)
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        return None


def _source_for(row, seen: set[int] | None = None) -> CoverSource | None:
    seen = set() if seen is None else seen
    if row["id"] in seen:
        return None
    seen.add(row["id"])
    if row["cover_kind"] == "upload" and row["cover_ref"]:
        return _file_source(Path(row["cover_ref"]), row["cover_ref"])
    if row["cover_kind"] == "album" and row["cover_ref"]:
        try:
            child = get_album(int(row["cover_ref"]))
        except ValueError:
            child = None
        if child and any(item["id"] == child["id"] for item in child_albums(row)):
            source = _source_for(child, seen)
            if source:
                return source
    entries = image_entries(row)
    entry = row["cover_ref"] if row["cover_kind"] == "internal" else (entries[0] if entries else None)
    if entry and entry in entries:
        container = Path(row["path"])
        source = _file_source(container / entry) if row["type"] == "folder" else _zip_source(container, entry)
        if source:
            return source
    if row["type"] != "folder":
        return None
    prefix = path_key(row["path"]) + "/"
    with connect() as conn:
        descendants = [
            item for item in conn.execute("SELECT * FROM albums").fetchall()
            if path_key(item["path"]).startswith(prefix)
        ]
    descendants.sort(key=lambda item: natural_key(item["path"]))
    return next((source for item in descendants if (source := _source_for(item, seen.copy()))), None)


def resolve_cover(album_id: int) -> CoverSource | None:
    row = get_album(album_id)
    if not row:
        return None
    source = _source_for(row)
    if source:
        with connect() as conn:
            conn.execute("UPDATE albums SET cover_path = ? WHERE id = ?", (source.display, album_id))
    return source


def original_cover(album_id: int) -> tuple[str, Path | bytes] | None:
    source = resolve_cover(album_id)
    if not source:
        return None
    try:
        if source.kind == "zip":
            with zipfile.ZipFile(source.container) as archive:
                return source.entry or "cover", archive.read(source.entry)
        path = Path(source.container)
        return path.name, path
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        return None


def _load(source: CoverSource) -> Image.Image:
    if source.kind == "zip":
        with zipfile.ZipFile(source.container) as archive:
            data = archive.read(source.entry)
        image = Image.open(io.BytesIO(data))
    else:
        image = Image.open(source.container)
    image.seek(0)
    return ImageOps.exif_transpose(image).convert("RGBA")


def make_thumbnail(album_id: int, width: int, height: int, mode: str, quality: int) -> Path | None:
    row = get_album(album_id)
    if not row:
        return None
    # Album mtime and cover_version are the invalidation contract. A hit must not inspect the source.
    raw_key = f"{album_id}|{row['mtime']}|{row['cover_version']}|{width}|{height}|{mode}|{quality}"
    cache_key = hashlib.sha256(raw_key.encode()).hexdigest()
    output = THUMB_DIR / f"{cache_key}.webp"
    with connect() as conn:
        hit = conn.execute("SELECT file_path FROM thumbs WHERE cache_key = ?", (cache_key,)).fetchone()
        if hit and Path(hit["file_path"]).is_file():
            return Path(hit["file_path"])
    source = resolve_cover(album_id)
    if not source:
        return None
    try:
        image = _load(source)
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        if mode == "cover":
            result = ImageOps.fit(background, (width, height), Image.Resampling.LANCZOS)
        else:
            background.thumbnail((width, height), Image.Resampling.LANCZOS)
            result = background
        result.save(output, "WEBP", quality=quality, method=4)
    except (OSError, ValueError, zipfile.BadZipFile):
        output.unlink(missing_ok=True)
        return None
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO thumbs(album_id,cache_key,file_path,source_mtime) VALUES(?,?,?,?)",
            (album_id, cache_key, str(output), row["mtime"]),
        )
    return output


def set_cover(
    album_id: int, kind: str, entry: str | None = None, source_album_id: int | None = None
) -> bool:
    row = get_album(album_id)
    if not row:
        return False
    old_upload = row["cover_ref"] if row["cover_kind"] == "upload" else None
    if kind == "internal" and (not entry or entry not in image_entries(row)):
        raise ValueError("封面条目不存在")
    if kind == "album" and (
        source_album_id is None or not any(item["id"] == source_album_id for item in child_albums(row))
    ):
        raise ValueError("下级相册不存在")
    cover_ref = entry if kind == "internal" else str(source_album_id) if kind == "album" else None
    with connect() as conn:
        conn.execute(
            "UPDATE albums SET cover_kind=?,cover_ref=?,cover_path=NULL,cover_version=cover_version+1 WHERE id=?",
            (kind, cover_ref, album_id),
        )
        invalidate_thumbs(conn, album_id)
    if old_upload:
        try:
            Path(old_upload).unlink(missing_ok=True)
        except OSError:
            pass
    return True

