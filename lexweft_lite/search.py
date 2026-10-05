"""段落の全文検索.

- 3 文字以上の語は全文索引 (trigram, BM25)、2 文字以下の語は部分一致で探す。
- 1 つの問いの中の語はすべて含む段落 (AND) を返す。
- 複数の問い (言い換え・別表記) を渡すと、問いごとの順位を RRF で 1 本にまとめる。
  言い換えを作るのは呼び出し側 (MCP の LLM や利用者) で、アプリは辞書や意味層で順位を変えない。
"""

from __future__ import annotations

import re
from typing import Any

from .store import Store

RRF_K = 60
_SPLIT_RE = re.compile(r"[\s　]+")


def _terms(query: str) -> list[str]:
    return [t for t in _SPLIT_RE.split((query or "").strip()) if t]


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def rank_one(store: Store, query: str, limit: int = 50) -> list[int]:
    """1 つの問いで段落 ID を順に返す."""
    terms = _terms(query)
    if not terms:
        return []
    long_terms = [t for t in terms if len(t) >= 3]
    short_terms = [t for t in terms if len(t) < 3]
    if long_terms:
        sql = "SELECT p.id, p.text FROM paragraphs_fts f JOIN paragraphs p ON p.id = f.rowid WHERE paragraphs_fts MATCH ?"
        params: list[Any] = [" AND ".join(_fts_phrase(t) for t in long_terms)]
        for t in short_terms:
            sql += " AND p.text LIKE ?"
            params.append(f"%{t}%")
        sql += " ORDER BY bm25(paragraphs_fts) LIMIT ?"
        params.append(limit)
        return [int(r["id"]) for r in store.conn.execute(sql, params)]
    sql = "SELECT id, text FROM paragraphs WHERE " + " AND ".join("text LIKE ?" for _ in short_terms)
    rows = store.conn.execute(sql, [f"%{t}%" for t in short_terms]).fetchall()
    # 出現回数が多く、段落が短いものを上に
    scored = sorted(rows, key=lambda r: (-sum(r["text"].count(t) for t in short_terms), len(r["text"])))
    return [int(r["id"]) for r in scored[:limit]]


def search(store: Store, queries: list[str] | str, top_k: int = 10, document_id: int | None = None) -> list[dict[str, Any]]:
    """段落を探す. queries に複数の言い換えを渡すと、問いごとの順位を RRF でまとめる."""
    if isinstance(queries, str):
        queries = [queries]
    queries = [q for q in queries if q and q.strip()]
    scores: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    for q in queries:
        for rank, pid in enumerate(rank_one(store, q, limit=max(50, top_k * 5))):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
            matched.setdefault(pid, []).append(q)
    rows = store.get_paragraphs(list(scores))
    if document_id is not None:
        rows = {k: v for k, v in rows.items() if v["document_id"] == document_id}
    ranked = sorted(rows, key=lambda pid: -scores[pid])[:top_k]
    concepts = _concepts_of(store, ranked)
    out = []
    for pid in ranked:
        r = rows[pid]
        text = r["text"]
        if r["heading"] and text.startswith(r["heading"]):
            text = text[len(r["heading"]):].lstrip("\n")
        out.append({"paragraph_id": pid, "document_id": r["document_id"], "title": r["title"], "heading": r["heading"].lstrip("# "),
                    "text": text, "score": round(scores[pid], 5), "matched": matched[pid], "concepts": concepts.get(pid, [])})
    return out


def _concepts_of(store: Store, paragraph_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """段落に結ばれた概念 (表示用. 順位には使わない)."""
    if not paragraph_ids:
        return {}
    q = ",".join("?" * len(paragraph_ids))
    out: dict[int, list[dict[str, Any]]] = {}
    for r in store.conn.execute(f"SELECT e.paragraph_id, c.id, c.name, c.type FROM evidence e JOIN concepts c ON c.id = e.concept_id WHERE e.paragraph_id IN ({q})",
                                paragraph_ids):
        out.setdefault(int(r["paragraph_id"]), []).append({"id": r["id"], "name": r["name"], "type": r["type"]})
    return out


def search_all(lib, queries: list[str] | str, top_k: int = 10, scope: str | None = None) -> list[dict[str, Any]]:
    """登録したフォルダとフォルダの外の資料をまとめて探す (どれも同じ数え方の点なので、点の順に並べる)."""
    stores = [lib.for_scope(scope)] if scope else lib.stores()
    hits = []
    for st in stores:
        for h in search(st, queries, top_k=top_k):
            hits.append({**h, "scope": st.key, "folder": st.label})
    hits.sort(key=lambda h: -h["score"])
    return hits[:top_k]
