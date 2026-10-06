"""画面 (ブラウザ) と、その裏の API. 127.0.0.1 だけで待ち受ける.

資料と意味層は、登録したフォルダごとの保存先に分かれている (library.py)。
API の scope は、登録したフォルダのパス、または "central" (フォルダの外の資料)。
"""

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
from . import concepts as co
from . import groups as gr
from . import keywords as kw
from . import layer as ly
from . import runtime
from .ingest import ingest, ingest_text
from .library import CENTRAL, LAYER_DIR
from .loaders import SUPPORTED_SUFFIXES
from .markdown import document_markdown, remove_document_file
from .search import search_all

WEB_DIR = Path(__file__).parent / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
# 画面はこのアプリの中のファイルだけを使う (スクリプトは /static のファイルだけ。style 属性は画面の色付けに使う)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def _lib():
    return runtime.library()


def _startup() -> None:
    """前の版で取り込んだ資料の語を数え、古くなった意味層を作り直し、フォルダの見張りを始める."""
    import threading

    def run() -> None:
        try:
            for st in _lib().stores():
                while kw.backfill(st, limit=50):
                    pass
                cl.build_in_background(st)
        except Exception:  # noqa: BLE001  画面の起動は止めない
            pass

    threading.Thread(target=run, daemon=True).start()
    from . import jobs

    jobs.watch()


@asynccontextmanager
async def _lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    _startup()
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
    # ほかのサイトに埋め込ませない・ほかの場所のスクリプトを読ませない・型を推測させない
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
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
def _stats() -> dict[str, int]:
    total: dict[str, int] = {}
    for st in _lib().stores():
        for k, v in st.stats().items():
            total[k] = total.get(k, 0) + v
    return total


def _scopes() -> list[dict[str, Any]]:
    """意味層の範囲 (登録したフォルダと、フォルダの外の資料) と、それぞれの状態."""
    lib = _lib()
    out = []
    for row in lib.sources():
        exists = Path(row["path"]).exists()
        item = {"scope": row["path"], "label": "/".join(Path(row["path"]).parts[-2:]), "location": row["location"], "reason": row["reason"],
                "exists": exists, "auto": bool(row["auto"])}
        if exists or row["location"] == "central":
            st = lib.store_for_folder(row["path"])
            item.update({k: v for k, v in cl.status(st).items() if k not in ("scope", "label")})
            item["data_dir"] = str(Path(st.path).parent)
        out.append(item)
    if lib.catalog.count_documents():
        out.append({"scope": CENTRAL, "label": "フォルダの外の資料", "location": "central", "reason": "", "exists": True, "auto": False,
                    **{k: v for k, v in cl.status(lib.catalog).items() if k not in ("scope", "label")}, "data_dir": str(config.home())})
    return out


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    return {"version": __version__, "home": str(config.home()), "distribution": config.distribution(), "stats": _stats(),
            "scopes": _scopes(), "sources": len(_lib().sources()), "layer_dir": LAYER_DIR, "types": co.list_types(_lib()),
            "relation_kinds": list(ly.RELATION_KINDS), "documents_without_concepts": co.documents_without_concepts(_lib(), limit=10)}


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


def _with_scope(d: dict[str, Any], st) -> dict[str, Any]:
    d["scope"], d["folder"] = st.key, st.label
    return d


@app.get("/api/documents")
def documents(q: str | None = None, limit: int = 300, scope: str | None = None) -> dict[str, Any]:
    lib = _lib()
    stores = [lib.for_scope(scope)] if scope else lib.stores()
    docs, total = [], 0
    for st in stores:
        total += st.count_documents(q)
        docs += [_with_scope(d, st) for d in st.list_documents(q, limit)]
    docs.sort(key=lambda d: (d["added_at"], d["id"]), reverse=True)
    return {"total": total, "documents": docs[:limit]}


@app.get("/api/documents/folders")
def document_folders(depth: int = 4) -> list[dict[str, Any]]:
    from collections import Counter

    counts: Counter = Counter()
    for st in _lib().stores():
        for f in st.folders(depth=max(1, min(depth, 12)), limit=1000):
            counts[f["folder"]] += f["documents"]
    return [{"folder": f, "documents": n} for f, n in counts.most_common(40)]


