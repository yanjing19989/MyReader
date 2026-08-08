import asyncio
import io
import json
import os
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .config import UPLOAD_DIR, VIEWER_PATH
from .covers import get_album, image_entries, make_thumbnail, set_cover
from .db import album_dict, connect, init_db, invalidate_thumbs, path_key
from .scanner import is_image, natural_key, normalize_path, refresh_all, scan_paths


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="MyReader", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
scan_lock = asyncio.Lock()


class ScanRequest(BaseModel):
    paths: list[str] = Field(min_length=1)
    recursive: bool = True


class CoverRequest(BaseModel):
    kind: Literal["default", "internal"]
    entry: str | None = None


class ViewerRequest(BaseModel):
    path: str
    type: Literal["folder", "zip"]


def _relations(rows):
    parents: dict[int, int | None] = {}
    folders = [row for row in rows if row["type"] == "folder"]
    for row in rows:
        key = path_key(row["path"])
        candidates = [
            parent for parent in folders
            if parent["id"] != row["id"] and key.startswith(path_key(parent["path"]) + "/")
        ]
        parents[row["id"]] = max(candidates, key=lambda item: len(item["path"]))["id"] if candidates else None
    return parents


def _tree(rows, parents, query: str):
    by_parent: dict[int | None, list] = {}
    for row in rows:
        by_parent.setdefault(parents[row["id"]], []).append(row)
    for group in by_parent.values():
        group.sort(key=lambda item: natural_key(item["name"]))

    def build(row):
        children = [build(child) for child in by_parent.get(row["id"], [])]
        node = {"album": album_dict(row), "path": row["path"], "children": children}
        if not query or query in row["name"].casefold():
            return node
        kept = [child for child in children if child]
        if kept:
            node["children"] = kept
            return node
        return None

    return [node for row in by_parent.get(None, []) if (node := build(row))]


@app.post("/api/scans")
async def scan(request: ScanRequest):
    async def stream():
        async with scan_lock:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            def emit(event):
                loop.call_soon_threadsafe(queue.put_nowait, event)

            task = asyncio.create_task(asyncio.to_thread(scan_paths, request.paths, request.recursive, emit))
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), 0.1)
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                except asyncio.TimeoutError:
                    continue
            await task

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/refresh")
async def refresh():
    async with scan_lock:
        return await asyncio.to_thread(refresh_all)


@app.get("/api/albums")
def albums(
    view: str = Query("children", pattern="^(children|tree)$"),
    parent_id: int | None = None,
    q: str = "",
    sort: str = Query("name", pattern="^(name|added_at|mtime|size|file_count)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM albums").fetchall()
    parents = _relations(rows)
    query = q.strip().casefold()
    if view == "tree":
        return {"tree": _tree(rows, parents, query)}

    parent = next((row for row in rows if row["id"] == parent_id), None) if parent_id else None
    if parent_id is not None and parent is None:
        raise HTTPException(404, "相册不存在")
    items = [row for row in rows if parents[row["id"]] == parent_id]
    if query:
        items = [row for row in items if query in row["name"].casefold()]
    reverse = order == "desc"
    if sort == "name":
        items.sort(key=lambda row: natural_key(row["name"]), reverse=reverse)
    else:
        items.sort(key=lambda row: row[sort], reverse=reverse)
    ancestors = []
    current = parent
    while current:
        parent_key = parents[current["id"]]
        current = next((row for row in rows if row["id"] == parent_key), None)
        if current:
            ancestors.append(album_dict(current))
    ancestors.reverse()
    return {
        "items": [album_dict(row) for row in items],
        "parent": album_dict(parent),
        "ancestors": ancestors,
    }


@app.get("/api/albums/{album_id}/images")
def album_images(album_id: int):
    row = get_album(album_id)
    if not row:
        raise HTTPException(404, "相册不存在")
    return {"items": image_entries(row)}


@app.get("/api/albums/{album_id}/cover")
async def album_cover(
    album_id: int,
    width: int = Query(300, ge=32, le=1200),
    height: int = Query(400, ge=32, le=1200),
    mode: str = Query("cover", pattern="^(cover|contain)$"),
    quality: int = Query(75, ge=20, le=95),
):
    output = await asyncio.to_thread(make_thumbnail, album_id, width, height, mode, quality)
    if not output:
        raise HTTPException(404, "没有可用封面")
    return FileResponse(output, media_type="image/webp", headers={"Cache-Control": "no-cache"})


@app.put("/api/albums/{album_id}/cover")
def update_cover(album_id: int, request: CoverRequest):
    try:
        if not set_cover(album_id, request.kind, request.entry):
            raise HTTPException(404, "相册不存在")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/albums/{album_id}/cover/upload")
async def upload_cover(album_id: int, file: UploadFile = File(...)):
    row = get_album(album_id)
    if not row:
        raise HTTPException(404, "相册不存在")
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "封面文件不能超过 25 MB")
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "上传文件不是有效图片") from exc
    output = UPLOAD_DIR / f"{album_id}-{uuid.uuid4().hex}.webp"
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGBA")
    background = Image.new("RGB", image.size, "white")
    background.paste(image, mask=image.getchannel("A"))
    background.save(output, "WEBP", quality=90, method=4)
    old_upload = row["cover_ref"] if row["cover_kind"] == "upload" else None
    with connect() as conn:
        conn.execute(
            "UPDATE albums SET cover_kind='upload',cover_ref=?,cover_path=NULL WHERE id=?",
            (str(output), album_id),
        )
        invalidate_thumbs(conn, album_id)
    if old_upload:
        try:
            Path(old_upload).unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": True}


@app.post("/api/viewer/open")
def open_viewer(request: ViewerRequest):
    raw = Path(request.path).expanduser()
    if not raw.is_absolute():
        raise HTTPException(400, "必须提供绝对路径")
    path = Path(normalize_path(raw))
    valid = path.is_dir() if request.type == "folder" else path.is_file() and path.suffix.casefold() == ".zip"
    if not valid:
        raise HTTPException(400, "路径不存在或类型不匹配")
    if not VIEWER_PATH.is_file():
        raise HTTPException(503, f"LocalViewer 未配置或不存在：{VIEWER_PATH}")
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([str(VIEWER_PATH), str(path)], shell=False, close_fds=True, **kwargs)
    except OSError as exc:
        raise HTTPException(500, f"无法启动 LocalViewer：{exc}") from exc
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}
