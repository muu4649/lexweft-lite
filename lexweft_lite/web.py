"""画面 (ブラウザ) と、その裏の API. 127.0.0.1 だけで待ち受ける."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config
from . import clusters as cl
from . import keywords as kw
from . import layer as ly
from . import runtime
from .ingest import ingest, ingest_text
from .loaders import SUPPORTED_SUFFIXES
from .markdown import document_markdown, layer_markdown, remove_document_file
from .search import search as _search
from .store import nfc

WEB_DIR = Path(__file__).parent / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


def _backfill_keywords() -> None:
    """前の版で取り込んだ資料の語を、裏で数えておく (キーワードのつながり用)."""
    import threading

    def run() -> None:
        try:
            while kw.backfill(runtime.store(), limit=50):
                pass
            cl.refresh_registered(runtime.store())   # 資料が変わったフォルダの、まとまりと地図を作り直す
        except Exception:  # noqa: BLE001  画面の起動は止めない
            pass

    threading.Thread(target=run, daemon=True).start()


@asynccontextmanager
async def _lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    _backfill_keywords()
    yield


app = FastAPI(title="LeXWeft Lite", version=__version__, lifespan=_lifespan)


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
    elif request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"   # 版を上げたときに古い画面が残らないように (毎回確かめる)
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
    return {"version": __version__, "home": str(config.home()), "distribution": config.distribution(), "stats": s.stats(), "scopes": cl.scopes(s),
            "sources": len(s.list_sources()), "types": ly.list_types(s),
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


class PrefixIn(BaseModel):
    prefix: str


@app.get("/api/documents")
def documents(q: str | None = None, limit: int = 300) -> dict[str, Any]:
    s = runtime.store()
    return {"total": s.count_documents(q), "documents": s.list_documents(q, limit)}


@app.get("/api/documents/folders")
def document_folders(depth: int = 4) -> list[dict[str, Any]]:
    return runtime.store().folders(depth=max(1, min(depth, 12)))


@app.post("/api/documents/delete-under")
def delete_under(body: PrefixIn) -> dict[str, Any]:
    if not body.prefix.strip() or body.prefix.strip() in ("/", "~"):
        raise ValueError("消す範囲が広すぎます。フォルダを指定してください")
    ids = runtime.store().delete_documents_under(body.prefix.strip())
    for i in ids:
        remove_document_file(i, runtime.markdown_dir())
    if len(ids) >= 200:
        runtime.store().compact()
    return {"deleted": len(ids)}


@app.get("/api/documents/outside")
def outside() -> dict[str, Any]:
    return {"documents": len(runtime.store().documents_outside_sources())}


@app.post("/api/documents/delete-outside")
def delete_outside() -> dict[str, Any]:
    """登録したフォルダの外の資料をまとめて消す (元のファイルは消さない)."""
    s = runtime.store()
    if not s.list_sources():
        raise ValueError("フォルダを 1 つ以上登録してから使ってください")
    ids = s.documents_outside_sources()
    s.delete_documents(ids)
    for i in ids:
        remove_document_file(i, runtime.markdown_dir())
    if len(ids) >= 200:
        s.compact()
    cl.refresh_registered(s)
    return {"deleted": len(ids)}


@app.get("/api/scan")
def scan(path: str) -> dict[str, Any]:
    from .loaders import scan as _scan

    try:
        return _scan(path.strip())
    except FileNotFoundError as e:
        raise HTTPException(404, f"見つかりません: {e}") from e


@app.post("/api/pick-folder")
def pick_folder() -> dict[str, Any]:
    """この PC のフォルダ選択の窓を出す (ブラウザで開いているとき用. Mac アプリは自前の窓を使う)."""
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Darwin":
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "取り込むフォルダを選んでください")'],
                               capture_output=True, text=True, timeout=600)
        elif system == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                  "$d.Description = '取り込むフォルダを選んでください'; if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, timeout=600)
        else:
            raise HTTPException(501, "この OS ではフォルダ選択の窓を出せません。パスを入力してください")
    except subprocess.TimeoutExpired:
        return {"path": None}
    path = (r.stdout or "").strip()
    return {"path": path or None}


class SourceIn(BaseModel):
    path: str | None = None
    delete_documents: bool = False


@app.get("/api/sources")
def sources() -> list[dict[str, Any]]:
    return runtime.store().list_sources()


@app.post("/api/sources")
def add_source(body: SourceIn) -> dict[str, Any]:
    """フォルダを登録して取り込む."""
    from . import jobs

    path = nfc(str(Path((body.path or "").strip()).expanduser()))
    if not body.path or not Path(path).is_dir():
        raise ValueError("フォルダが見つかりません")
    if Path(path) in (Path.home(), Path("/"), Path(path).anchor and Path(Path(path).anchor)):
        raise ValueError("ホームフォルダや PC 全体は登録できません。資料の入ったフォルダを選んでください")
    runtime.store().add_source(path.rstrip("/"))
    return jobs.start(path.rstrip("/")).view()


@app.post("/api/sources/run")
def run_sources(body: SourceIn) -> dict[str, Any]:
    """登録したフォルダをもう一度読み、新しいファイル・変わったファイルを取り込む (path が無ければ全部)."""
    from . import jobs

    paths = [body.path] if body.path else [s["path"] for s in runtime.store().list_sources() if s["exists"]]
    return jobs.start(paths).view()


@app.post("/api/sources/remove")
def remove_source(body: SourceIn) -> dict[str, Any]:
    s = runtime.store()
    ok = s.remove_source((body.path or "").rstrip("/"))
    deleted = 0
    if ok and body.delete_documents:
        ids = s.delete_documents_under(body.path or "")
        for i in ids:
            remove_document_file(i, runtime.markdown_dir())
        deleted = len(ids)
        if deleted >= 200:
            s.compact()
    s.conn.execute("DELETE FROM lscopes WHERE scope = ?", ((body.path or "").rstrip("/"),))
    s.conn.commit()
    cl.refresh_registered(s)
    return {"removed": ok, "deleted": deleted}


class ScopeIn(BaseModel):
    scope: str = cl.ALL


@app.get("/api/scopes")
def scopes() -> list[dict[str, Any]]:
    """意味層を作る範囲 (登録したフォルダと、すべての資料) と、それぞれの状態."""
    return cl.scopes(runtime.store())


@app.get("/api/layer")
def layer_status(scope: str = cl.ALL) -> dict[str, Any]:
    return cl.status(runtime.store(), scope)


@app.post("/api/layer/rebuild")
def layer_rebuild(body: ScopeIn) -> dict[str, Any]:
    started = cl.build_in_background(runtime.store(), body.scope, force=True)
    return {"started": started, **cl.status(runtime.store(), body.scope)}


@app.get("/api/map")
def map_data(scope: str = cl.ALL) -> dict[str, Any]:
    s = runtime.store()
    st = cl.status(s, scope)
    if (st["stale"] or not st["built_at"]) and st["documents_now"]:
        cl.build_in_background(s, scope)   # まだ無い・古い範囲は、開いたときに作る
    return cl.map_data(s, scope)


@app.get("/api/clusters/{cluster_id}")
def cluster(cluster_id: int, scope: str = cl.ALL) -> dict[str, Any]:
    return cl.cluster_detail(runtime.store(), cluster_id, scope)


@app.get("/api/documents/{document_id}/similar")
def similar(document_id: int, scope: str | None = None) -> list[dict[str, Any]]:
    return cl.similar_documents(runtime.store(), document_id, scope)


@app.post("/api/jobs")
def start_job(body: PathIn) -> dict[str, Any]:
    from . import jobs

    return jobs.start(body.path).view()


@app.get("/api/jobs/latest")
def latest_job() -> dict[str, Any]:
    from . import jobs

    j = jobs.latest()
    return j.view() if j else {}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    from . import jobs

    return jobs.cancel(job_id).view()


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


@app.get("/api/keywords/graph")
def keywords_graph(limit: int = 80, q: str | None = None, scope: str = cl.ALL) -> dict[str, Any]:
    s = runtime.store()
    docs = None if scope == cl.ALL else cl.scope_documents(s, scope)
    g = kw.graph(s, limit=max(10, min(limit, 200)), query=q, documents=docs)
    # 語の色を、その語がいちばん強く出るまとまりの色にする
    cs = {c["id"]: c for c in cl.clusters(s, scope)}
    of = cl.cluster_of_terms(s, [n["label"] for n in g["nodes"]], scope)
    for n in g["nodes"]:
        c = cs.get(of.get(n["label"], -1))
        if c:
            n["color"], n["cluster"], n["type"] = c["color"], c["id"], c["label"]
    g["clusters"] = [{"id": c["id"], "label": c["label"], "color": c["color"]} for c in cs.values()]
    return g


@app.get("/api/keywords/documents")
def keyword_documents(term: str, limit: int = 50) -> dict[str, Any]:
    return kw.documents_with(runtime.store(), term, limit)


@app.get("/api/export/layer.md", response_class=PlainTextResponse)
def export_layer() -> str:
    return layer_markdown(runtime.store())


# ---------------- 画面 ----------------
@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """画面の入口. 画面のファイルが変わったら必ず読み直されるよう、更新時刻を印に付ける."""
    stamp = int(max((WEB_DIR / n).stat().st_mtime for n in ("app.js", "app.css", "index.html")))
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("/static/app.js", f"/static/app.js?v={stamp}").replace("/static/app.css", f"/static/app.css?v={stamp}")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # 閉じたばかりの接続が残っていても使えるとみなす (uvicorn と同じ)
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


def _serve_for_app(host: str, ready_file: str, parent_pid: int | None) -> None:
    """Mac アプリから起動されたとき: 空いているポートで開き、その URL を ready_file に書く. 親 (アプリ) が終わったら止まる."""
    import json
    import os
    import socket
    import threading
    import time

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))

    def announce() -> None:
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            tmp = Path(ready_file + ".tmp")
            tmp.write_text(json.dumps({"url": f"http://127.0.0.1:{port}/"}), encoding="utf-8")
            tmp.replace(ready_file)   # 書きかけのファイルを親に見せない

    def watch_parent() -> None:
        while not server.should_exit:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                server.should_exit = True
                return
            time.sleep(1.0)

    threading.Thread(target=announce, daemon=True).start()
    if parent_pid:
        threading.Thread(target=watch_parent, daemon=True).start()
    print(f"LeXWeft Lite: http://127.0.0.1:{port}/  (保存先 {config.home()})", flush=True)
    server.run(sockets=[sock])


def serve(host: str = "127.0.0.1", port: int | None = None, open_browser: bool = True,
          ready_file: str | None = None, parent_pid: int | None = None) -> None:
    """画面を開く. port を指定しなければ 8765 から空いている番号を探す (指定したらその番号だけを使う)."""
    import threading
    import webbrowser

    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("LeXWeft Lite は手元の PC (127.0.0.1) でだけ動かします")
    if ready_file:
        _serve_for_app(host, ready_file, parent_pid)
        return
    explicit = port is not None
    port = port or 8765
    if not _port_free(host, port):
        if explicit:
            raise SystemExit(f"ポート {port} は使われています")
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