@app.post("/api/documents/delete-under")
def delete_under(body: PrefixIn) -> dict[str, Any]:
    if not body.prefix.strip() or body.prefix.strip() in ("/", "~"):
        raise ValueError("消す範囲が広すぎます。フォルダを指定してください")
    deleted = 0
    for st in _lib().stores():
        ids = st.delete_documents_under(body.prefix.strip())
        for i in ids:
            if st.markdown_dir:
                remove_document_file(i, st.markdown_dir)
        if len(ids) >= 200:
            st.compact()
        if ids:
            cl.build_in_background(st)
        deleted += len(ids)
    return {"deleted": deleted}


@app.get("/api/documents/outside")
def outside() -> dict[str, Any]:
    return {"documents": len(_lib().catalog.documents_outside_sources())}


@app.post("/api/documents/delete-outside")
def delete_outside() -> dict[str, Any]:
    """登録したフォルダの外の資料をまとめて消す (元のファイルは消さない)."""
    lib = _lib()
    if not lib.sources():
        raise ValueError("フォルダを 1 つ以上登録してから使ってください")
    s = lib.catalog
    ids = s.documents_outside_sources()
    s.delete_documents(ids)
    for i in ids:
        remove_document_file(i, runtime.markdown_dir())
    if len(ids) >= 200:
        s.compact()
    cl.build_in_background(s)
    return {"deleted": len(ids)}


@app.get("/api/scan")
def scan(path: str) -> dict[str, Any]:
    from .loaders import scan as _scan

    try:
        r = _scan(path.strip())
    except FileNotFoundError as e:
        raise HTTPException(404, f"見つかりません: {e}") from e
    # 登録したときの保存先 (フォルダの中か、アプリ側か) を前もって知らせる
    from .library import cloud_reason, real

    p = real(path.strip())
    r["layer_location"] = "central" if cloud_reason(p) else "folder"
    r["layer_reason"] = cloud_reason(p)
    r["layer_dir"] = str(Path(p) / LAYER_DIR)
    r["registered"] = bool(_lib().source(p))
    return r


@app.post("/api/pick-folder")
def pick_folder() -> dict[str, Any]:
    """この PC のフォルダ選択の窓を出す (ブラウザで開いているとき用. Mac アプリは自前の窓を使う)."""
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Darwin":
            r = subprocess.run(["/usr/bin/osascript", "-e", 'POSIX path of (choose folder with prompt "取り込むフォルダを選んでください")'],
                               capture_output=True, text=True, timeout=600)
        elif system == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                  "$d.Description = '取り込むフォルダを選んでください'; if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")
            import os

            exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            r = subprocess.run([exe, "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True, timeout=600)
        else:
            raise HTTPException(501, "この OS ではフォルダ選択の窓を出せません。パスを入力してください")
    except subprocess.TimeoutExpired:
        return {"path": None}
    path = (r.stdout or "").strip()
    return {"path": path or None}


class SourceIn(BaseModel):
    path: str | None = None
    delete_documents: bool = False
    delete_data: bool = False
    auto: bool | None = None


@app.get("/api/sources")
def sources() -> list[dict[str, Any]]:
    from .jobs import scan_signature

    lib = _lib()
    out = []
    for row in lib.sources():
        exists = Path(row["path"]).is_dir()
        d = {**row, "exists": exists, "documents": 0, "changed": False}
        if exists or row["location"] == "central":
            st = lib.store_for_folder(row["path"])
            d["documents"] = st.count_documents()
            d["data_dir"] = str(Path(st.path).parent)
        if exists:
            d["changed"] = scan_signature(row["path"]) != (row["scan_sig"] or "")
        out.append(d)
    return out


@app.post("/api/sources")
def add_source(body: SourceIn) -> dict[str, Any]:
    """フォルダを登録して取り込む (意味層の保存先も作る)."""
    from . import jobs

    row = _lib().register((body.path or "").strip())
    return {**jobs.start(row["path"]).view(), "source": row}


@app.post("/api/sources/run")
def run_sources(body: SourceIn) -> dict[str, Any]:
    """登録したフォルダをもう一度読み、新しいファイル・変わったファイル・消えたファイルを反映する (path が無ければ全部)."""
    from . import jobs

    paths = [body.path] if body.path else [s["path"] for s in _lib().sources() if Path(s["path"]).is_dir()]
    return jobs.start(paths).view()


@app.post("/api/sources/remove")
def remove_source(body: SourceIn) -> dict[str, Any]:
    return _lib().unregister((body.path or "").strip(), delete_data=body.delete_data or body.delete_documents)


@app.post("/api/sources/auto")
def source_auto(body: SourceIn) -> dict[str, Any]:
    _lib().set_auto((body.path or "").strip(), bool(body.auto))
    return {"auto": bool(body.auto)}


# ---------------- 意味層 (自動) ----------------
class ScopeIn(BaseModel):
    scope: str = CENTRAL


@app.get("/api/scopes")
def scopes() -> list[dict[str, Any]]:
    return _scopes()


@app.get("/api/layer")
def layer_status(scope: str = CENTRAL) -> dict[str, Any]:
    return cl.status(_lib().for_scope(scope))


@app.post("/api/layer/rebuild")
def layer_rebuild(body: ScopeIn) -> dict[str, Any]:
    st = _lib().for_scope(body.scope)
    started = cl.build_in_background(st, force=True)
    return {"started": started, **cl.status(st)}


@app.get("/api/map")
def map_data(scope: str = CENTRAL) -> dict[str, Any]:
    st = _lib().for_scope(scope)
    s = cl.status(st)
    if (s["stale"] or not s["built_at"]) and s["documents_now"]:
        cl.build_in_background(st)   # まだ無い・古い意味層は、開いたときに作る
    return gr.add_to_map(st, cl.map_data(st))


class GroupIn(BaseModel):
    scope: str
    key: str
    label: str = ""


@app.get("/api/groups")
def group_list(scope: str = CENTRAL) -> list[dict[str, Any]]:
    """グループ (登録したフォルダの 1 段下のサブフォルダ) の一覧."""
    return gr.groups(_lib().for_scope(scope))


@app.post("/api/groups/rename")
def group_rename(body: GroupIn) -> dict[str, Any]:
    try:
        return gr.rename(_lib().for_scope(body.scope), body.key, body.label)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0])) from e


