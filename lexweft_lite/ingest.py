"""取り込み: 読む → 段落に分ける → 保存する → Markdown を書き出す."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import keywords
from .chunker import CHUNKING_VERSION, from_labeled, split_document
from .loaders import Loaded, load_file, load_path, load_url
from .markdown import write_document_file
from .store import Store


@dataclass
class IngestResult:
    document_id: int | None
    title: str
    source: str
    paragraphs: int
    status: str  # added | updated | unchanged | empty
    markdown_path: str | None = None


def ingest_loaded(store: Store, doc: Loaded, markdown_dir: Path | None = None, force: bool = False) -> IngestResult:
    """1 件の資料を保存する. 同じ出所で中身が変わっていなければ何もしない (force なら取り込み直す)."""
    sha = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
    old = store.find_document_by_source(doc.source)
    if old is not None and old["sha256"] == sha and not force:
        return IngestResult(int(old["id"]), old["title"], doc.source, 0, "unchanged")
    # 段落と節の表に分ける (見出しは段落に持たせず、節の表に置く)。本文の無い節は捨てる
    paragraphs, sections = from_labeled(doc.paragraphs) if doc.paragraphs is not None else split_document(doc.text)
    if not paragraphs:
        return IngestResult(None, doc.title, doc.source, 0, "empty")
    doc_id = store.add_document(doc.title, doc.source, doc.kind, sha, doc.meta, paragraphs, sections)
    keywords.index_document(store, doc_id)
    md = str(write_document_file(store, doc_id, markdown_dir)) if markdown_dir else None
    return IngestResult(doc_id, doc.title, doc.source, len(paragraphs), "updated" if old is not None else "added", md)


def ingest(store: Store, target: str, markdown_dir: Path | None = None) -> list[IngestResult]:
    """ファイル・フォルダ・URL を取り込む. 同じ出所で中身が変わっていなければ何もしない."""
    if target.startswith(("http://", "https://")):
        return [ingest_loaded(store, load_url(target), markdown_dir)]
    return [ingest_loaded(store, d, markdown_dir) for d in load_path(target)]


def ingest_text(store: Store, title: str, text: str, source: str | None = None, markdown_dir: Path | None = None) -> IngestResult:
    """貼り付けた文章をそのまま 1 件の資料にする."""
    src = source or f"text:{hashlib.sha256((title + text).encode('utf-8')).hexdigest()[:16]}"
    return ingest_loaded(store, Loaded(title=title or "無題", source=src, text=text, kind="text"), markdown_dir)


# ---------------- 段落の分け方の版が変わったとき ----------------
def rechunk_needed(store: Store) -> bool:
    """段落の分け方の版が、この保存先に記録された版と違うか (資料が無ければ、版だけ記録して False)."""
    if store.meta("chunking") == CHUNKING_VERSION:
        return False
    if not store.count_documents():
        store.set_meta("chunking", CHUNKING_VERSION)
        return False
    return True


def rechunk(store: Store, markdown_dir: Path | None = None) -> dict[str, Any]:
    """取り込み済みの資料を、今の分け方で取り込み直す. 資料の番号はそのまま、根拠の段落は付け直す.

    元のファイルがある資料はファイルから読み直す。貼り付けた文章や URL など元のファイルが無い資料は、
    今の段落から見出しを外して節の表に移す。
    """
    counts: Counter = Counter()
    for d in store.list_documents():
        src = d["source"] or ""
        loaded = None
        if not src.startswith(("http://", "https://", "text:")) and Path(src).is_file():
            try:
                loaded = load_file(Path(src))
            except Exception:  # noqa: BLE001  読めなければ、今の段落から作り直す
                loaded = None
        if loaded is not None:
            counts[ingest_loaded(store, loaded, markdown_dir, force=True).status] += 1
        else:
            counts["restructured" if _restructure(store, int(d["id"]), markdown_dir) else "skipped"] += 1
    store.set_meta("chunking", CHUNKING_VERSION)
    return dict(counts)


def _restructure(store: Store, doc_id: int, markdown_dir: Path | None) -> bool:
    """今の段落から見出しを外し、続けて同じ見出しの段落を 1 つの節にまとめて、節の表に移す."""
    row = store.conn.execute("SELECT title, source, kind, sha256 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    doc = store.get_document(doc_id)
    if row is None or doc is None:
        return False
    texts: list[str] = []
    sections: list[tuple[str, int, int]] = []
    cur, first = "", 0
    for p in store.paragraphs_of(doc_id):
        head = (p["heading"] or "").strip()
        body = p["text"] or ""
        if head and body.startswith(head):
            body = body[len(head):].lstrip("\n")
        body = body.strip()
        if not body:
            continue
        if head != cur:
            if cur:
                sections.append((cur, first, len(texts) - 1))
            cur, first = head, len(texts)
        texts.append(body)
    if cur and texts:
        sections.append((cur, first, len(texts) - 1))
    if not texts:
        return False
    store.add_document(row["title"], doc["source"], row["kind"], row["sha256"], doc["meta"], texts, sections)
    keywords.index_document(store, doc_id)
    if markdown_dir:
        write_document_file(store, doc_id, markdown_dir)
    return True
