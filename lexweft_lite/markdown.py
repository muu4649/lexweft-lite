"""LLM が読みやすい Markdown を作る.

資料は「書誌の front matter + 見出し + 段落番号つきの本文」にする。段落番号 [¶123] は
意味層の根拠や検索結果と同じ番号なので、LLM はこの番号で根拠を指せる。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .store import Store


def _yaml_value(v: Any) -> str:
    s = str(v).replace("\n", " ")
    return f'"{s}"' if re.search(r"[:#\[\]{},&*!|>'\"%@`]", s) or not s else s


def document_markdown(store: Store, document_id: int, offset: int = 0, limit: int | None = None) -> str:
    doc = store.get_document(document_id)
    if doc is None:
        raise KeyError(f"資料 {document_id} はありません")
    paras = store.paragraphs_of(document_id, offset, limit)
    total = int(store.conn.execute("SELECT COUNT(*) FROM paragraphs WHERE document_id = ?", (document_id,)).fetchone()[0])
    head = ["---", f"id: {doc['id']}", f"title: {_yaml_value(doc['title'])}", f"source: {_yaml_value(doc['source'])}",
            f"kind: {doc['kind']}", f"added: {doc['added_at'][:10]}", f"paragraphs: {total}"]
    for k, v in (doc.get("meta") or {}).items():
        if k in {"title", "id"} or isinstance(v, (dict, list)):
            continue
        head.append(f"{k}: {_yaml_value(v)}")
    if offset or (limit is not None and offset + len(paras) < total):
        head.append(f"range: {offset + 1}-{offset + len(paras)}")
    head += ["---", "", f"# {doc['title']}", ""]
    lines = head
    title = doc["title"].strip()
    # 見出しは節の表から、その節の最初の段落の前に書く (段落の本文には見出しが入っていない)
    starts = {s["first_ordinal"]: s["heading"] for s in store.sections_of(document_id)}
    if paras and not starts:   # 前の版で取り込んだまま (段落に見出しを持っていた) の資料
        prev = None
        for p in paras:
            h = (p["heading"] or "").strip()
            if h and h != prev:
                starts[p["ordinal"]] = h
            prev = h or prev
    for p in paras:
        heading = (starts.get(p["ordinal"]) or "").strip()
        if heading and heading.lstrip("#").strip() != title:   # 資料の題名と同じ見出し (ファイルの先頭の # 題名) は 2 度書かない
            lines += [heading if heading.startswith("#") else f"## {heading}", ""]
        body = p["text"]
        old = (p["heading"] or "").strip()
        if old and body.startswith(old):   # 前の版の段落は、本文の先頭に見出しが付いている
            body = body[len(old):].lstrip("\n")
        lines += [f"[¶{p['id']}] {body.strip()}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _slug(title: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", title).strip("_")
    return s[:60] or "document"


def write_document_file(store: Store, document_id: int, out_dir: Path) -> Path:
    """資料の Markdown をファイルに書く (同じ資料の古いファイルは消す)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{document_id:05d}-*.md"):
        old.unlink()
    doc = store.get_document(document_id)
    path = out_dir / f"{document_id:05d}-{_slug(doc['title'])}.md"
    path.write_text(document_markdown(store, document_id), encoding="utf-8")
    return path


def remove_document_file(document_id: int, out_dir: Path) -> None:
    for old in out_dir.glob(f"{document_id:05d}-*.md"):
        old.unlink()


def layer_markdown(store: Store) -> str:
    """意味層全体を 1 枚の Markdown にする (型ごとの概念、別名、説明、根拠の段落、関係)."""
    from . import layer

    lines = ["# 意味層", ""]
    st = store.stats()
    lines += [f"資料 {st['documents']} 件 / 概念 {st['concepts']} / 関係 {st['relations']} / 根拠 {st['evidence']}", ""]
    for t in layer.list_types(store):
        concepts = layer.list_concepts(store, type_=t["name"])
        if not concepts:
            continue
        lines += [f"## {t['name']}", ""]
        if t["description"]:
            lines += [f"_{t['description']}_", ""]
        for c in concepts:
            detail = layer.get_concept(store, c["id"])
            lines.append(f"### {c['name']}")
            if detail["aliases"]:
                lines.append(f"- 別名: {', '.join(detail['aliases'])}")
            if detail["description"]:
                lines.append(f"- 説明: {detail['description']}")
            if detail["evidence"]:
                refs = ", ".join(f"[¶{e['paragraph_id']}] {e['title']}" for e in detail["evidence"][:12])
                more = f" ほか {len(detail['evidence']) - 12} 件" if len(detail["evidence"]) > 12 else ""
                lines.append(f"- 根拠: {refs}{more}")
            for r in detail["relations"]:
                arrow = f"{r['kind']} → {r['dst']}" if r["direction"] == "out" else f"← {r['kind']} {r['src']}"
                ref = f" [¶{r['paragraph_id']}]" if r.get("paragraph_id") else ""
                lines.append(f"- {arrow}{ref}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