@app.get("/api/groups/links")
def group_links(scope: str = CENTRAL, a: str | None = None, b: str | None = None, max_edges: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    """2 つのグループの関わり (まとまりどうしの線と、近い資料の組)."""
    return gr.links(_lib().for_scope(scope), a, b, max_edges=max_edges)


@app.get("/api/clusters/{cluster_id}")
def cluster(cluster_id: int, scope: str = CENTRAL) -> dict[str, Any]:
    return cl.cluster_detail(_lib().for_scope(scope), cluster_id)


@app.get("/api/documents/{document_id}/similar")
def similar(document_id: int) -> list[dict[str, Any]]:
    return cl.similar_documents(_lib().for_id(document_id), document_id)


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
    path = body.path.strip()
    try:
        st = _lib().catalog if path.startswith(("http://", "https://")) else _lib().for_source(path)
        return [r.__dict__ for r in ingest(st, path, st.markdown_dir)]
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
    st = _lib().for_id(document_id)
    doc = st.get_document(document_id)
    if doc is None:
        raise HTTPException(404, "資料がありません")
    return _with_scope({**doc, "paragraphs": st.paragraphs_of(document_id), "concepts": ly.concepts_in_document(st, document_id)}, st)


@app.get("/api/documents/{document_id}/markdown", response_class=PlainTextResponse)
def document_md(document_id: int) -> str:
    return document_markdown(_lib().for_id(document_id), document_id)


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int) -> dict[str, Any]:
    st = _lib().for_id(document_id)
    ok = st.delete_document(document_id)
    if st.markdown_dir:
        remove_document_file(document_id, st.markdown_dir)
    cl.build_in_background(st)
    return {"deleted": ok}


# ---------------- 検索 ----------------
@app.get("/api/search")
def search(q: list[str] = Query(default=[]), top_k: int = 20, scope: str | None = None) -> dict[str, Any]:
    lib = _lib()
    queries = [x.strip() for part in q for x in part.split("|") if x.strip()]
    concepts: list[dict[str, Any]] = []
    for x in queries:
        for c in co.list_concepts(lib, scope, query=x, limit=10):
            if c["id"] not in {y["id"] for y in concepts}:
                concepts.append(c)
    return {"queries": queries, "paragraphs": search_all(lib, queries, top_k=top_k, scope=scope), "concepts": concepts}


@app.get("/api/route")
def route(q: str, scope: str | None = None, max_documents: int = 8) -> dict[str, Any]:
    """意味層をたどって探す: 関係するまとまり → 資料 → 段落と、あわせて確かめたい読み残しの資料."""
    from . import navigate as nav

    lib = _lib()
    r = nav.route(lib, q, scope, max_documents)
    shown = [d["document_id"] for d in r["documents"]]
    u = nav.unread(lib, q, read_document_ids=shown, limit=8, scope=scope)
    return {"route": r, "unread": u}


# ---------------- 意味層 (課題と解決手段) ----------------
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
    scope: str | None = None


