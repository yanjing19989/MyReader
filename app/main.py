import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .db import PUBLIC_FIELDS, album_dict, connect, init_db, path_key
from .scanner import natural_key, refresh_all, scan_paths


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


@app.get("/api/health")
def health():
    return {"ok": True}
