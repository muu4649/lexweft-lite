"""取り込み: 読む → 段落に分ける → 保存する → Markdown を書き出す."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import keywords
from .chunker import _split_with_heads
from .loaders import Loaded, load_path, load_url
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


def ingest_loaded(store: Store, doc: Loaded, markdown_dir: Path | None = None) -> IngestResult:
    sha = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
    old = store.find_document_by_source(doc.source)
    if old is not None and old["sha256"] == sha:
        return IngestResult(int(old["id"]), old["title"], doc.source, 0, "unchanged")
    paragraphs = doc.paragraphs if doc.paragraphs is not None else _split_with_heads(doc.text)
    # 見出しだけの段落 (題名の行など、本文の無い節) は捨てる
    paragraphs = [(h, b) for h, b in paragraphs if b.strip() and b.strip() != (h or "").strip()]
    if not paragraphs:
        return IngestResult(None, doc.title, doc.source, 0, "empty")
    doc_id = store.add_document(doc.title, doc.source, doc.kind, sha, doc.meta, paragraphs)
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
