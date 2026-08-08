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
    signature: str


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


def _file_source(path: Path, display: str | None = None) -> CoverSource | None:
    try:
        stat = path.stat()
        if not path.is_file() or not is_image(path.name):
            return None
        return CoverSource(
            "file", str(path), None, display or normalize_path(path),
            f"file:{normalize_path(path)}:{stat.st_mtime_ns}:{stat.st_size}",
        )
    except OSError:
        return None


def _zip_source(path: Path, entry: str) -> CoverSource | None:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo(entry)
        stat = path.stat()
        display = f"{normalize_path(path)}!/{entry}"
        return CoverSource(
            "zip", str(path), entry, display,
            f"zip:{normalize_path(path)}:{stat.st_mtime_ns}:{stat.st_size}:{entry}:{info.CRC}",
        )
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError):
        return None


def _direct_source(row) -> CoverSource | None:
    if row["cover_kind"] == "upload" and row["cover_ref"]:
        return _file_source(Path(row["cover_ref"]), row["cover_ref"])
    entries = image_entries(row)
    entry = row["cover_ref"] if row["cover_kind"] == "internal" else (entries[0] if entries else None)
    if not entry or entry not in entries:
        return None
    container = Path(row["path"])
    return _file_source(container / entry) if row["type"] == "folder" else _zip_source(container, entry)


def resolve_cover(album_id: int) -> CoverSource | None:
    row = get_album(album_id)
    if not row:
        return None
    source = _direct_source(row)
    if not source and row["type"] == "folder":
        prefix = path_key(row["path"]) + "/"
        with connect() as conn:
            descendants = [
                item for item in conn.execute("SELECT * FROM albums").fetchall()
                if path_key(item["path"]).startswith(prefix)
            ]
        descendants.sort(key=lambda item: natural_key(item["path"]))
        source = next((candidate for item in descendants if (candidate := _direct_source(item))), None)
    if source:
        with connect() as conn:
            conn.execute("UPDATE albums SET cover_path = ? WHERE id = ?", (source.display, album_id))
    return source


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
    source = resolve_cover(album_id)
    if not source:
        return None
    raw_key = f"{album_id}|{source.signature}|{width}|{height}|{mode}|{quality}"
    cache_key = hashlib.sha256(raw_key.encode()).hexdigest()
    output = THUMB_DIR / f"{cache_key}.webp"
    with connect() as conn:
        hit = conn.execute("SELECT file_path FROM thumbs WHERE cache_key = ?", (cache_key,)).fetchone()
        if hit and Path(hit["file_path"]).is_file():
            conn.execute(
                "UPDATE thumbs SET last_accessed=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE cache_key=?",
                (cache_key,),
            )
            return Path(hit["file_path"])
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
            "INSERT OR REPLACE INTO thumbs(album_id,cache_key,file_path,source_sig) VALUES(?,?,?,?)",
            (album_id, cache_key, str(output), source.signature),
        )
    return output


def set_cover(album_id: int, kind: str, entry: str | None = None) -> bool:
    row = get_album(album_id)
    if not row:
        return False
    old_upload = row["cover_ref"] if row["cover_kind"] == "upload" else None
    if kind == "internal" and (not entry or entry not in image_entries(row)):
        raise ValueError("封面条目不存在")
    with connect() as conn:
        conn.execute(
            "UPDATE albums SET cover_kind=?,cover_ref=?,cover_path=NULL WHERE id=?",
            (kind, entry if kind == "internal" else None, album_id),
        )
        invalidate_thumbs(conn, album_id)
    if old_upload:
        try:
            Path(old_upload).unlink(missing_ok=True)
        except OSError:
            pass
    return True