@app.get("/api/types")
def types() -> list[dict[str, Any]]:
    return co.list_types(_lib())


@app.post("/api/types")
def add_type(body: TypeIn) -> dict[str, Any]:
    return co.add_type(_lib(), body.name, body.description, body.color)


@app.delete("/api/types/{name}")
def delete_type(name: str) -> dict[str, Any]:
    return {"deleted": co.delete_type(_lib(), name)}


@app.get("/api/concepts")
def concepts(type: str | None = None, q: str | None = None, limit: int = 1000, scope: str | None = None) -> list[dict[str, Any]]:
    return co.list_concepts(_lib(), scope, type_=type, query=q, limit=limit)


@app.post("/api/concepts")
def add_concept(body: ConceptIn) -> dict[str, Any]:
    if not body.paragraph_ids:
        raise ValueError("根拠にする段落を 1 つ以上選んでください")
    return co.write(_lib(), body.name, body.type, body.paragraph_ids, body.description or None, body.aliases)


@app.post("/api/concepts/merge")
def merge(body: MergeIn) -> dict[str, Any]:
    return co.merge(_lib(), body.keep, body.drop)


@app.get("/api/concepts/{concept_id}")
def concept(concept_id: int) -> dict[str, Any]:
    return co.get_concept(_lib(), concept_id, evidence_limit=200)


@app.patch("/api/concepts/{concept_id}")
def patch_concept(concept_id: int, body: ConceptPatch) -> dict[str, Any]:
    return co.update(_lib(), concept_id, body.name, body.type, body.description)


@app.delete("/api/concepts/{concept_id}")
def delete_concept(concept_id: int) -> dict[str, Any]:
    return {"deleted": co.delete(_lib(), concept_id)}


@app.post("/api/concepts/{concept_id}/aliases")
def add_aliases(concept_id: int, body: AliasesIn) -> dict[str, Any]:
    return {"added": co.add_aliases(_lib(), concept_id, body.aliases)}


@app.delete("/api/concepts/{concept_id}/aliases/{alias}")
def remove_alias(concept_id: int, alias: str) -> dict[str, Any]:
    return {"deleted": co.remove_alias(_lib(), concept_id, alias)}


@app.post("/api/concepts/{concept_id}/evidence")
def add_evidence(concept_id: int, body: EvidenceIn) -> dict[str, Any]:
    return co.add_evidence(_lib(), concept_id, body.paragraph_ids, body.note)


@app.delete("/api/concepts/{concept_id}/evidence/{paragraph_id}")
def remove_evidence(concept_id: int, paragraph_id: int) -> dict[str, Any]:
    return {"deleted": co.remove_evidence(_lib(), concept_id, paragraph_id)}


@app.post("/api/relations")
def relate(body: RelationIn) -> dict[str, Any]:
    return co.relate(_lib(), body.source, body.target, body.kind, body.paragraph_id, body.note, body.scope)


@app.delete("/api/relations/{relation_id}")
def delete_relation(relation_id: int) -> dict[str, Any]:
    return {"deleted": co.delete_relation(_lib(), relation_id)}


@app.get("/api/graph")
def graph(documents: bool = True, type: str | None = None, scope: str | None = None) -> dict[str, Any]:
    return co.graph(_lib(), scope, with_documents=documents, type_=type)


@app.get("/api/keywords/graph")
def keywords_graph(limit: int = 80, q: str | None = None, scope: str = CENTRAL) -> dict[str, Any]:
    st = _lib().for_scope(scope)
    g = kw.graph(st, limit=max(10, min(limit, 200)), query=q)
    # 語の色を、その語がいちばん強く出るまとまりの色にする
    cs = {c["id"]: c for c in cl.clusters(st)}
    of = cl.cluster_of_terms(st, [n["label"] for n in g["nodes"]])
    for n in g["nodes"]:
        c = cs.get(of.get(n["label"], -1))
        if c:
            n["color"], n["cluster"], n["type"] = c["color"], c["id"], c["label"]
    g["clusters"] = [{"id": c["id"], "label": c["label"], "color": c["color"]} for c in cs.values()]
    return g


@app.get("/api/keywords/documents")
def keyword_documents(term: str, limit: int = 50, scope: str = CENTRAL) -> dict[str, Any]:
    return kw.documents_with(_lib().for_scope(scope), term, limit)


@app.get("/api/export/layer.md", response_class=PlainTextResponse)
def export_layer(scope: str | None = None) -> str:
    return co.layer_markdown(_lib(), scope)


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
