"""画面 (ブラウザ) と、その裏の API. 127.0.0.1 だけで待ち受ける."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config
from . import layer as ly
from . import runtime
from .ingest import ingest, ingest_text
from .loaders import SUPPORTED_SUFFIXES
from .markdown import document_markdown, layer_markdown, remove_document_file
from .search import search as _search

WEB_DIR = Path(__file__).parent / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}

app = FastAPI(title="LeXWeft Lite", version=__version__)


@app.middleware("http")
async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    """他のサイトからの書き込み (CSRF) と、別名の Host での呼び出し (DNS rebinding) を断る."""
    raw = request.headers.get("host") or ""
    host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.rsplit(":", 1)[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-lexweft") != "1":
        return JSONResponse({"detail": "missing X-LexWeft header"}, status_code=403)
    resp = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(KeyError)
async def _key_error(_: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse({"detail": str(exc.args[0]) if exc.args else "not found"}, status_code=404)


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------- 全体 ----------------
@app.get("/api/overview")
def overview() -> dict[str, Any]:
    s = runtime.store()
    return {"version": __version__, "home": str(config.home()), "distribution": config.distribution(), "stats": s.stats(), "types": ly.list_types(s),
            "relation_kinds": list(ly.RELATION_KINDS), "documents_without_concepts": ly.documents_without_concepts(s, limit=10)}


@app.get("/api/mcp-config")
def mcp_config() -> dict[str, Any]:
    from .cli import server_entry

    return {"mcpServers": {"lexweft-lite": server_entry()}}


# ---------------- 資料 ----------------
class PathIn(BaseModel):
    path: str


class TextIn(BaseModel):
    title: str
    text: str


@app.get("/api/documents")
def documents() -> list[dict[str, Any]]:
    return runtime.store().list_documents()


@app.post("/api/documents/path")
def add_path(body: PathIn) -> list[dict[str, Any]]:
    try:
        return [r.__dict__ for r in ingest(runtime.store(), body.path.strip(), runtime.markdown_dir())]
    except FileNotFoundError as e:
        raise HTTPException(404, f"見つかりません: {e}") from e


@app.post("/api/documents/text")
def add_text(body: TextIn) -> dict[str, Any]:
    return ingest_text(runtime.store(), body.title, body.text, markdown_dir=runtime.markdown_dir()).__dict__


@app.post("/api/documents/upload")
async def upload(files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    """選んだファイルを保存先の files/ に写してから取り込む (元のファイルは触らない)."""
    dest = config.home() / "files"
    dest.mkdir(exist_ok=True)
    out: list[dict[str, Any]] = []
    for f in files:
        name = re.sub(r"[\\/:*?\"<>|]+", "_", Path(f.filename or "upload").name)
        if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
            out.append({"title": name, "status": "unsupported"})
            continue
        path = dest / name
        path.write_bytes(await f.read())
        out += [r.__dict__ for r in ingest(runtime.store(), str(path), runtime.markdown_dir())]
    return out


@app.get("/api/documents/{document_id}")
def document(document_id: int) -> dict[str, Any]:
    s = runtime.store()
    doc = s.get_document(document_id)
    if doc is None:
        raise HTTPException(404, "資料がありません")
    return {**doc, "paragraphs": s.paragraphs_of(document_id), "concepts": ly.concepts_in_document(s, document_id)}


@app.get("/api/documents/{document_id}/markdown", response_class=PlainTextResponse)
def document_md(document_id: int) -> str:
    return document_markdown(runtime.store(), document_id)


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int) -> dict[str, Any]:
    ok = runtime.store().delete_document(document_id)
    remove_document_file(document_id, runtime.markdown_dir())
    return {"deleted": ok}


# ---------------- 検索 ----------------
@app.get("/api/search")
def search(q: list[str] = Query(default=[]), top_k: int = 20) -> dict[str, Any]:
    s = runtime.store()
    queries = [x.strip() for part in q for x in part.split("|") if x.strip()]
    concepts = []
    for x in queries:
        for c in ly.list_concepts(s, query=x, limit=10):
            if c["id"] not in {y["id"] for y in concepts}:
                concepts.append(c)
    return {"queries": queries, "paragraphs": _search(s, queries, top_k=top_k), "concepts": concepts}


# ---------------- 意味層 ----------------
class TypeIn(BaseModel):
    name: str
    description: str = ""
    color: str | None = None


class ConceptIn(BaseModel):
    name: str
    type: str
    description: str = ""
    aliases: list[str] = []
    paragraph_ids: list[int] = []


class ConceptPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    description: str | None = None


class AliasesIn(BaseModel):
    aliases: list[str]


class EvidenceIn(BaseModel):
    paragraph_ids: list[int]
    note: str = ""


class MergeIn(BaseModel):
    keep: int
    drop: int


class RelationIn(BaseModel):
    source: int | str
    target: int | str
    kind: str = "解決する"
    paragraph_id: int | None = None
    note: str = ""


@app.get("/api/types")
def types() -> list[dict[str, Any]]:
    return ly.list_types(runtime.store())


@app.post("/api/types")
def add_type(body: TypeIn) -> dict[str, Any]:
    return ly.add_type(runtime.store(), body.name, body.description, body.color)


@app.delete("/api/types/{name}")
def delete_type(name: str) -> dict[str, Any]:
    return {"deleted": ly.delete_type(runtime.store(), name)}


@app.get("/api/concepts")
def concepts(type: str | None = None, q: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    return ly.list_concepts(runtime.store(), type_=type, query=q, limit=limit)


@app.post("/api/concepts")
def add_concept(body: ConceptIn) -> dict[str, Any]:
    s = runtime.store()
    out = ly.upsert_concept(s, body.name, body.type, body.description or None, body.aliases)
    if body.paragraph_ids:
        out["evidence"] = ly.add_evidence(s, out["id"], body.paragraph_ids)
    return out


@app.post("/api/concepts/merge")
def merge(body: MergeIn) -> dict[str, Any]:
    return ly.merge_concepts(runtime.store(), body.keep, body.drop)


@app.get("/api/concepts/{concept_id}")
def concept(concept_id: int) -> dict[str, Any]:
    return ly.get_concept(runtime.store(), concept_id, evidence_limit=200)


@app.patch("/api/concepts/{concept_id}")
def patch_concept(concept_id: int, body: ConceptPatch) -> dict[str, Any]:
    return ly.update_concept(runtime.store(), concept_id, body.name, body.type, body.description)


@app.delete("/api/concepts/{concept_id}")
def delete_concept(concept_id: int) -> dict[str, Any]:
    return {"deleted": ly.delete_concept(runtime.store(), concept_id)}


@app.post("/api/concepts/{concept_id}/aliases")
def add_aliases(concept_id: int, body: AliasesIn) -> dict[str, Any]:
    return {"added": ly.add_aliases(runtime.store(), concept_id, body.aliases)}


@app.delete("/api/concepts/{concept_id}/aliases/{alias}")
def remove_alias(concept_id: int, alias: str) -> dict[str, Any]:
    return {"deleted": ly.remove_alias(runtime.store(), concept_id, alias)}


@app.post("/api/concepts/{concept_id}/evidence")
def add_evidence(concept_id: int, body: EvidenceIn) -> dict[str, Any]:
    return ly.add_evidence(runtime.store(), concept_id, body.paragraph_ids, body.note)


@app.delete("/api/concepts/{concept_id}/evidence/{paragraph_id}")
def remove_evidence(concept_id: int, paragraph_id: int) -> dict[str, Any]:
    return {"deleted": ly.remove_evidence(runtime.store(), concept_id, paragraph_id)}


@app.post("/api/relations")
def relate(body: RelationIn) -> dict[str, Any]:
    return ly.relate(runtime.store(), body.source, body.target, body.kind, body.paragraph_id, body.note)


@app.delete("/api/relations/{relation_id}")
def delete_relation(relation_id: int) -> dict[str, Any]:
    return {"deleted": ly.delete_relation(runtime.store(), relation_id)}


@app.get("/api/graph")
def graph(documents: bool = True, type: str | None = None) -> dict[str, Any]:
    return ly.graph(runtime.store(), with_documents=documents, type_=type)


@app.get("/api/export/layer.md", response_class=PlainTextResponse)
def export_layer() -> str:
    return layer_markdown(runtime.store())


# ---------------- 画面 ----------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _lite_running(port: int) -> bool:
    """そのポートで LeXWeft Lite が既に動いているか."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview", timeout=1.5) as r:
            return "distribution" in json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("LeXWeft Lite は手元の PC (127.0.0.1) でだけ動かします")
    if not _port_free(host, port):
        if _lite_running(port):
            url = f"http://127.0.0.1:{port}/"
            print(f"LeXWeft Lite はもう動いています: {url}")
            if open_browser:
                webbrowser.open(url)
            return
        free = next((p for p in range(port + 1, port + 30) if _port_free(host, p)), None)
        if free is None:
            raise SystemExit(f"ポート {port} から {port + 29} がすべて使われています。--port で空いている番号を指定してください")
        print(f"ポート {port} は別のアプリが使っているので、{free} で開きます")
        port = free
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"LeXWeft Lite: {url}  (保存先 {config.home()})  止めるには Ctrl+C")
    uvicorn.run(app, host=host, port=port, log_level="warning")
